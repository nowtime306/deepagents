"""RDC configuration history, recipient and delivery boundaries; no external services."""
import hashlib
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from app.configuration_history import begin_context, end_context, list_history, set_actor
from app.errors import APIError
from app.model_routing import fingerprint, resolve
from app.models import CredentialVault, ModelInput, ModelRegistry
from app.notification_channels import dispatch
from app.notifications import notify
from app.repository_runs import same_identity
from app.storage import Database


@pytest.fixture
async def resources(tmp_path):
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
        pytest.skip("Requires an isolated _test database")
    db = Database(url)
    await db.pool.open()
    owner = uuid4()
    await db.execute(
        "INSERT INTO users(id,username,password_hash,role_id,is_admin) VALUES(%s,%s,'unused','admin',true)",
        (owner, str(owner)),
    )
    tokens = begin_context()
    set_actor(owner)
    models = ModelRegistry(db, CredentialVault(tmp_path / "test.key"))
    try:
        yield db, models, {"id": owner, "is_admin": True}
    finally:
        end_context(tokens)
        await db.pool.close()


async def test_history_atomic_cas_default_delete_and_secret_redaction(resources):
    db, models, user = resources
    secret = "RDC_NEVER_DISPLAY_KEY"
    url = "https://example.invalid/private/URL_PATH"
    first = await models.save(ModelInput(name=uuid4().hex, model="fixture", base_url=url, api_key=secret,
                                         is_default=True))
    await db.execute("UPDATE model_configs SET base_url=%s,revision=revision+1 WHERE id=%s",
                     ("https://name:URL_PASSWORD@example.invalid/private/URL_PATH?token=URL_QUERY", first))
    # Multiple implicit default changes share the controlled request correlation ID.
    second = await models.save(ModelInput(name=uuid4().hex, model="fixture", base_url="https://other.invalid",
                                          is_default=True))
    one = await list_history(db, user, "model", first)
    two = await list_history(db, user, "model", second)
    assert one[0]["changes"]["is_default"] == {"before": True, "after": False}
    assert one[0]["change_id"] == two[0]["change_id"]
    assert one[0]["actor_id"] == user["id"]
    encoded = str(one)
    assert all(value not in encoded for value in [secret, "URL_PASSWORD", "URL_PATH", "URL_QUERY", "name:"])
    assert one[-1]["changes"]["api_key_ciphertext"]["credential_change"] == "已设置"
    count = len(one)
    with pytest.raises(APIError):
        await models.save(ModelInput(name="conflict", model="fixture", base_url=url, revision=1), first)
    assert len(await list_history(db, user, "model", first)) == count
    with pytest.raises(RuntimeError):
        async with db.pool.connection() as connection:
            await connection.execute(
                "UPDATE model_configs SET temperature=0.1,revision=revision+1 WHERE id=%s", (first,)
            )
            raise RuntimeError("rollback")
    assert len(await list_history(db, user, "model", first)) == count
    await db.execute("DELETE FROM model_configs WHERE id=%s", (first,))
    assert (await list_history(db, user, "model", first))[0]["operation"] == "DELETE"
    with pytest.raises(APIError):
        await list_history(db, {"id": uuid4(), "is_admin": False}, "model", first)
    private = uuid4()
    await db.execute("INSERT INTO repositories(id,owner_id,name,slug,ref) VALUES(%s,%s,'private','org/repo','main')",
                     (private, user["id"]))
    assert await list_history(db, {"id": uuid4(), "is_admin": True}, "repository", str(private)) == []


def test_route_consent_changes_only_with_recipients_or_data_scope():
    route = {"id": "route", "require_images": False, "require_tools": False, "require_native": False,
             "revision": 1, "name": "before", "failures": 1, "cooldown": 10}
    candidate = {"id": "model", "base_url": "https://receiver.invalid/v1", "revision": 1, "name": "before"}
    digest = fingerprint(route, [candidate])
    assert fingerprint({**route, "revision": 4, "name": "after", "failures": 4},
                       [{**candidate, "revision": 7, "name": "after"}]) == digest
    assert fingerprint(route, [{**candidate, "base_url": "https://other.invalid/v1"}]) != digest
    assert fingerprint({**route, "require_images": True}, [candidate]) != digest
    assert fingerprint(route, [candidate, {**candidate, "id": "new"}]) != digest


async def test_route_selected_receiver_remains_fixed_and_revocation_blocks(resources):
    db, models, user = resources
    primary = await models.save(ModelInput(name=uuid4().hex, model="fixture", base_url="https://first.invalid"))
    alternate = await models.save(ModelInput(name=uuid4().hex, model="fixture", base_url="https://second.invalid"))
    route, run = uuid4(), uuid4()
    await db.execute("INSERT INTO model_routes(id,name,primary_model,candidates,failures,cooldown) "
                     "VALUES(%s,'fixture',%s,%s,3,60)", (route, primary, Jsonb([primary])))
    from app.model_routing import description
    async with db.pool.connection() as connection:
        _, _, digest = await description(connection, primary)
    await db.execute("INSERT INTO model_route_consents(owner_id,route_id,digest) VALUES(%s,%s,%s)",
                     (user["id"], route, digest))
    selected = await resolve(models, user["id"], run, primary, enabled=True)
    await db.execute("UPDATE model_routes SET candidates=%s,revision=revision+1 WHERE id=%s",
                     (Jsonb([alternate, primary]), route))
    assert (await resolve(models, user["id"], run, primary, enabled=True)).id == selected.id
    with pytest.raises(APIError) as missing_consent:
        await resolve(models, user["id"], uuid4(), primary, enabled=True)
    assert missing_consent.value.code == "ROUTE_CONSENT_REQUIRED"
    await db.execute("DELETE FROM model_route_consents WHERE owner_id=%s AND route_id=%s", (user["id"], route))
    with pytest.raises(APIError) as revoked:
        await resolve(models, user["id"], run, primary, enabled=True)
    assert revoked.value.code == "ROUTE_PERMISSION_REVOKED"


async def test_delivery_maintenance_current_key_frozen_body_target_change_pauses(resources, monkeypatch):
    db, models, user = resources
    channel = uuid4()
    target = hashlib.sha256(b"https://receiver.invalid/frozen").hexdigest()
    await db.execute("INSERT INTO notification_channels(id,owner_id,name,kind,endpoint_ciphertext,secret_ciphertext,"
                     "host_hint,target_digest) VALUES(%s,%s,'before','webhook',%s,%s,'receiver.invalid',%s)",
                     (channel, user["id"], models.vault.encrypt("https://receiver.invalid/frozen"),
                      models.vault.encrypt("key1"), target))
    async with db.pool.connection() as connection:
        await notify(connection, user["id"], "冻结消息", "SUCCEEDED", "work", uuid4(), uuid4().hex,
                     [{"id": channel, "revision": 1}])
    await db.execute("UPDATE notification_channels SET name='after',secret_ciphertext=%s,"
                     "revision=revision+1 WHERE id=%s",
                     (models.vault.encrypt("key2"), channel))
    await db.execute("UPDATE notifications SET title='mutated' WHERE owner_id=%s", (user["id"],))
    calls = []
    def transport(channel, notice, vault, attempt):
        calls.append((notice["title"], vault.decrypt(channel["secret_ciphertext"]).get_secret_value()))
        return "SUCCEEDED", None
    monkeypatch.setattr("app.notification_channels.deliver", transport)
    state = SimpleNamespace(db=db, models=models)
    await dispatch(state)
    assert calls == [("冻结消息", "key2")]
    async with db.pool.connection() as connection:
        await notify(connection, user["id"], "旧目标", "SUCCEEDED", "work", uuid4(), uuid4().hex,
                     [{"id": channel, "revision": 2}])
    await db.execute("UPDATE notification_channels SET target_digest='different',revision=revision+1 WHERE id=%s",
                     (channel,))
    await dispatch(state)
    assert len(calls) == 1
    rows = await db.fetch("SELECT status,error_code FROM notification_deliveries "
                          "WHERE channel_id=%s ORDER BY created_at",
                          (channel,))
    assert rows[-1]["status"] == "PAUSED"
    # UNKNOWN is never admitted by the automatic pending-only dispatcher.
    await db.execute("UPDATE notification_deliveries SET status='UNKNOWN' WHERE channel_id=%s", (channel,))
    await dispatch(state)
    assert len(calls) == 1


def test_repository_identity_excludes_credentials_but_preserves_scope():
    source = {"slug": "org/repo", "ref": "main", "subdirectory": "src"}
    assert same_identity({**source, "token_ciphertext": "rotated", "name": "renamed", "revision": 9}, source)
    for key in source:
        assert not same_identity({**source, key: "changed"}, source)

async def test_route_active_run_keeps_original_target_while_new_run_requires_consent(resources):
    db, models, user = resources
    model = await models.save(ModelInput(name=uuid4().hex, model="fixture", base_url="https://original.invalid"))
    route, run = uuid4(), uuid4()
    await db.execute("INSERT INTO model_routes(id,name,primary_model,candidates,failures,cooldown) "
                     "VALUES(%s,'fixed-target',%s,%s,3,60)", (route, model, Jsonb([model])))
    from app.model_routing import description
    from app.resource_runtime import resource_run
    async with db.pool.connection() as connection:
        _, _, digest = await description(connection, model)
    await db.execute("INSERT INTO model_route_consents(owner_id,route_id,digest) VALUES(%s,%s,%s)",
                     (user["id"], route, digest))
    async with resource_run(db, user["id"], "ask", run):
        selected = await resolve(models, user["id"], run, model, enabled=True)
        await db.execute("UPDATE model_configs SET base_url='https://new-target.invalid',"
                         "revision=revision+1 WHERE id=%s",
                         (model,))
        fixed = await resolve(models, user["id"], run, model, enabled=True)
        assert fixed.base_url == selected.base_url == "https://original.invalid"
        assert fixed.revision == selected.revision
    with pytest.raises(APIError) as changed:
        await resolve(models, user["id"], uuid4(), model, enabled=True)
    assert changed.value.code == "ROUTE_CONSENT_REQUIRED"
