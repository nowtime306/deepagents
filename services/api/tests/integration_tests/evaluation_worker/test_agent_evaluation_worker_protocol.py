"""Worker/process protocol tests; no PG, target, business task, sandbox or provider."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import evaluation_platform_effects as effects
import pytest
from evaluation_runner import platform_driver as process

from app import agent_evaluation_platform_worker as worker
from app.agent_evaluation_platform_admission import Admission

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())


def definition():
    raw = json.loads((ROOT / "services/evaluation/datasets/a20-fixed.json").read_text(encoding="utf-8"))
    raw["cases"] = [next(row for row in raw["cases"] if row["case_id"] == "AET-A02")]
    return raw


def request():
    return {
        "schema_version": 1,
        "run_id": str(uuid4()),
        "owner_id": str(uuid4()),
        "candidate_commit": "a" * 40,
        "definition": definition(),
        "budgets": {
            "max_seconds": 300,
            "max_model_calls": 20,
            "max_tokens": 20000,
            "max_sandboxes": 2,
        },
        "repeats": 1,
        "version_set": {},
    }


def result(row, outcome="FAIL"):
    sample = {
        "case_id": "AET-A02",
        "repeat_index": 1,
        "task_outcome": outcome,
        "metric_status": "NOT_REQUIRED",
        "gate_verdict": outcome,
        "cleanup_status": "CLEAN",
        "facts": [{"registered_id": "output_equals", "status": outcome}],
        "metrics": [],
        "evidence_refs": [],
        "root_refs": [],
        "usage": None,
    }
    files = {
        "json": json.dumps({"task_outcome": outcome}),
        "junit": "<testsuite/>",
        "markdown": outcome,
    }
    manifest = {
        "kind": "agent-platform-report-v1",
        "run_id": str(row["id"]),
        "owner_id": str(row["owner_id"]),
        "ready_verified": True,
        "files_sha256": {name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()},
    }
    return {
        "schema_version": 1,
        "verified": True,
        "run_id": str(row["id"]),
        "owner_id": str(row["owner_id"]),
        "samples": [sample],
        "cleanup_status": "CLEAN",
        "report": {
            "manifest": manifest,
            "files": files,
            "verified": True,
            "redacted": True,
        },
    }


@pytest.mark.parametrize("extra", [{"command": ["unsafe"]}, {"path": "C:/private"}, {"token": "secret"}])
def test_process_request_rejects_executable_and_credential_fields(extra):
    body = request()
    body.update(extra)
    with pytest.raises(ValueError):
        process.request_definition(body)


def test_fixed_definition_preflight_is_exact_and_has_no_target_or_api_effect(
    monkeypatch,
):
    body = request()
    value, registered = process.request_definition(body)
    assert value.cases[0].case_id == "AET-A02" and len(registered.cases) == 20
    body["definition"]["cases"][0]["input"] = "changed user input"
    with pytest.raises(ValueError):
        process.request_definition(body)


@pytest.mark.parametrize("key", ["max_seconds", "max_model_calls", "max_tokens", "max_sandboxes"])
def test_child_rejects_insufficient_parent_budget_before_suite_initialization(key):
    body = request()
    body["budgets"][key] = 0
    with pytest.raises(ValueError):
        process.request_definition(body)


def test_zero_publication_worker_does_not_even_claim_an_existing_queue():
    class DB:
        async def fetch(self, *args):
            raise AssertionError("No DB should be consulted")

    async def maintenance():
        return True

    assert asyncio.run(worker.Worker(DB(), Admission(), maintenance=maintenance).once()) is False


def test_empty_or_boolean_sample_result_cannot_complete_or_write_database():
    class DB:
        pass

    async def maintenance():
        return True

    actor = worker.Worker(DB(), Admission(), maintenance=maintenance)
    row = {
        "id": uuid4(),
        "owner_id": uuid4(),
        "snapshot": {"definition": definition()},
        "repeats": 1,
    }
    for samples in ([], [{"case_id": "AET-A02", "repeat_index": True}]):
        with pytest.raises(ValueError):
            asyncio.run(actor.commit(row, {"schema_version": 1, "verified": True, "samples": samples}))


def test_private_permit_is_live_scoped_and_cannot_rebind_target_case():
    allowed = True

    async def validate(payload):
        return allowed

    async def probe():
        nonlocal allowed
        server = worker.PermitServer(validate)
        value = {
            "environment_id": str(uuid4()),
            "case_id": "AET-A02",
            "mode": "ask",
            "boundary": "provider",
        }
        assert await server.permit("wrong", value) is False
        assert await server.permit(server.secret, value) is True
        assert await server.permit(server.secret, {**value, "case_id": "AET-A01"}) is False
        allowed = False
        assert await server.permit(server.secret, value) is False
        assert server.requests == 1

    asyncio.run(probe())


def test_real_loopback_permit_client_denies_after_revocation_and_parent_exit(
    monkeypatch,
):
    allowed = True

    async def validate(payload):
        return allowed

    async def probe():
        nonlocal allowed
        async with worker.PermitServer(validate) as server:
            monkeypatch.setenv(effects.ORIGIN, server.origin)
            monkeypatch.setenv(effects.SECRET, server.secret)
            environment = str(uuid4())
            await asyncio.to_thread(effects.require_permit, environment, "AET-A02", "ask", "provider")
            allowed = False
            with pytest.raises(RuntimeError):
                await asyncio.to_thread(effects.require_permit, environment, "AET-A02", "ask", "tool")
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(effects.require_permit, environment, "AET-A02", "ask", "provider")

    asyncio.run(probe())


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:23456",
        "http://127.0.0.1:8100",
        "https://example.com",
        "http://user:pass@127.0.0.1:12345",
        "http://127.0.0.1:12345/?token=x",
    ],
)
def test_target_delegate_rejects_foreign_reserved_or_credential_bearing_origin(monkeypatch, origin):
    monkeypatch.setenv(effects.ORIGIN, origin)
    monkeypatch.setenv(effects.SECRET, "s" * 40)
    with pytest.raises(RuntimeError):
        effects.configuration()


def test_missing_target_bridge_remains_not_ready_even_with_valid_source(monkeypatch, tmp_path):
    config = worker.DriverConfig(tmp_path, Path(sys.executable), "a" * 40, {}, tmp_path / "admin")
    driver = worker.FixedSuiteDriver(config, {})
    monkeypatch.setattr(driver, "verify_source", lambda: True)
    assert driver.readiness(uuid4())["owner_permissions_per_effect"] is False
    assert driver.readiness(uuid4())["ready"] is False


@pytest.mark.parametrize("exit_code,with_result", [(0, False), (0, True), (3, True)])
def test_actual_controlled_child_protocol_no_auto_replay_and_env_only_secrets(
    monkeypatch, tmp_path, exit_code, with_result
):
    row = {
        "id": uuid4(),
        "owner_id": uuid4(),
        "repeats": 1,
        "snapshot": {"definition": definition(), "version_set": {}},
        "budgets": {
            "max_seconds": 300,
            "max_model_calls": 20,
            "max_tokens": 20000,
            "max_sandboxes": 2,
        },
    }
    response = result(row)
    config = worker.DriverConfig(tmp_path, Path(sys.executable), "a" * 40, {}, tmp_path / "admin")
    driver = worker.FixedSuiteDriver(config, {})
    monkeypatch.setattr(driver, "verify_source", lambda: True)
    monkeypatch.setattr(driver, "validate_definition", lambda *args: True)
    monkeypatch.setattr(driver, "verify_runtime", lambda: True)
    monkeypatch.setattr(
        driver,
        "process_command",
        lambda: [str(config.python), "-I", str(tmp_path / "fixed-entry.py")],
    )
    monkeypatch.setattr(driver, "validate_runtime_proof", lambda actual, nonce: actual["nonce"] == nonce)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("CONFIDENT_API_KEY", "must-not-inherit")
    observed = []
    original = worker.Popen

    def spawn(args, **kwargs):
        observed.append((args, kwargs["env"]))
        assert args[1:3] == ["-I", str(tmp_path / "fixed-entry.py")]
        assert "EVALUATION_PLATFORM_PERMIT_SECRET" not in str(args)
        assert "OPENAI_API_KEY" not in kwargs["env"] and "CONFIDENT_API_KEY" not in kwargs["env"]
        directory = tmp_path / ".local/agent-evaluations/platform-worker" / str(row["id"])
        code = (
            "from pathlib import Path; import sys,json,os,hmac,hashlib; envelope=json.loads(sys.stdin.buffer.read()); "
        )
        if with_result:
            code += (
                "p=Path(sys.argv[1]); proof=json.dumps({'nonce':envelope['nonce']}).encode(); "
                "p.with_name('runtime-attestation.json').write_bytes(proof); "
                "obj=json.loads(sys.argv[2]); obj['runtime_attestation_sha256']=hashlib.sha256(proof).hexdigest(); "
                "raw=json.dumps(obj).encode(); p.write_bytes(raw); "
                "receipt={'schema_version':1,'run_id':obj['run_id'],'owner_id':obj['owner_id'],"
                "'sha256':hashlib.sha256(raw).hexdigest(),"
                "'mac':hmac.new(os.environ['EVALUATION_PLATFORM_RESULT_SECRET'].encode(),"
                "raw,hashlib.sha256).hexdigest()}; "
                "p.with_name('result-ready.json').write_text(json.dumps(receipt)); "
            )
        code += "raise SystemExit(" + str(exit_code) + ")"
        return original(
            [sys.executable, "-I", "-c", code, str(directory / "result.json"), json.dumps(response)],
            **kwargs,
        )

    monkeypatch.setattr(worker, "Popen", spawn)
    permit = SimpleNamespace(origin="http://127.0.0.1:23456", secret="s" * 40)

    async def cancelled():
        return False

    if with_result:
        actual = asyncio.run(driver.execute(row, permit, cancelled))
        assert actual["samples"][0]["task_outcome"] == "FAIL"
        if exit_code == 3:
            assert actual["infrastructure_error"] == "PROCESS_EXIT_3"
    else:
        with pytest.raises(FileNotFoundError):
            asyncio.run(driver.execute(row, permit, cancelled))
    with pytest.raises(FileExistsError):
        asyncio.run(driver.execute(row, permit, cancelled))
    assert len(observed) == 1


def test_cancel_or_expiry_during_permission_await_cannot_grant_later_effect(
    monkeypatch,
):
    live = True

    class DB:
        async def fetch(self, *args):
            return [{"id": 1}] if live else []

    async def maintenance():
        return True

    async def authorize(*args):
        nonlocal live
        live = False

    monkeypatch.setattr(worker, "authorize", authorize)
    actor = worker.Worker(
        DB(),
        SimpleNamespace(inspect=lambda _: {"status": "READY"}),
        maintenance=maintenance,
    )
    row = {
        "id": uuid4(),
        "owner_id": uuid4(),
        "lease_token": uuid4(),
        "snapshot": {"definition": definition()},
    }
    value = {"case_id": "AET-A02", "mode": "ask"}
    assert asyncio.run(actor.permitted(row, value)) is False


@pytest.mark.parametrize("tamper", ["bytes", "owner", "mac", "boolean_schema"])
def test_authenticated_reader_packet_rejects_tamper_before_import(tmp_path, tamper):
    import hmac

    run, owner = uuid4(), uuid4()
    secret = "s" * 40
    raw = b'{"verified":true}'
    (tmp_path / "result.json").write_bytes(raw)
    receipt = {
        "schema_version": 1,
        "run_id": str(run),
        "owner_id": str(owner),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mac": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
    }
    if tamper == "bytes":
        (tmp_path / "result.json").write_bytes(raw + b" ")
    elif tamper == "owner":
        receipt["owner_id"] = str(uuid4())
    elif tamper == "mac":
        receipt["mac"] = "0" * 64
    else:
        receipt["schema_version"] = True
    (tmp_path / "result-ready.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError):
        worker.authenticated_result(tmp_path, secret, run, owner)
