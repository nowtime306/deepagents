"""Validate GitHub serialization against actual Git objects in a new trusted temporary repository."""

import base64
import json
import os
import subprocess
from urllib.parse import unquote, urlsplit

import httpx
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client
from test_repositories import CONTENT
from test_repository_actions import decide, prepared
from test_repository_actions import ready as ready

from app.repository_provider import GitHub


class LocalGit:
    """Only fixed Git subcommands operate on test-owned primitive files, never the project repository."""

    def __init__(self, root):
        self.root = root.resolve()
        self.environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        self.call("init", "--template=", "--initial-branch=main")
        self.call("config", "user.name", "Protocol Test")
        self.call("config", "user.email", "protocol@local.invalid")
        (root / "src").mkdir()
        (root / "src/main.py").write_bytes(CONTENT)
        (root / "src/delete.txt").write_bytes(b"delete")
        (root / "retained.txt").write_bytes(b"outside selected directory")
        self.call("add", "--all")
        self.call("commit", "--no-gpg-sign", "-m", "trusted fixture baseline")

    def call(self, *args, data=None, index=False):
        environment = dict(self.environment)
        if index:
            environment["GIT_INDEX_FILE"] = str(self.root / ".git" / "protocol-index")
        result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, *args],
            cwd=self.root, env=environment, input=data, capture_output=True, check=True)
        return result.stdout

    def handle(self, request):
        path = urlsplit(str(request.url)).path.removeprefix("/repos/example/project")
        body = json.loads(request.content) if request.content else {}
        if path.startswith("/commits/"):
            ref = unquote(path.removeprefix("/commits/"))
            return {"sha": self.call("rev-parse", ref).decode().strip(),
                    "commit": {"tree": {"sha": self.call("rev-parse", ref + "^{tree}").decode().strip()}}}
        if path.startswith("/git/trees/"):
            tree = path.removeprefix("/git/trees/")
            options = ["-rlz"] if "recursive=1" in request.url.query.decode() else ["-lz"]
            entries = []
            for line in self.call("ls-tree", *options, tree).split(b"\0"):
                if not line:
                    continue
                metadata, name = line.split(b"\t", 1)
                mode, kind, object_id, size = metadata.decode().split()
                entries.append({"path": name.decode(), "mode": mode, "type": kind, "sha": object_id,
                                **({"size": int(size)} if kind == "blob" else {})})
            return {"sha": tree, "tree": entries, "truncated": False}
        if path.startswith("/git/blobs/"):
            blob = path.removeprefix("/git/blobs/")
            data = self.call("cat-file", "blob", blob)
            return {"sha": blob, "encoding": "base64", "size": len(data), "content": base64.b64encode(data).decode()}
        if path == "/git/blobs":
            data = base64.b64decode(body["content"], validate=True)
            return {"sha": self.call("hash-object", "-w", "--stdin", data=data).decode().strip()}
        if path == "/git/trees":
            self.call("read-tree", body["base_tree"], index=True)
            for item in body["tree"]:
                mode, object_id = (item["mode"], item["sha"]) if item["sha"] else ("0", "0" * 40)
                self.call("update-index", "--index-info",
                          data=(mode + " " + object_id + "\t" + item["path"] + "\n").encode(),
                          index=True)
            return {"sha": self.call("write-tree", index=True).decode().strip()}
        if path == "/git/commits":
            return {"sha": self.call("commit-tree", body["tree"], "-p", body["parents"][0], "-m", body["message"])
                    .decode().strip()}
        if path == "/git/refs":
            self.call("update-ref", body["ref"], body["sha"], "0" * 40)
            return {"ref": body["ref"], "object": {"sha": body["sha"]}}
        raise AssertionError((request.method, path))


def test_actual_objects_fixed_subdirectory_delete_and_new_branch_no_default_write(ready, tmp_path, monkeypatch):
    connection, app, run, repository, _ = ready
    fixture = LocalGit(tmp_path)
    base = fixture.call("rev-parse", "main").decode().strip()
    tree = fixture.call("rev-parse", "main^{tree}").decode().strip()
    source = connection.portal.call(app.state.db.fetch,
        "SELECT source FROM work_run_repositories WHERE run_id=%s", (run["id"],))[0]["source"]
    source.update(sha=base, tree=tree)
    connection.portal.call(app.state.db.execute, "UPDATE work_run_repositories SET source=%s WHERE run_id=%s",
                           (Jsonb(source), run["id"]))
    original = httpx.AsyncClient
    calls = []

    async def transport(request):
        assert request.url.host == "api.github.com"
        assert request.headers["Authorization"] == "Bearer private-test-repository-token"
        calls.append((request.method, request.url.path))
        return httpx.Response(201 if request.method == "POST" else 200, json=fixture.handle(request))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(transport)))
    app.state.repository_factory = GitHub
    probe = connection.post(f"/api/v1/repositories/{repository}/test", headers=ORIGIN)
    assert probe.status_code == 200 and probe.json()["sha"] == base and probe.json()["files"] == 2, probe.text
    action = prepared(connection, run)
    assert decide(connection, action).status_code == 200
    branch = "owner-agent/" + str(run["id"])
    assert fixture.call("show", branch + ":src/main.py") == b"print('changed')\n"
    assert fixture.call("show", branch + ":retained.txt") == b"outside selected directory"
    assert fixture.call("ls-tree", "--name-only", branch, "src/delete.txt") == b""
    assert fixture.call("rev-parse", "main").decode().strip() == base
    assert fixture.call("rev-parse", branch + "^").decode().strip() == base
    assert b"100755" in fixture.call("ls-tree", branch, "src/main.py")
    assert sum(method == "POST" and path.endswith("/git/refs") for method, path in calls) == 1
    assert b"private-test-repository-token" not in (tmp_path / ".git/config").read_bytes()
