"""Immutable history, pointer CAS, repairable drafts and atomic preservation."""
from uuid import uuid4

from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client
from test_workflow_composition import definition


def seeded(connection):
    first = {"name": "version-one-" + uuid4().hex, "model_id": "test", "instructions": "first instruction",
             "definition": definition(operation="upper"), "layout": {"action": {"x": 10, "y": 20}}}
    created = connection.post("/api/v1/workflows", headers=ORIGIN, json=first)
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    prefix = "/api/v1/workflows/" + identifier
    assert connection.get(prefix).json()["current_published_version"] is None
    assert connection.post(prefix + "/test-runs", headers=ORIGIN, json={"text": "Hello"}).status_code == 200
    published = connection.post(prefix + "/publish", headers=ORIGIN, json={"revision": 1})
    assert published.status_code == 201 and published.json()["current_published_version"] == 1
    second = {**first, "name": "version-two-" + uuid4().hex, "instructions": "second instruction", "revision": 1,
              "definition": definition(operation="lower"), "layout": {"action": {"x": 500, "y": 200}}}
    assert connection.put(prefix, headers=ORIGIN, json=second).status_code == 200
    assert connection.post(prefix + "/test-runs", headers=ORIGIN, json={"text": "Hello"}).status_code == 200
    assert connection.post(prefix + "/publish", headers=ORIGIN, json={"revision": 2}).json()["version"] == 2
    return identifier, prefix, first, second


def counts(connection, app):
    return connection.portal.call(app.state.db.fetch,
        "SELECT (SELECT count(*) FROM workflows) AS drafts, (SELECT count(*) FROM workflow_runs) AS runs, "
        "(SELECT count(*) FROM model_usage) AS usage, (SELECT count(*) FROM api_credentials) AS credentials")


def test_preview_layout_and_current_selection_do_not_change_draft_or_explicit_version_execution(client):
    connection, app = client
    identifier, prefix, first, second = seeded(connection)
    before = connection.get(prefix).json()
    source = connection.portal.call(app.state.db.fetch,
        "SELECT * FROM workflow_versions WHERE workflow_id=%s ORDER BY version", (identifier,))
    one = connection.get(prefix + "/versions/1").json()
    two = connection.get(prefix + "/versions/2").json()
    assert one["name"] == first["name"] and one["instructions"] == first["instructions"]
    assert one["layout"] == first["layout"] and one["layout_known"] and one["schema_supported"]
    assert two["name"] == second["name"] and two["layout"] == second["layout"] and two["is_current"]
    original_counts = counts(connection, app)
    current = connection.post(prefix + "/versions/1/set-current", headers=ORIGIN,
                              json={"release_revision": before["release_revision"]})
    assert current.status_code == 200 and current.json()["current_published_version"] == 1
    after = connection.get(prefix).json()
    pointer_fields = {"current_published_version", "release_revision"}
    assert {key: value for key, value in before.items() if key not in pointer_fields} == {
        key: value for key, value in after.items() if key not in pointer_fields}
    assert counts(connection, app) == original_counts
    assert connection.portal.call(app.state.db.fetch,
        "SELECT * FROM workflow_versions WHERE workflow_id=%s ORDER BY version", (identifier,)) == source
    for number, expected in [(2, "hello"), (1, "HELLO")]:
        executed = connection.post(prefix + f"/versions/{number}/runs", headers=ORIGIN, json={"text": "Hello"})
        assert executed.status_code == 200 and executed.json()["output"] == expected
    repeated = connection.post(prefix + "/publish", headers=ORIGIN, json={"revision": 2}).json()
    assert repeated["version"] == 2 and not repeated["created"] and repeated["current_published_version"] == 1


def test_restore_cas_preserves_original_draft_in_independent_backup_and_clears_testing(client):
    connection, app = client
    identifier, prefix, first, second = seeded(connection)
    history = connection.portal.call(app.state.db.fetch,
        "SELECT * FROM workflow_versions WHERE workflow_id=%s ORDER BY version", (identifier,))
    runs = connection.get(prefix + "/runs").json()
    original = counts(connection, app)
    conflict = connection.post(prefix + "/versions/1/restore", headers=ORIGIN, json={"revision": 1})
    assert conflict.status_code == 409 and counts(connection, app) == original
    response = connection.post(prefix + "/versions/1/restore", headers=ORIGIN, json={"revision": 2})
    assert response.status_code == 200
    restored = connection.get(prefix).json()
    assert response.json()["workflow"] == restored
    assert restored["revision"] == 3 and restored["tested_revision"] is None and restored["tested_pins"] is None
    assert restored["current_published_version"] == 2 and restored["release_revision"] == 3
    assert restored["name"] == first["name"] and restored["layout"] == first["layout"]
    backup = connection.get("/api/v1/workflows/" + response.json()["draft_backup_id"]).json()
    assert backup["definition"]["nodes"][1]["parameters"] == second["definition"]["nodes"][1]["parameters"]
    assert backup["instructions"] == second["instructions"] and backup["layout"] == second["layout"]
    assert backup["revision"] == 1 and backup["tested_revision"] is None and backup["current_published_version"] is None
    assert connection.get(prefix + "/runs").json() == runs
    assert connection.portal.call(app.state.db.fetch,
        "SELECT * FROM workflow_versions WHERE workflow_id=%s ORDER BY version", (identifier,)) == history
    assert connection.post(prefix + "/publish", headers=ORIGIN, json={"revision": 3}).status_code == 409


def test_copy_is_independent_untested_unpublished_and_inactive_history_is_editable(client):
    connection, app = client
    _, prefix, first, _ = seeded(connection)
    assert connection.put(prefix + "/versions/1/state", headers=ORIGIN, json={"active": False}).status_code == 200
    before = counts(connection, app)[0]
    response = connection.post(prefix + "/versions/1/copy", headers=ORIGIN, json={"name": "copy-" + uuid4().hex})
    assert response.status_code == 201
    copy_prefix = "/api/v1/workflows/" + response.json()["id"]
    copied = connection.get(copy_prefix).json()
    assert copied["tested_revision"] is None and copied["current_published_version"] is None
    assert copied["layout"] == first["layout"] and copied["instructions"] == first["instructions"]
    assert connection.get(copy_prefix + "/runs").json()["items"] == []
    assert connection.get(copy_prefix + "/versions").json()["items"] == []
    after = counts(connection, app)[0]
    assert after == {**before, "drafts": before["drafts"] + 1}
    assert connection.post(prefix + "/versions/1/restore", headers=ORIGIN, json={"revision": 2}).status_code == 200


def test_current_cas_and_downline_are_explicit_and_do_not_silently_fallback(client):
    connection, _ = client
    _, prefix, _, _ = seeded(connection)
    response = connection.post(prefix + "/versions/1/set-current", headers=ORIGIN, json={"release_revision": 3})
    assert response.status_code == 200 and response.json()["release_revision"] == 4
    assert connection.post(prefix + "/versions/2/set-current", headers=ORIGIN,
                           json={"release_revision": 3}).status_code == 409
    down = connection.put(prefix + "/versions/1/state", headers=ORIGIN, json={"active": False}).json()
    assert down["current_published_version"] is None and down["release_revision"] == 5
    up = connection.put(prefix + "/versions/1/state", headers=ORIGIN, json={"active": True}).json()
    assert up["current_published_version"] is None and up["release_revision"] == 5
    assert connection.post(prefix + "/versions/1/set-current", headers=ORIGIN,
                           json={"release_revision": 5}).status_code == 200


def test_changed_resources_cannot_become_current_but_history_can_be_copied_for_repair(client):
    connection, app = client
    _, prefix, _, _ = seeded(connection)
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET revision=revision+1 WHERE id='test'")
    assert connection.post(prefix + "/versions/1/set-current", headers=ORIGIN,
                           json={"release_revision": 3}).status_code == 409
    assert connection.get(prefix).json()["current_published_version"] == 2
    assert connection.post(prefix + "/versions/1/copy", headers=ORIGIN,
                           json={"name": "repair-" + uuid4().hex}).status_code == 201
    assert connection.post(prefix + "/versions/1/restore", headers=ORIGIN, json={"revision": 2}).status_code == 200
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id='test'")
    response = connection.post(prefix + "/versions/1/copy", headers=ORIGIN,
                               json={"name": "disabled-repair-" + uuid4().hex})
    assert response.status_code == 201
    assert connection.get("/api/v1/workflows/" + response.json()["id"]).json()["model_id"] == "test"


def test_restore_name_conflict_rolls_back_backup_and_unsupported_schema_cannot_mutate(client):
    connection, app = client
    identifier, prefix, first, _ = seeded(connection)
    assert connection.post("/api/v1/workflows", headers=ORIGIN, json=first).status_code == 201
    before = counts(connection, app)
    assert connection.post(prefix + "/versions/1/restore", headers=ORIGIN, json={"revision": 2}).status_code == 409
    assert counts(connection, app) == before and connection.get(prefix).json()["revision"] == 2
    unsupported = {**first["definition"], "schema_version": 2}
    connection.portal.call(app.state.db.execute,
        "UPDATE workflow_versions SET definition=%s WHERE workflow_id=%s AND version=1",
        (Jsonb(unsupported), identifier))
    preview = connection.get(prefix + "/versions/1").json()
    assert not preview["schema_supported"] and preview["canvas_definition"] is None
    assert preview["definition"] == unsupported
    old = next(item for item in connection.get(prefix + "/versions").json()["items"] if item["version"] == 1)
    assert not old["schema_supported"] and old["canvas_definition"] is None
    assert old["definition"] == unsupported
    for action, body in [("restore", {"revision": 2}), ("copy", {"name": "unsupported-copy"}),
                         ("set-current", {"release_revision": 3})]:
        response = connection.post(prefix + "/versions/1/" + action, headers=ORIGIN, json=body)
        assert response.status_code == 422, response.text
    assert counts(connection, app) == before


def test_legacy_layout_is_not_replaced_with_current_draft_and_active_cannot_be_set_current(client):
    connection, app = client
    identifier, prefix, _, second = seeded(connection)
    connection.portal.call(app.state.db.execute,
        "UPDATE workflow_versions SET layout='{}'::jsonb WHERE workflow_id=%s AND version=1", (identifier,))
    source = connection.get(prefix + "/versions/1").json()
    assert source["layout"] == {} and not source["layout_known"]
    assert connection.get(prefix).json()["layout"] == second["layout"]
    connection.put(prefix + "/versions/1/state", headers=ORIGIN, json={"active": False})
    assert connection.post(prefix + "/versions/1/set-current", headers=ORIGIN,
                           json={"release_revision": 3}).status_code == 409


def test_only_one_concurrent_current_selection_matches_release_revision(client):
    from concurrent.futures import ThreadPoolExecutor

    connection, _ = client
    _, prefix, _, _ = seeded(connection)

    def select(version):
        return connection.post(prefix + f"/versions/{version}/set-current", headers=ORIGIN,
                               json={"release_revision": 3}).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(select, [1, 2]))
    assert sorted(statuses) == [200, 409]
    assert connection.get(prefix).json()["release_revision"] == 4


def test_owner_and_read_only_permissions_protect_history_writes(client):
    from test_api import DATABASE, add_user, run_async

    connection, app = client
    _, prefix, _, _ = seeded(connection)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    role = "version-reader-" + uuid4().hex
    connection.portal.call(app.state.db.execute,
        "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)", (role, role, Jsonb(["workflows.read"])))
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id=%s WHERE id=%s", (role, owner))
    assert connection.get(prefix + "/versions/1").status_code == 200
    for action, body in [("copy", {"name": "readonly-copy"}), ("restore", {"revision": 2}),
                         ("set-current", {"release_revision": 3})]:
        assert connection.post(prefix + "/versions/1/" + action, headers=ORIGIN, json=body).status_code == 403
    username = "version-other-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    assert connection.post("/api/v1/auth/login", headers=ORIGIN,
        json={"username": username, "password": "integration-password"}).status_code == 200
    assert connection.get(prefix).status_code == 404
    assert connection.get(prefix + "/versions/1").status_code == 404
    for action, body in [("copy", {"name": "foreign-copy"}), ("restore", {"revision": 2}),
                         ("set-current", {"release_revision": 3})]:
        assert connection.post(prefix + "/versions/1/" + action, headers=ORIGIN, json=body).status_code == 404


def test_workflow_deletion_with_a_current_pointer_keeps_existing_delete_contract(client):
    connection, _ = client
    _, prefix, _, _ = seeded(connection)
    deleted = connection.delete(prefix, headers=ORIGIN)
    assert deleted.status_code == 200, deleted.text
    assert connection.get(prefix).status_code == 404


def test_version_switch_never_retargets_api_keys_subflows_or_granted_ask_versions(client):
    import json

    from test_ask_workflows import tool_context
    from test_external_workflows import wait_run
    from test_workflow_composition import create, run

    connection, app = client
    _, prefix, _, _ = seeded(connection)
    version = next(row["id"] for row in connection.get(prefix + "/versions").json()["items"] if row["version"] == 2)
    credential = connection.post("/api/v1/api-keys", headers=ORIGIN,
        json={"name": "固定v2", "version_id": version}).json()
    switched = connection.post(prefix + "/versions/1/set-current", headers=ORIGIN, json={"release_revision": 3})
    assert switched.status_code == 200
    headers = {"Authorization": "Bearer " + credential["key"], "Idempotency-Key": uuid4().hex}
    admitted = connection.post("/api/public/v1/workflow-runs", headers=headers, json={"text": "Hello"})
    assert admitted.status_code == 202
    assert wait_run(connection, admitted.json()["id"], headers)["output"] == "hello"
    key_row = connection.portal.call(app.state.db.fetch,
        "SELECT version_id FROM api_credentials WHERE id=%s", (credential["id"],))[0]
    assert str(key_row["version_id"]) == version
    parent = create(connection, definition("subworkflow", version_id=version))
    assert run(connection, parent, "Hello").json()["output"] == "hello"
    _, _, tool = tool_context(connection, app, version)
    result = json.loads(connection.portal.call(tool.ainvoke, {"version_id": version, "text": "Hello"}))
    assert result["output"] == "hello"
    assert connection.get(prefix).json()["current_published_version"] == 1


def test_history_management_never_invokes_models_or_tools(client):
    connection, app = client
    _, prefix, _, _ = seeded(connection)

    def forbidden(_):
        raise AssertionError("Version management must not invoke a model")

    app.state.runtime.model_factory = forbidden
    before = counts(connection, app)[0]
    assert connection.get(prefix + "/versions/1").status_code == 200
    assert connection.post(prefix + "/versions/1/set-current", headers=ORIGIN,
                           json={"release_revision": 3}).status_code == 200
    assert connection.post(prefix + "/versions/1/copy", headers=ORIGIN,
                           json={"name": "no-model-copy-" + uuid4().hex}).status_code == 201
    assert connection.post(prefix + "/versions/1/restore", headers=ORIGIN, json={"revision": 2}).status_code == 200
    after = counts(connection, app)[0]
    assert after["runs"] == before["runs"] and after["usage"] == before["usage"]
    assert after["credentials"] == before["credentials"]

def test_025_bare_history_is_renderable_without_rewriting_immutable_source(client):
    connection, app = client
    identifier, prefix, _, _ = seeded(connection)
    # Exact bare definition installed for legacy rows by migration 025.
    bare = {"nodes": [{"id": "start", "kind": "start"}, {"id": "llm", "kind": "llm"},
                      {"id": "end", "kind": "end", "input": "llm"}],
            "edges": [{"source": "start", "target": "llm"}, {"source": "llm", "target": "end"}]}
    connection.portal.call(app.state.db.execute,
        "UPDATE workflow_versions SET definition=%s,layout='{}'::jsonb WHERE workflow_id=%s AND version=1",
        (Jsonb(bare), identifier))
    history = connection.portal.call(app.state.db.fetch,
        "SELECT * FROM workflow_versions WHERE workflow_id=%s ORDER BY version", (identifier,))

    def forbidden(_):
        raise AssertionError("Legacy preview and draft repair must never invoke models")

    app.state.runtime.model_factory = forbidden
    before = counts(connection, app)[0]
    preview = connection.get(prefix + "/versions/1").json()
    assert preview["definition"] == bare and preview["schema_supported"]
    assert preview["canvas_definition"]["nodes"][1]["parameters"] == {}
    assert preview["canvas_definition"]["nodes"][1]["input"] == "input"
    listed = connection.get(prefix + "/versions").json()["items"]
    old = next(item for item in listed if item["version"] == 1)
    assert old["definition"] == bare and old["canvas_definition"] == preview["canvas_definition"]
    assert old["schema_supported"]
    restored = connection.post(prefix + "/versions/1/restore", headers=ORIGIN, json={"revision": 2})
    assert restored.status_code == 200, restored.text
    draft = connection.get(prefix).json()
    assert restored.json()["workflow"] == draft
    assert draft["definition"] == preview["canvas_definition"]
    assert draft["tested_revision"] is None and draft["tested_pins"] is None
    assert draft["current_published_version"] == 2 and draft["release_revision"] == 3
    copied = connection.post(prefix + "/versions/1/copy", headers=ORIGIN,
                             json={"name": "bare-copy-" + uuid4().hex})
    assert copied.status_code == 201, copied.text
    copy = connection.get("/api/v1/workflows/" + copied.json()["id"]).json()
    assert copy["definition"] == preview["canvas_definition"]
    assert connection.portal.call(app.state.db.fetch,
        "SELECT * FROM workflow_versions WHERE workflow_id=%s ORDER BY version", (identifier,)) == history
    after = counts(connection, app)[0]
    assert after["runs"] == before["runs"] and after["usage"] == before["usage"]
