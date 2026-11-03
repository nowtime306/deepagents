"""Resource revisions are evidence, while run configuration and revocation stay distinct."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_versions import selected
from app.config import ModelConfig
from app.errors import APIError
from app.model_capabilities import require_tools
from app.models import CredentialVault, ModelRegistry
from app.resource_runtime import _credentials, freeze_model, resource_run
from app.work_worker import WorkWorker


class Records:
    def __init__(self):
        self.snapshots = {}
        self.changed = False

    async def fetch(self, query, params):
        if "run_resource_stages" in query:
            return []
        if "run_resource_snapshots" in query:
            return [self.snapshots[params]] if params in self.snapshots else []
        return []

    async def execute(self, query, params):
        if "INSERT INTO run_resource_snapshots" in query:
            self.snapshots.setdefault(params[:6], {
                "revision": params[6], "snapshot": params[7].obj, "execution_config": params[8],
                "credential_scope": params[9],
            })
        elif "credential_changed=true" in query:
            self.changed = True


def model(revision=1, **changes):
    return ModelConfig(id="test", name="model", model="example", base_url="https://old.example/v1",
                       revision=revision, api_key="old-secret", **changes)


async def test_first_use_and_nested_members_share_snapshot_but_new_run_uses_latest(tmp_path):
    database, vault, owner = Records(), CredentialVault(tmp_path / "key"), uuid4()
    async with resource_run(database, owner, "ask", uuid4()):
        first = await freeze_model(model(2), vault)  # queue-time revision 1 does not participate
        current = first.model_copy(update={"revision": 3, "temperature": 1.5, "base_url": "https://new.example"})
        async with resource_run(database, owner, "workflow", uuid4()):
            nested = await freeze_model(current, vault)
        assert nested == first
        assert nested.api_key.get_secret_value() == "old-secret"
        other = await freeze_model(first.model_copy(update={"id": "other"}), vault)
        assert other.id == "other" and len(database.snapshots) == 2
    async with resource_run(database, owner, "ask", uuid4()):
        assert (await freeze_model(current, vault)).revision == 3
    assert "old-secret" not in repr(database.snapshots)


async def test_recovery_uses_current_key_only_for_same_destination(tmp_path):
    database, vault, owner, run = Records(), CredentialVault(tmp_path / "key"), uuid4(), uuid4()
    async with resource_run(database, owner, "work", run):
        await freeze_model(model(), vault)
    _credentials.clear()  # process restart cannot retrieve old keys
    rotated = model(2).model_copy(update={"api_key": model().api_key.__class__("new-secret"), "temperature": 1.2})
    async with resource_run(database, owner, "work", run):
        recovered = await freeze_model(rotated, vault)
        assert recovered.revision == 1 and recovered.temperature is None
        assert recovered.api_key.get_secret_value() == "new-secret" and database.changed
    _credentials.clear()
    async with resource_run(database, owner, "work", run):
        with pytest.raises(APIError) as error:
            await freeze_model(rotated.model_copy(update={"base_url": "https://another.example"}), vault)
        assert error.value.code == "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED"


async def test_published_agent_model_revision_change_is_accepted_but_incompatibility_is_rejected():
    class Database:
        async def fetch(self, *_):
            return [{"snapshot": {"model_id": "test", "model_revision": 1}, "id": uuid4(), "version": 1}]

    class Models:
        config = model(2)

        async def get(self, _):
            return self.config

    class Capabilities:
        async def freeze(self, _, value):
            return value

    models = Models()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db=Database(), models=models, capabilities=Capabilities(),
    )))
    assert (await selected(request, uuid4(), uuid4(), uuid4()))["model_revision"] == 1
    models.config = models.config.model_copy(update={"kind": "embedding"})
    with pytest.raises(APIError) as error:
        await selected(request, uuid4(), uuid4(), uuid4())
    assert error.value.code == "AGENT_MODEL_UNSUPPORTED"


async def test_registry_still_checks_live_disablement_after_freeze(tmp_path):
    vault = CredentialVault(tmp_path / "key")

    class Database(Records):
        enabled = True

        async def fetch(self, query, params):
            if "FROM model_configs" in query:
                config = model().model_dump(exclude={"api_key"})
                return [{**config, "enabled": self.enabled, "api_key_ciphertext": vault.encrypt("secret")}]
            return await super().fetch(query, params)

    database = Database()
    registry = ModelRegistry(database, vault)
    async with resource_run(database, uuid4(), "ask", uuid4()):
        await registry.get("test")
        database.enabled = False
        with pytest.raises(APIError) as error:
            await registry.get("test")
        assert error.value.code == "MODEL_NOT_FOUND"


async def test_work_recovery_target_change_pauses_without_replaying_or_marking_unknown(monkeypatch):
    class Database:
        statements = []

        async def execute(self, query, params):
            self.statements.append((query, params))

        async def fetch(self, *_):
            return []  # PAUSED is not a terminal state

    async def execute(_):
        raise APIError(409, "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED", "恢复目标变化")

    async def emit(*_):
        pass

    async def heartbeat(_):
        await asyncio.Event().wait()

    database = Database()
    worker = WorkWorker(database, execute)
    monkeypatch.setattr(worker, "emit", emit)
    monkeypatch.setattr(worker, "heartbeat", heartbeat)
    await worker.perform({"id": uuid4(), "owner_id": uuid4(), "lease_token": uuid4()})
    assert len(database.statements) == 1
    query, params = database.statements[0]
    assert "status='PAUSED'" in query and "lease_token=NULL" in query
    assert params[0] == "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED"


@pytest.mark.parametrize("declaration,code", [(None, "MODEL_TOOLS_UNVERIFIED"), (False, "MODEL_TOOLS_UNSUPPORTED")])
def test_unknown_and_negative_tools_capabilities_are_not_assumed_supported(declaration, code):
    with pytest.raises(APIError) as error:
        require_tools(model(supports_tools=declaration))
    assert error.value.code == code
    require_tools(model(supports_tools=True))
