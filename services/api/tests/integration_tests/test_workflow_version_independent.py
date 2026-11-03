"""Independent version lifecycle regressions, prepared before review execution gate."""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client


def payload(name, operation, offset=0):
    return {
        "name": name,
        "model_id": "test",
        "instructions": "configuration:" + operation,
        "definition": {
            "nodes": [
                {"id": "start", "kind": "start"},
                {
                    "id": "action",
                    "kind": "transform",
                    "parameters": {"operation": operation},
                },
                {"id": "end", "kind": "end", "input": "action"},
            ],
            "edges": [
                {"source": "start", "target": "action"},
                {"source": "action", "target": "end"},
            ],
        },
        "layout": {
            "start": {"x": 80 + offset, "y": 100},
            "action": {"x": 380 + offset, "y": 100},
            "end": {"x": 680 + offset, "y": 100},
        },
    }


def seed(c):
    name = "independent-version-" + uuid4().hex
    one = payload(name, "upper")
    response = c.post("/api/v1/workflows", headers=ORIGIN, json=one)
    assert response.status_code == 201, response.text
    prefix = "/api/v1/workflows/" + response.json()["id"]
    first = c.post(prefix + "/test-runs", headers=ORIGIN, json={"text": "MiXeD"})
    assert first.status_code == 200 and first.json()["output"] == "MIXED"
    assert (
        c.post(prefix + "/publish", headers=ORIGIN, json={"revision": 1}).json()[
            "version"
        ]
        == 1
    )
    two = payload(name + "-second", "lower", 200)
    assert c.put(prefix, headers=ORIGIN, json={**two, "revision": 1}).status_code == 200
    assert (
        c.post(prefix + "/test-runs", headers=ORIGIN, json={"text": "MiXeD"}).json()[
            "output"
        ]
        == "mixed"
    )
    assert (
        c.post(prefix + "/publish", headers=ORIGIN, json={"revision": 2}).json()[
            "version"
        ]
        == 2
    )
    return prefix, one, two, first.json()["id"]


def immutable(row):
    return {
        key: row[key]
        for key in [
            "name",
            "instructions",
            "model_id",
            "model_revision",
            "draft_revision",
            "definition",
            "layout",
            "digest",
            "resource_pins",
        ]
    }


def test_current_rollback_and_repeat_publish_do_not_rewrite_history_or_explicit_run(
    client,
):
    c, app = client
    prefix, one, two, run = seed(c)
    before = [immutable(c.get(prefix + f"/versions/{v}").json()) for v in [1, 2]]
    historical = c.get(prefix + "/runs?run_id=" + run).json()["items"][0]
    draft = c.get(prefix).json()
    assert draft["current_published_version"] == 2
    selected = c.post(
        prefix + "/versions/1/set-current",
        headers=ORIGIN,
        json={"release_revision": draft["release_revision"]},
    )
    assert (
        selected.status_code == 200
        and selected.json()["current_published_version"] == 1
    )
    current = c.get(prefix).json()
    assert (
        current["revision"] == draft["revision"]
        and current["definition"] == draft["definition"]
    )
    assert (
        c.post(prefix + "/publish", headers=ORIGIN, json={"revision": 2}).json()[
            "created"
        ]
        is False
    )
    assert c.get(prefix).json()["current_published_version"] == 1
    explicit = c.post(
        prefix + "/versions/2/runs", headers=ORIGIN, json={"text": "MiXeD"}
    )
    assert explicit.status_code == 200 and explicit.json()["output"] == "mixed"
    assert c.get(prefix + "/runs?run_id=" + run).json()["items"][0] == historical
    assert [
        immutable(c.get(prefix + f"/versions/{v}").json()) for v in [1, 2]
    ] == before
    assert c.get(prefix + "/versions/2").json()["active"] is True
    conflict = c.post(
        prefix + "/versions/2/set-current",
        headers=ORIGIN,
        json={"release_revision": draft["release_revision"]},
    )
    assert (
        conflict.status_code == 409
        and c.get(prefix).json()["current_published_version"] == 1
    )


def test_restore_atomically_retains_complete_persisted_draft_and_forces_retest(client):
    c, _ = client
    prefix, one, two, _ = seed(c)
    before = c.get(prefix).json()
    response = c.post(
        prefix + "/versions/1/restore",
        headers=ORIGIN,
        json={"revision": before["revision"]},
    )
    assert response.status_code == 200, response.text
    returned = response.json()
    backup = c.get("/api/v1/workflows/" + returned["draft_backup_id"]).json()
    restored = c.get(prefix).json()
    for key in ["instructions", "model_id", "definition", "layout"]:
        assert backup[key] == before[key]
    assert (
        backup["tested_revision"] is None
        and backup["current_published_version"] is None
        and backup["revision"] == 1
    )
    assert (
        c.get("/api/v1/workflows/" + backup["id"] + "/versions").json()["items"] == []
    )
    assert (
        restored["revision"] == before["revision"] + 1
        and restored["tested_revision"] is None
        and restored["tested_pins"] is None
    )
    assert (
        restored["current_published_version"] == 2
        and restored["release_revision"] == before["release_revision"]
    )
    assert (
        restored["instructions"] == one["instructions"]
        and restored["layout"] == one["layout"]
    )
    assert (
        c.post(
            prefix + "/publish", headers=ORIGIN, json={"revision": restored["revision"]}
        ).status_code
        == 409
    )
    c.post(
        prefix + "/test-runs", headers=ORIGIN, json={"text": "MiXeD"}
    ).raise_for_status()
    published = c.post(
        prefix + "/publish", headers=ORIGIN, json={"revision": restored["revision"]}
    )
    assert published.status_code == 201 and published.json()["version"] == 3


def test_failure_after_backup_insert_rolls_back_backup_and_draft(client, monkeypatch):
    from app import workflow_versions

    c, _ = client
    prefix, _, _, _ = seed(c)
    before = c.get(prefix).json()
    ids = {r["id"] for r in c.get("/api/v1/workflows").json()["items"]}
    original = workflow_versions.insert_draft

    async def failed_after_insert(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("independent-rollback-after-insert")

    monkeypatch.setattr(workflow_versions, "insert_draft", failed_after_insert)
    with pytest.raises(RuntimeError, match="independent-rollback-after-insert"):
        c.post(
            prefix + "/versions/1/restore",
            headers=ORIGIN,
            json={"revision": before["revision"]},
        )
    assert c.get(prefix).json() == before
    assert {r["id"] for r in c.get("/api/v1/workflows").json()["items"]} == ids


def test_inactive_history_copy_is_independent_and_never_clones_runs(client):
    c, _ = client
    prefix, one, two, _ = seed(c)
    assert (
        c.put(
            prefix + "/versions/1/state", headers=ORIGIN, json={"active": False}
        ).status_code
        == 200
    )
    source = c.get(prefix + "/versions/1").json()
    assert source["active"] is False
    copied = c.post(
        prefix + "/versions/1/copy",
        headers=ORIGIN,
        json={"name": "copy-" + uuid4().hex},
    )
    assert copied.status_code == 201, copied.text
    dest = "/api/v1/workflows/" + copied.json()["id"]
    clone = c.get(dest).json()
    assert (
        clone["definition"] == source["definition"]
        and clone["layout"] == source["layout"]
    )
    assert (
        clone["current_published_version"] is None and clone["tested_revision"] is None
    )
    assert (
        c.get(dest + "/runs").json()["items"] == []
        and c.get(dest + "/versions").json()["items"] == []
    )
    changed = deepcopy(one)
    changed["name"] = clone["name"]
    changed["revision"] = 1
    changed["definition"]["nodes"][1]["parameters"]["operation"] = "trim"
    assert c.put(dest, headers=ORIGIN, json=changed).status_code == 200
    assert immutable(c.get(prefix + "/versions/1").json()) == immutable(source)
    assert c.get(prefix).json()["instructions"] == two["instructions"]


def test_legacy_unknown_layout_and_unselected_pointer_are_not_guessed(client):
    c, app = client
    prefix, _, two, _ = seed(c)
    identifier = prefix.rsplit("/", 1)[1]
    c.portal.call(
        app.state.db.execute,
        "UPDATE workflow_versions SET layout='{}' WHERE workflow_id=%s",
        (identifier,),
    )
    c.portal.call(
        app.state.db.execute,
        "UPDATE workflows SET current_published_version=NULL WHERE id=%s",
        (identifier,),
    )
    assert c.get(prefix).json()["current_published_version"] is None
    old = c.get(prefix + "/versions/1").json()
    assert old["layout"] == {} and old["layout_known"] is False
    assert c.get(prefix).json()["layout"] == two["layout"]


def test_under_read_role_all_version_writes_are_forbidden(client):
    from psycopg.types.json import Jsonb

    c, app = client
    prefix, _, _, _ = seed(c)
    owner = c.get("/api/v1/auth/me").json()["id"]
    role = "independent-version-reader-" + uuid4().hex
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)",
        (role, role, Jsonb(["workflows.read"])),
    )
    c.portal.call(
        app.state.db.execute,
        "UPDATE users SET role_id=%s,is_admin=false WHERE id=%s",
        (role, owner),
    )
    assert c.get(prefix + "/versions/1").status_code == 200
    for suffix, body in [
        ("restore", {"revision": 2}),
        ("copy", {"name": "viewer-copy"}),
        ("set-current", {"release_revision": 3}),
    ]:
        assert (
            c.post(
                prefix + "/versions/1/" + suffix, headers=ORIGIN, json=body
            ).status_code
            == 403
        )


def test_legacy_raw_version_remains_immutable_but_canvas_and_restored_workflow_are_canonical(
    client,
):
    from psycopg.types.json import Jsonb

    c, app = client
    prefix, _, _, _ = seed(c)
    identifier = prefix.rsplit("/", 1)[1]
    legacy = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "action", "kind": "transform", "parameters": {"operation": "upper"}},
            {"id": "end", "kind": "end", "input": "action"},
        ],
        "edges": [
            {"source": "start", "target": "action"},
            {"source": "action", "target": "end"},
        ],
    }
    c.portal.call(
        app.state.db.execute,
        "UPDATE workflow_versions SET definition=%s WHERE workflow_id=%s AND version=1",
        (Jsonb(legacy), identifier),
    )
    detail = c.get(prefix + "/versions/1").json()
    assert detail["definition"] == legacy and detail["schema_supported"] is True
    assert all(
        isinstance(n["parameters"], dict) and "input" in n
        for n in detail["canvas_definition"]["nodes"]
    )
    draft = c.get(prefix).json()
    response = c.post(
        prefix + "/versions/1/restore",
        headers=ORIGIN,
        json={"revision": draft["revision"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workflow"]["revision"] == body["revision"]
    assert (
        body["workflow"]["id"] == identifier and body["draft_backup_id"] != identifier
    )
    assert all(
        isinstance(n["parameters"], dict)
        for n in body["workflow"]["definition"]["nodes"]
    )
    assert c.get(prefix + "/versions/1").json()["definition"] == legacy
