"""Repository scope, encrypted credentials and immutable Git object validation."""

import base64
from uuid import uuid4

import pytest
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client

from app.errors import APIError
from app.repository_provider import GitHub, object_sha

CONTENT = b"print('hello')\n"
COMMIT, TREE = "a" * 40, "b" * 40


class LocalGitHub(GitHub):
    """Deterministic Git data endpoint; no real account or external calls."""

    entries = None
    corrupt = False
    calls = []

    async def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/commits/"):
            return {"sha": COMMIT, "commit": {"tree": {"sha": TREE}}}
        if path.startswith("/git/trees/"):
            return {"tree": self.entries if self.entries is not None else [
                {"path": "代码.py", "mode": "100644", "type": "blob", "size": len(CONTENT),
                 "sha": object_sha(CONTENT)}]}
        if path.startswith("/git/blobs/"):
            return {"encoding": "base64", "content": base64.b64encode(
                CONTENT + (b"!" if self.corrupt else b"")).decode()}
        raise AssertionError((method, path))


def grant(connection, app):
    user = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (user,))
    app.state.repository_factory = LocalGitHub
    return user


def create(connection, **changes):
    return connection.post("/api/v1/repositories", headers=ORIGIN,
        json={"name": "代码仓库", "slug": "example/project", "ref": "feature/测试".replace("测试", "test"), **changes})


def test_private_credentials_cas_readonly_probe_and_new_permissions(client):
    connection, app = client
    assert connection.get("/api/v1/repositories").status_code == 403
    user = grant(connection, app)
    secret = "only-server-repository-secret"
    result = create(connection, token=secret)
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    row = connection.get("/api/v1/repositories").json()["items"][0]
    assert row["has_token"] and secret not in str(row) and "token_ciphertext" not in row
    encrypted = connection.portal.call(app.state.db.fetch, "SELECT token_ciphertext FROM repositories WHERE id=%s",
                                        (identifier,))[0]["token_ciphertext"]
    assert secret not in encrypted
    body = {"name": "改名", "slug": "example/project", "ref": "main", "revision": 1}
    assert connection.put(f"/api/v1/repositories/{identifier}", json=body, headers=ORIGIN).status_code == 200
    assert connection.put(f"/api/v1/repositories/{identifier}", json=body, headers=ORIGIN).status_code == 409
    current = connection.portal.call(app.state.db.fetch, "SELECT token_ciphertext FROM repositories WHERE id=%s",
                                     (identifier,))[0]["token_ciphertext"]
    assert current == encrypted
    LocalGitHub.calls.clear()
    probe = connection.post(f"/api/v1/repositories/{identifier}/test", headers=ORIGIN)
    assert probe.json()["sha"] == COMMIT and probe.json()["files"] == 1
    assert all(method == "GET" for method, _, _ in LocalGitHub.calls)
    assert any(path.startswith("/git/trees/" + TREE) for _, path, _ in LocalGitHub.calls)
    body.update(revision=2, clear_token=True)
    assert connection.put(f"/api/v1/repositories/{identifier}", json=body, headers=ORIGIN).status_code == 200
    assert not connection.get("/api/v1/repositories").json()["items"][0]["has_token"]
    other = "repo-other-" + uuid4().hex
    run_async(add_user(DATABASE, other, "integration-password", is_admin=True))
    cookies = dict(connection.cookies)
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert not connection.get("/api/v1/repositories").json()["items"]
    assert connection.post(f"/api/v1/repositories/{identifier}/test", headers=ORIGIN).status_code == 404
    assert connection.delete(f"/api/v1/repositories/{identifier}", headers=ORIGIN).status_code == 404
    connection.cookies.clear()
    connection.cookies.update(cookies)
    assert connection.get("/api/v1/auth/me").json()["id"] == user
    assert connection.delete(f"/api/v1/repositories/{identifier}", headers=ORIGIN).status_code == 200


@pytest.mark.parametrize("changes", [{"slug": "https://evil.invalid/repo"}, {"slug": "a/b.git"},
    {"ref": "../main"}, {"subdirectory": "../secret"}, {"subdirectory": ".git/config"},
    {"subdirectory": "nested\\file"}, {"token": "secret", "clear_token": True}])
def test_repository_input_rejects_ambiguous_identifiers(client, changes):
    connection, app = client
    grant(connection, app)
    assert create(connection, **changes).status_code == 422


@pytest.mark.parametrize("entry", [
    {"path": "../outside"}, {"path": "nested/.GiT/config"}, {"path": "a", "mode": "120000"},
    {"path": "a", "type": "commit", "mode": "160000"}, {"path": "a", "size": 1024 * 1024 + 1}])
def test_source_entries_refuse_unsafe_or_oversized_before_blob_requests(client, monkeypatch, entry):
    connection, app = client
    grant(connection, app)
    monkeypatch.setattr(LocalGitHub, "entries", [{"path": "a", "type": "blob", "mode": "100644", "size": 1,
                                                  "sha": object_sha(b"x"), **entry}])
    identifier = create(connection).json()["id"]
    assert connection.post(f"/api/v1/repositories/{identifier}/test", headers=ORIGIN).status_code == 422


def test_blob_digest_and_file_count_are_verified(client, monkeypatch):
    connection, app = client
    grant(connection, app)
    identifier = create(connection).json()["id"]
    monkeypatch.setattr(LocalGitHub, "corrupt", True)
    result = connection.post(f"/api/v1/repositories/{identifier}/test", headers=ORIGIN)
    assert result.status_code == 502 and result.json()["error"]["code"] == "REPOSITORY_INTEGRITY"
    monkeypatch.setattr(LocalGitHub, "entries", [{"path": f"{i}.txt", "type": "blob", "mode": "100644", "size": 0,
                                                 "sha": object_sha(b"")} for i in range(181)])
    assert connection.post(f"/api/v1/repositories/{identifier}/test", headers=ORIGIN).status_code == 422


def test_task_freezes_source_and_denies_recovery_or_revoked_repository(client):
    from test_workflow_tasks import enable

    from app.repository_runs import binding

    connection, app = client
    grant(connection, app)
    enable(connection, app)
    repository = create(connection, token="server-only-test-key").json()["id"]
    task = connection.post("/api/v1/work-tasks", headers=ORIGIN,
                           json={"title": "固定仓库版本", "goal": "修改源码"}).json()["id"]
    start_body = {"model_id": "test", "revision": 1, "repository_id": repository}
    wrong_environment = connection.post(f"/api/v1/work-tasks/{task}/runs", headers=ORIGIN,
                                        json={**start_body, "environment": "browser"})
    assert wrong_environment.status_code == 422
    started = connection.post(f"/api/v1/work-tasks/{task}/runs", headers=ORIGIN, json=start_body)
    assert started.status_code == 202, started.text
    run = connection.portal.call(app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s",
                                 (started.json()["run_id"],))[0]
    frozen = connection.portal.call(binding, app.state.db, run)
    assert frozen["source"]["sha"] == COMMIT and "server-only-test-key" not in str(frozen)
    assert frozen["source"]["files"][0]["path"] == "代码.py"
    assert connection.delete(f"/api/v1/repositories/{repository}", headers=ORIGIN).status_code == 409
    assert connection.post(f"/api/v1/work-runs/{run['id']}/cancel", headers=ORIGIN).status_code == 202
    bad_restore = connection.post(f"/api/v1/work-tasks/{task}/runs", headers=ORIGIN,
        json={"model_id": "test", "revision": 1, "restore_from_run": str(run["id"])})
    assert bad_restore.status_code == 422
    connection.portal.call(app.state.db.execute, "UPDATE repositories SET enabled=false WHERE id=%s", (repository,))
    with pytest.raises(APIError) as error:
        connection.portal.call(binding, app.state.db, run)
    assert error.value.code == "REPOSITORY_REVOKED"


def test_github_transport_host_redirects_auth_and_unknown_write(monkeypatch):
    import httpx

    original = httpx.AsyncClient
    requests = []

    async def transport(request):
        requests.append(request)
        if request.method == "POST":
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(302, headers={"Location": "https://evil.invalid/secret"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(transport)))
    provider = GitHub({"slug": "example/project"}, "private-test-only")
    with pytest.raises(APIError):
        run_async(provider.request("GET", "/commits/main"))
    with pytest.raises(APIError):
        run_async(provider.request("POST", "/git/refs", {"ref": "refs/heads/task"}))
    assert len(requests) == 2
    assert all(r.url.host == "api.github.com" and r.headers["Authorization"] == "Bearer private-test-only"
               for r in requests)


def test_snapshot_has_overall_deadline_not_only_per_blob_timeouts(monkeypatch):
    import asyncio

    original_timeout = asyncio.timeout
    monkeypatch.setattr("app.repository_provider.asyncio.timeout", lambda _: original_timeout(0.01))

    class Slow(GitHub):
        async def request(self, method, path, body=None):
            await asyncio.sleep(1)
            raise AssertionError("Overall timeout must cancel this read")

    with pytest.raises(APIError) as error:
        run_async(Slow({"slug": "example/project", "ref": "main", "subdirectory": ""}, "").snapshot())
    assert error.value.code == "REPOSITORY_READ_TIMEOUT"
