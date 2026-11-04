"""Execute the actual target.start function with all process/DB/HTTP edges substituted."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import f05_deadline_contract as proof
import httpx
import pytest
from evaluation_runner import fixed_run
from evaluation_runner.journal import atomic_json
from f05_support import ROOT, load_script


@pytest.mark.parametrize("case_id", ["AET-F05", "AET-A02"])
def test_real_start_contract_skips_only_f05_a02_model_bootstrap(tmp_path, monkeypatch, case_id):
    import asyncio
    import os
    import secrets
    import sys
    import time

    from app import manage, models

    monkeypatch.setattr(sys, "path", list(sys.path))

    child, owner, namespace, model = tmp_path / "child", str(uuid4()), str(uuid4()), str(uuid4())
    state = child / "target"
    state.mkdir(parents=True)
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == case_id)
    from f05_support import target

    value = target()
    context = fixed_run.suite_context(None, specimen, fixed_run.catalog(), value)
    atomic_json(child / "suite-context.json", context)
    repository = tmp_path / "source"
    support = repository / "services/evaluation/profile_support"
    support.mkdir(parents=True)
    (support / "evaluation_target.py").write_bytes(
        (ROOT / "services/evaluation/profile_support/evaluation_target.py").read_bytes()
    )
    atomic_json(
        support / "suite-capabilities.json",
        json.loads((ROOT / "services/evaluation/profile_support/suite-capabilities.json").read_text(encoding="utf-8")),
    )

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(manage, "migrate", noop)
    monkeypatch.setattr(manage, "add_user", noop)
    monkeypatch.setattr(models, "CredentialVault", lambda path: path.parent.mkdir(parents=True, exist_ok=True))
    queries, requests, children = [], [], []

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0]

        def __iter__(self):
            return iter(self.rows)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, *args):
            queries.append(sql)
            if isinstance(sql, str) and "FROM users" in sql:
                return Cursor([(owner,)])
            if isinstance(sql, str) and "work_sandbox_namespace" in sql:
                return Cursor([(namespace,)])
            return Cursor([("001",)])

    def spawn(*args, **kwargs):
        children.append(args)
        atomic_json(state / "server-ready.json", {"api_base_url": "http://127.0.0.1:23459"})
        return SimpleNamespace(pid=987654, poll=lambda: None)

    def server(request):
        requests.append((request.method, request.url.path))
        actual = json.loads((state / "target-state.json").read_text(encoding="utf-8"))
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json={"environment_id": actual["environment_id"], "owner_id": owner})
        if request.url.path.endswith("/models"):
            return httpx.Response(201, json={"id": model})
        return httpx.Response(200, json={"sealed": True})

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda *args, **kwargs: original_client(*args, **kwargs, transport=httpx.MockTransport(server))
    )
    script = load_script("evaluation-target.py")
    namespace_values = {
        "ROOT": repository,
        "__file__": str(ROOT / "scripts/evaluation-target.py"),
        "Path": Path,
        "uuid4": uuid4,
        "secrets": secrets,
        "asyncio": asyncio,
        "os": os,
        "sys": sys,
        "time": time,
        "json": json,
        "admin_params": lambda _: {},
        "target_params": lambda *args: {},
        "make_conninfo": lambda **kwargs: "dbname=controlled_test",
        "platform_parent": lambda *args: None,
        "source_fingerprint": lambda _: {},
        "save": atomic_json,
        "psycopg": SimpleNamespace(connect=lambda *args, **kwargs: Connection()),
        "sql": SimpleNamespace(
            SQL=lambda value: SimpleNamespace(format=lambda *args: value), Identifier=lambda value: value
        ),
        "subprocess": SimpleNamespace(
            check_output=lambda args, **kwargs: value.platform_commit if "rev-parse" in args else "",
            Popen=spawn,
            CREATE_NO_WINDOW=0,
        ),
    }
    for name, substitute in namespace_values.items():
        monkeypatch.setattr(script, name, substitute, raising=False)
    script.start(state, None, None)
    final = json.loads((state / "target-manifest.json").read_text(encoding="utf-8"))
    assert len(children) == 1 and queries  # These are recorded fakes, not a real process/database.
    if case_id == "AET-F05":
        assert requests == [("POST", "/api/v1/auth/login"), ("GET", "/api/v1/agent-evaluations/environment")]
        assert final["resource_bindings"] == {}
        actual = proof.read(state / "target-state.json")
        private = proof.read(state / "server-private.json")
        assert proof.startup_anchor(child, actual, private) == actual["f05_anchor_sha256"]
    else:
        assert requests[-2:] == [
            ("POST", "/api/v1/admin/models"),
            ("POST", "/api/v1/agent-evaluations/profiles/model-bindings"),
        ]
        assert final["resource_bindings"] == {"AET-A02": model}
        assert not any((path / proof.ANCHOR).exists() for path in state.iterdir() if path.is_dir())
