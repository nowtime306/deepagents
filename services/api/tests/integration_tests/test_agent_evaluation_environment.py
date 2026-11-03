"""Isolated-DB HTTP identity proof; no evaluator execution, vendor requests or browser access."""

import json
import os
import subprocess
from copy import deepcopy
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_api import DATABASE, ORIGIN, add_user
from test_api import client as client

ENDPOINT = "/api/v1/agent-evaluations/environment"


@pytest.fixture
def evaluation_environment(client, tmp_path):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    role = "evaluation-reader-" + uuid4().hex
    connection.portal.call(app.state.db.execute,
        "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)",
        (role, role, Jsonb(["evaluation.read"])))
    connection.portal.call(app.state.db.execute,
        "UPDATE users SET role_id=%s,is_admin=false WHERE id=%s", (role, owner))
    directory = tmp_path / "environment_test"
    directory.mkdir()
    marker = {
        "environment_id": str(uuid4()), "owner_id": owner,
        "database_name": connection.portal.call(app.state.db.fetch, "SELECT current_database() AS name")[0]["name"],
        "platform_commit": "a" * 40,
        "sandbox_namespace": str(connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_sandbox_namespace")[0]["id"]),
        "fixture_profile": {"id": "a20-reviewed", "version": 1},
    }
    original = (app.state.settings.data_dir, app.state.settings.evaluation_instance_id,
                app.state.settings.evaluation_platform_commit)
    app.state.settings.data_dir = directory
    app.state.settings.evaluation_instance_id = marker["environment_id"]
    app.state.settings.evaluation_platform_commit = marker["platform_commit"]
    (directory / "evaluation-instance.json").write_text(json.dumps(marker), encoding="utf-8")
    try:
        yield connection, app, marker, directory, role
    finally:
        (app.state.settings.data_dir, app.state.settings.evaluation_instance_id,
         app.state.settings.evaluation_platform_commit) = original


def test_environment_readonly_identity_and_no_sensitive_fields(evaluation_environment):
    connection, _, marker, directory, _ = evaluation_environment
    result = connection.get(ENDPOINT)
    assert result.status_code == 200, result.text
    body = result.json()
    assert set(body) == {"schema_version", "environment_id", "owner_id", "platform_commit", "database_is_test",
                         "schema_migrations", "sandbox_namespace", "permissions", "fixture_profile"}
    assert body["schema_version"] == 1 and body["database_is_test"] is True and body["schema_migrations"]
    for field in ("environment_id", "owner_id", "platform_commit", "sandbox_namespace", "fixture_profile"):
        assert body[field] == marker[field]
    assert body["permissions"] == ["evaluation.read"]
    assert all(value not in result.text for value in
               (str(directory), marker["database_name"], "postgresql://", "test-only", "ciphertext", "model.key"))


@pytest.mark.parametrize("missing", ["evaluation_instance_id", "evaluation_platform_commit"])
def test_environment_disabled_by_default_even_with_valid_marker(evaluation_environment, missing):
    connection, app, _, _, _ = evaluation_environment
    setattr(app.state.settings, missing, None)
    assert connection.get(ENDPOINT).status_code == 404


def test_environment_requires_live_read_permission_and_bound_owner(evaluation_environment):
    connection, app, _, _, role = evaluation_environment
    connection.portal.call(app.state.db.execute,
        "UPDATE platform_roles SET permissions='[]'::jsonb WHERE id=%s", (role,))
    assert connection.get(ENDPOINT).status_code == 403
    connection.portal.call(app.state.db.execute,
        "UPDATE platform_roles SET permissions=%s WHERE id=%s", (Jsonb(["evaluation.read"]), role))
    assert connection.get(ENDPOINT).status_code == 200
    other = "evaluation-other-" + uuid4().hex
    connection.portal.call(add_user, DATABASE, other, "integration-password")
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE username=%s", (other,))
    connection.cookies.clear()
    assert connection.post("/api/v1/auth/login", headers=ORIGIN,
                           json={"username": other, "password": "integration-password"}).status_code == 200
    rejected = connection.get(ENDPOINT)
    assert rejected.status_code == 403 and rejected.json()["error"]["code"] == "EVALUATION_OWNER_MISMATCH"
    connection.cookies.clear()
    assert connection.get(ENDPOINT).status_code == 401


@pytest.mark.parametrize("mutation", ["missing", "invalid_json", "unknown_secret", "oversize", "deep_json",
                                    "environment_id", "database_name", "sandbox_namespace", "platform_commit",
                                    "profile_version", "profile_id", "directory"])
def test_environment_rejects_bad_markers_without_paths_or_secrets(evaluation_environment, mutation):
    connection, app, original, directory, _ = evaluation_environment
    marker = deepcopy(original)
    path = directory / "evaluation-instance.json"
    secret = "MARKER_SECRET_MUST_NOT_APPEAR"
    if mutation == "missing":
        path.unlink()
    elif mutation == "invalid_json":
        path.write_text('{"secret":"' + secret, encoding="utf-8")
    elif mutation == "unknown_secret":
        marker["api_key"] = secret
        path.write_text(json.dumps(marker), encoding="utf-8")
    elif mutation == "oversize":
        path.write_text(" " * 4097, encoding="utf-8")
    elif mutation == "deep_json":
        path.write_text("[" * 1100 + "]" * 1100, encoding="utf-8")
    elif mutation == "directory":
        app.state.settings.data_dir = directory.parent
    else:
        if mutation == "profile_version":
            marker["fixture_profile"]["version"] = True
        elif mutation == "profile_id":
            marker["fixture_profile"]["id"] = "../" + secret
        else:
            marker[mutation] = {"environment_id": str(uuid4()), "database_name": "wrong_isolation_test",
                                "sandbox_namespace": str(uuid4()), "platform_commit": "b" * 40}[mutation]
        path.write_text(json.dumps(marker), encoding="utf-8")
    result = connection.get(ENDPOINT)
    assert result.status_code == 503, result.text
    assert result.json()["error"]["code"] == "EVALUATION_ISOLATION_UNVERIFIED"
    assert secret not in result.text and str(directory) not in result.text and "Traceback" not in result.text


@pytest.mark.parametrize("location", ["leaf", "ancestor"])
def test_environment_rejects_windows_junctions(evaluation_environment, location):
    if os.name != "nt":
        pytest.skip("Windows junction boundary")
    connection, app, _, directory, _ = evaluation_environment
    junction = directory.parent / ("alias_test" if location == "leaf" else "alias_parent")
    target = directory if location == "leaf" else directory.parent
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    created = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "New-Item -ItemType Junction -Path " + quote(junction) + " -Target " + quote(target) + " | Out-Null"],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert created.returncode == 0 and junction.is_junction(), created.stderr
    app.state.settings.data_dir = junction if location == "leaf" else junction / directory.name
    try:
        assert connection.get(ENDPOINT).status_code == 503
    finally:
        # Remove this exact owned link, never recurse into its target or touch the target contents.
        assert junction.parent == directory.parent and junction.is_junction()
        junction.rmdir()


def test_environment_rejects_ancestor_symlink_and_marker_symlink(evaluation_environment):
    connection, app, _, directory, _ = evaluation_environment
    alias = directory.parent / "symlink_parent"
    try:
        alias.symlink_to(directory.parent, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable without additional OS privileges")
    try:
        app.state.settings.data_dir = alias / directory.name
        assert connection.get(ENDPOINT).status_code == 503
        app.state.settings.data_dir = directory
        path = directory / "evaluation-instance.json"
        saved = directory / "marker-copy.json"
        path.rename(saved)
        path.symlink_to(saved)
        assert connection.get(ENDPOINT).status_code == 503
    finally:
        alias.unlink()
