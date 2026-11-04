"""Runtime identity regressions; no target, PG, business task or paid supplier."""

import importlib.util
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import evaluation_parent_guard as parent
import evaluation_platform_effects as effects
import pytest

from app import agent_evaluation_platform_worker as worker

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())


def load_script(name, path):
    """Load an active repository CLI with a hyphenated filename; never private candidate code."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime = load_script("worker_runtime_bootstrap", ROOT / "scripts/evaluation-runtime.py")


def required_guard(tmp_path, monkeypatch):
    data = tmp_path / "own_test"
    data.mkdir()
    owner, environment = str(uuid4()), str(uuid4())
    marker = {
        "schema_version": 1,
        "contract": parent.CONTRACT,
        "parent_run_id": str(uuid4()),
        "parent_owner_id": str(uuid4()),
        "case_id": "AET-A02",
        "candidate_commit": "a" * 40,
        "environment_id": environment,
        "owner_id": owner,
    }
    (data / parent.MARKER).write_text(json.dumps(marker), encoding="utf-8")
    (data / "evaluation-instance.json").write_text(
        json.dumps({"owner_id": owner, "environment_id": environment}), encoding="utf-8"
    )
    monkeypatch.setenv(effects.ORIGIN, "http://127.0.0.1:23456")
    monkeypatch.setenv(effects.SECRET, "s" * 40)
    return parent.ParentGuard(
        SimpleNamespace(
            data_dir=data,
            database_url="dbname=private_test",
            evaluation_platform_commit="a" * 40,
            evaluation_instance_id=environment,
        )
    )


@pytest.mark.parametrize("boundary", ["provider", "tool", "sandbox", "submission"])
def test_required_actual_two_read_race_losing_both_env_values_denies(tmp_path, monkeypatch, boundary):
    guard = required_guard(tmp_path, monkeypatch)

    def first_read_then_lose_both():
        configured = effects.configuration()
        monkeypatch.delenv(effects.ORIGIN)
        monkeypatch.delenv(effects.SECRET)
        return configured

    def no_request(*args):
        raise AssertionError("No permission request or downstream effect may occur after required loss")

    monkeypatch.setattr(parent, "configuration", first_read_then_lose_both)
    monkeypatch.setattr(effects, "build_opener", no_request)
    with pytest.raises(RuntimeError, match="disappeared"):
        guard.check_sync(boundary)
    assert guard.required is True and effects.configuration() is None


@contextmanager
def response_server(value):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    "value,allowed",
    [
        ({"allowed": True}, True),
        ({"allowed": 1}, False),
        ({"allowed": "true"}, False),
        ({"allowed": True, "extra": 0}, False),
    ],
)
def test_real_private_http_permission_requires_exact_boolean(monkeypatch, value, allowed):
    with response_server(value) as (origin, calls):
        monkeypatch.setenv(effects.ORIGIN, origin)
        monkeypatch.setenv(effects.SECRET, "s" * 40)
        if allowed:
            effects.require_permit(str(uuid4()), "AET-A02", "ask", "provider", required=True)
        else:
            with pytest.raises(RuntimeError):
                effects.require_permit(str(uuid4()), "AET-A02", "ask", "provider", required=True)
        assert calls == ["/permit"]


def clean_environment(home):
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
    }
    env.update(
        {
            key: str(home)
            for key in (
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "DEEPEVAL_HOME",
                "ENV_DIR_PATH",
            )
        }
    )
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        DEEPEVAL_TELEMETRY_OPT_OUT="1",
        DEEPEVAL_DISABLE_DOTENV="1",
    )
    return env


def base_request():
    import evaluation_sources as module

    return {
        "schema_version": 1,
        "repository": str(ROOT),
        "candidate_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_pins": module.source_fingerprint(ROOT),
        "runtime_pin": None,
        "version_set": {},
        "nonce": str(uuid4()),
        "action": "inspect",
        "argument": None,
    }


def invoke(python, request, home):
    return subprocess.run(
        [str(python), "-I", str(ROOT / "scripts/evaluation-runtime.py")],
        input=json.dumps(request).encode(),
        env=clean_environment(home),
        cwd=ROOT,
        capture_output=True,
        timeout=60,
        check=False,
    )


def test_real_isolated_api_interpreter_cannot_borrow_checkout_runner(tmp_path):
    request = base_request()
    result = invoke(ROOT / "services/api/.venv/Scripts/python.exe", request, tmp_path)
    assert result.returncode == 3 and not result.stdout
    assert json.loads(result.stderr)["status"] == "BLOCKED"
    # -I strips the test PYTHONPATH. API's environment is deliberately not the independent runner install.


@pytest.fixture(scope="module")
def actual_inspection(tmp_path_factory):
    home = tmp_path_factory.mktemp("actual-runtime")
    request = base_request()
    result = invoke(ROOT / "services/evaluation/.venv/Scripts/python.exe", request, home)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    proof = json.loads(result.stdout)
    assert proof["nonce"] == request["nonce"] and proof["package_origin"] == str(
        ROOT / "services/evaluation/src/evaluation_runner/__init__.py"
    )
    return request, proof, home


def attestation_request(inspection):
    request, proof, home = inspection
    request = deepcopy(request)
    request.update(
        action="attest",
        runtime_pin=proof["runtime"],
        nonce=str(uuid4()),
        version_set={
            "candidate_ref": str(uuid4()),
            "platform_commit": proof["candidate_commit"],
            "source_digest": proof["source_digest"],
            "runner_lock_digest": proof["runner_lock_digest"],
            "rules_id": proof["rules_id"],
            "rules_digest": proof["rules_digest"],
            "business_refs": [],
            "model_refs": [],
            "judge_ref": None,
        },
    )
    return request, home


def test_real_isolated_eval_interpreter_module_lock_dependencies_and_version_set_match(
    actual_inspection,
):
    request, home = attestation_request(actual_inspection)
    result = invoke(ROOT / "services/evaluation/.venv/Scripts/python.exe", request, home)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    proof = json.loads(result.stdout)
    assert proof["runtime"] == request["runtime_pin"] and proof["nonce"] == request["nonce"]
    assert proof["runner_lock_digest"] == request["version_set"]["runner_lock_digest"]


@pytest.mark.parametrize("fault", ["python_sha", "lock", "candidate", "module_bytes", "source_identity"])
def test_real_isolated_process_rejects_wrong_interpreter_lock_candidate_or_module_before_runner_execution(
    actual_inspection, fault
):
    request, home = attestation_request(actual_inspection)
    if fault == "python_sha":
        request["runtime_pin"]["python_sha256"] = "0" * 64
    if fault == "lock":
        request["source_pins"]["services/evaluation/uv.lock"] = "0" * 64
    if fault == "candidate":
        request["candidate_commit"] = "0" * 40
    if fault == "module_bytes":
        request["source_pins"]["services/evaluation/src/evaluation_runner/fixed_run.py"] = "0" * 64
    if fault == "source_identity":
        request["version_set"]["source_digest"] = "0" * 64
    result = invoke(ROOT / "services/evaluation/.venv/Scripts/python.exe", request, home)
    assert result.returncode == 3 and not result.stdout
    assert json.loads(result.stderr)["status"] == "BLOCKED"


def test_installed_same_version_modified_dependency_content_rejects_pin(actual_inspection, monkeypatch):
    request, _ = attestation_request(actual_inspection)
    actual = deepcopy(request["runtime_pin"])
    actual["installed_files_digest"] = "0" * 64
    monkeypatch.setattr(
        runtime,
        "interpreter_identity",
        lambda: {
            key: request["runtime_pin"][key]
            for key in (
                "python_version",
                "python_sha256",
                "base_python_sha256",
                "pyvenv_sha256",
            )
        },
    )
    monkeypatch.setattr(runtime, "runtime_identity", lambda _: actual)
    with pytest.raises(ValueError, match="dependency files"):
        runtime.attest(request)


@pytest.mark.parametrize(
    "fault",
    [
        "nonce",
        "module_digest",
        "rules_digest",
        "runtime",
        "business_refs",
        "boolean_schema",
    ],
)
def test_api_consumer_rejects_foreign_or_incomplete_actual_process_proof(tmp_path, fault):
    pins = {
        "services/evaluation/uv.lock": "b" * 64,
        "services/evaluation/src/evaluation_runner/__init__.py": "c" * 64,
    }
    nonce = str(uuid4())
    version = {
        "candidate_ref": str(uuid4()),
        "platform_commit": "a" * 40,
        "source_digest": worker.digest(pins),
        "runner_lock_digest": "b" * 64,
        "rules_id": "controlled-a20-v1",
        "rules_digest": "d" * 64,
        "business_refs": [],
        "model_refs": [],
        "judge_ref": None,
    }
    pin = {"test": "only proof-parser test; not an accepted runtime pin"}
    config = worker.DriverConfig(
        tmp_path,
        Path(sys.executable),
        "a" * 40,
        pins,
        tmp_path / "admin",
        runtime_pin=pin,
    )
    driver = worker.FixedSuiteDriver(config, version)
    proof = {
        "schema_version": 1,
        "nonce": nonce,
        "runtime": pin,
        "candidate_commit": "a" * 40,
        "source_digest": worker.digest(pins),
        "runner_lock_digest": "b" * 64,
        "package_origin": str(tmp_path / "services/evaluation/src/evaluation_runner/__init__.py"),
        "module_digest": worker.digest({key: value for key, value in pins.items() if key.endswith(".py")}),
        "rules_id": "controlled-a20-v1",
        "rules_digest": "d" * 64,
    }
    assert driver.validate_runtime_proof(proof, nonce) is True
    if fault in {"nonce", "module_digest", "rules_digest"}:
        proof[fault] = "e" * 64
    if fault == "runtime":
        proof["runtime"] = {}
    if fault == "business_refs":
        version["business_refs"] = [
            {
                "kind": "agent",
                "resource_id": str(uuid4()),
                "version": 1,
                "definition_digest": "f" * 64,
            }
        ]
    if fault == "boolean_schema":
        proof["schema_version"] = True
    with pytest.raises(ValueError):
        driver.validate_runtime_proof(proof, nonce)
