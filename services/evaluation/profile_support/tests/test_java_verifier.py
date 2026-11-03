"""Offline protocol/lifecycle tests. SDK receipts are substitutes, not Linux protection proof."""
import asyncio
import hashlib
import json
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.work_worker import CapacityUnavailable
from evaluation_java_verifier import (
    LAUNCHER_SHA,
    PROGRAM_SHA,
    TESTS_SHA,
    JavaVerifier,
    attach_java_verifier,
    validate_report,
)


def report(checksum):
    empty = hashlib.sha256(b"").hexdigest()
    command = {"exit_code": 0, "timed_out": False, "truncated": False, "stdout_sha256": empty, "stderr_sha256": empty}
    return {"schema_version": 1, "protocol": "java-max-v2", "source_sha256": checksum,
        "program_sha256": PROGRAM_SHA, "tests_sha256": TESTS_SHA, "compile": command.copy(),
        "adapter_compile": command.copy(), "javac_version_sha256": empty, "java_version_sha256": empty,
        "protection": {"uid": 1000, "gid": 1000, "blocked": True, "comparator_unchanged": True,
                       "cwd": "/workspace", "workspace_writable": True, "parent_replace_blocked": True},
        "class_protection": {"adapter_blocked": True, "candidate_blocked": True, "digests_unchanged": True},
        "isolation": {"schema_version": 1, "architecture": "amd64", "uid": 1000, "gid": 1000, "no_new_privs": 1,
            "euid": 1000, "egid": 1000, "saved_uid": 1000, "saved_gid": 1000, "no_supplementary_groups": True,
            "seccomp_mode": 2, "no_inherited_sockets": True, "native_getpid_allowed": True, "policy_sha256": LAUNCHER_SHA,
            "denied_errno": dict.fromkeys(("inet", "inet6", "socketpair", "execd_loopback", "connect", "x32", "compat",
                                           "syscall_425", "syscall_426", "syscall_427", "syscall_438"), 1)},
        "passed": True, "cases": [{**command, "case_index": index, "passed": True,
            "stdout_sha256": hashlib.sha256(str(expected).encode()).hexdigest(),
            "expected_sha256": hashlib.sha256(str(expected).encode()).hexdigest()} for index, expected in enumerate((8, -3, 4))]}


def fixture(tmp_path):
    owner, task, source_run, source_task, artifact, namespace, run_id, lease, snapshot, session = [uuid4() for _ in range(10)]
    content = b"public class Max { public static int max(int a,int b){return a>b?a:b;} }"
    checksum = hashlib.sha256(content).hexdigest()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / str(artifact)).write_bytes(content)
    row = {"id": artifact, "run_id": source_run, "snapshot_id": snapshot, "owner_id": owner,
        "path": "/Max.java", "digest": checksum, "size": len(content), "status": "COMPLETED", "task_id": source_task,
        "model_id": "lead", "environment": "coding", "namespace": namespace, "session_count": 1,
        "active_sessions": 0, "unknown_usage": 0, "unknown_operations": 0}

    class Database:
        live = True
        tasks = 0
        fresh = True
        async def fetch(self, query, params=()):
            if query.startswith("SELECT 1 FROM work_tasks UNION ALL"):
                return [] if self.fresh else [{"one": 1}]
            if "SELECT phase FROM run_resource_stages" in query:
                return []
            if "FROM work_artifacts a" in query:
                return [row] if tuple(map(str, params)) == (str(artifact), str(source_run), str(owner), str(owner)) else []
            if "FROM work_tasks t" in query:
                return [{"revision": 1, "runs": self.tasks}] if params == (task, owner) else []
            if "SELECT 1 FROM work_queue_runs" in query:
                return [{"one": 1}] if self.live and params[0] == run_id and params[3] == lease else []
            raise AssertionError("Unexpected SQL in isolated lifecycle test")
        async def execute(self, query, params=()):
            assert query.startswith("INSERT INTO run_resource_stages")
    class Commands:
        calls = 0
        failure = False
        value = report(checksum)
        async def run(self, command, *, opts):
            self.calls += 1
            assert command.startswith("python3 -I -c ") and opts.uid == opts.gid == 0
            if self.failure:
                raise TimeoutError("Lost execution acknowledgement")
            return SimpleNamespace(error=None, exit_code=0, text=json.dumps(self.value))
    commands = Commands()
    class Sandboxes:
        maximum = 2
        attempts = 1
        creates = 0
        releases = 0
        released = True
        refusal = False
        async def create(self, current, token, kind):
            self.creates += 1
            assert (current, token, kind) == (run_id, lease, "coding")
            if self.refusal:
                return None
            self.attempts += 1
            return {"id": session}, SimpleNamespace(id="fixed-sandbox-id", commands=commands)
        async def release(self, identifier):
            self.releases += 1
            assert identifier == session
            return self.released
    class Models:
        async def get(self, identifier):
            assert identifier in {"lead", "unsealed-id-with-same-model-name"}
            return SimpleNamespace(kind="chat", protocol="openai", model="eval::AET-W02::v1")
    database, sandboxes, models = Database(), Sandboxes(), Models()
    artifacts = SimpleNamespace(directory=artifact_dir, path=lambda identifier: artifact_dir / str(identifier))
    profiles = SimpleNamespace(lock=Lock(), models={"lead": {"owner_id": str(owner), "case_id": "AET-W02", "role": "lead"}})
    args = (owner, str(namespace), task, source_run, artifact, checksum)
    constructor = (database, sandboxes, artifacts, models, profiles)
    keywords = {"environment_id": uuid4(), "state_path": tmp_path / "verifiers.json"}
    service = JavaVerifier(*constructor, **keywords)
    asyncio.run(service.initialize())
    run = {"id": run_id, "task_id": task, "owner_id": owner, "lease_token": lease, "model_id": "lead"}
    return SimpleNamespace(service=service, database=database, sandboxes=sandboxes, row=row, commands=commands,
        args=args, run=run, content=content, artifact=artifact_dir / str(artifact),
        reload=lambda: JavaVerifier(*constructor, **keywords))


def test_actual_leased_dispatch_binds_source_and_consumes_only_second_slot(tmp_path):
    f = fixture(tmp_path)
    async def scenario():
        sealed = await f.service.register(*f.args)
        assert sealed["sealed"] and f.service.handles(f.run["task_id"])
        result = await f.service.execute(f.run)
        assert result["verification"]["passed"] is True
        assert result["source_sha256"] == f.args[-1] and result["verifier_run_id"] == str(f.run["id"])
        assert f.sandboxes.attempts == 2 and f.sandboxes.creates == f.sandboxes.releases == f.commands.calls == 1
        assert f.reload().jobs[str(f.run["task_id"])]["phase"] == "COMPLETED"
        with pytest.raises(RuntimeError, match="no execution replay"):
            await f.service.execute(f.run)
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("status", "RUNNING"), ("environment", "browser"), ("active_sessions", 1),
    ("unknown_usage", 1), ("unknown_operations", 1), ("session_count", 2), ("path", "/Other.java"), ("digest", "0"*64)])
def test_unsettled_or_mismatched_candidate_is_never_registered(tmp_path, field, value):
    f = fixture(tmp_path)
    f.row[field] = value
    with pytest.raises(ValueError):
        asyncio.run(f.service.register(*f.args))
    assert f.sandboxes.creates == 0


def test_foreign_source_owner_and_rebinding_are_refused(tmp_path):
    f = fixture(tmp_path)
    async def scenario():
        wrong = (uuid4(), *f.args[1:])
        with pytest.raises(ValueError):
            await f.service.register(*wrong)
        await f.service.register(*f.args)
        f.service.jobs[str(f.run["task_id"])]["source_sha256"] = "0"*64
        with pytest.raises(ValueError, match="rebound"):
            await f.service.register(*f.args)
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["lease", "bytes", "third_slot", "transport", "release"])
def test_fences_unknown_and_release_failures_cannot_replay_or_allocate_extra(tmp_path, failure):
    f = fixture(tmp_path)
    async def scenario():
        await f.service.register(*f.args)
        if failure == "lease": f.database.live = False
        elif failure == "bytes": f.artifact.write_bytes(b"tampered")
        elif failure == "third_slot": f.sandboxes.attempts = 2
        elif failure == "transport": f.commands.failure = True
        else: f.sandboxes.released = False
        with pytest.raises((RuntimeError, ValueError, TimeoutError)):
            await f.service.execute(f.run)
        if failure in {"lease", "bytes", "third_slot"}:
            assert f.sandboxes.creates == f.commands.calls == 0
        else:
            assert f.sandboxes.creates == f.commands.calls == f.sandboxes.releases == 1
            rebuilt = f.reload()
            assert rebuilt.jobs[str(f.run["task_id"])]["phase"] == "UNKNOWN"
            with pytest.raises(RuntimeError): await rebuilt.execute(f.run)
            assert f.sandboxes.creates == 1
    asyncio.run(scenario())


def test_known_admission_refusal_is_requeueable_only_for_same_real_run(tmp_path):
    f = fixture(tmp_path)
    async def scenario():
        await f.service.register(*f.args)
        f.sandboxes.refusal = True
        with pytest.raises(CapacityUnavailable): await f.service.execute(f.run)
        assert f.service.jobs[str(f.run["task_id"])]["phase"] == "REGISTERED"
        assert f.sandboxes.attempts == 1 and f.commands.calls == 0
        other = {**f.run, "id": uuid4()}
        with pytest.raises(RuntimeError): await f.service.execute(other)
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["stdout", "exit", "self_reported", "program", "protected_number", "uid", "cwd", "class_write", "missing_case"])
def test_candidate_stdout_or_unprotected_markers_cannot_claim_pass(mutation):
    value = report("a"*64)
    if mutation == "stdout": value["cases"][0]["stdout_sha256"] = hashlib.sha256(b"tests passed").hexdigest()
    elif mutation == "exit": value["cases"][0]["exit_code"] = 1
    elif mutation == "self_reported": value["cases"][0]["passed"] = False
    elif mutation == "program": value["program_sha256"] = "0"*64
    elif mutation == "protected_number": value["protection"]["blocked"] = 1
    elif mutation == "uid": value["protection"]["uid"] = 0
    elif mutation == "cwd": value["protection"]["cwd"] = "/tmp"
    elif mutation == "class_write": value["class_protection"]["adapter_blocked"] = False
    else: value["cases"].pop()
    with pytest.raises(ValueError): validate_report(value, "a"*64)


def test_known_compilation_failure_is_preserved_not_changed_to_unknown():
    value = report("a"*64)
    value["compile"]["exit_code"] = 1
    value.pop("adapter_compile"); value.pop("class_protection")
    value.update(cases=[], passed=False)
    assert validate_report(value, "a"*64)["passed"] is False


def test_reconstruction_poison_and_immutable_environment(tmp_path):
    f = fixture(tmp_path)
    asyncio.run(f.service.register(*f.args))
    job = f.service.jobs[str(f.run["task_id"])]
    job.update(phase="RUNNING", verifier_run_id=str(f.run["id"]))
    f.service.save()
    assert f.reload().jobs[str(f.run["task_id"])]["phase"] == "UNKNOWN"
    value = json.loads(f.service.path.read_text(encoding="utf-8"))
    value["environment_id"] = str(uuid4())
    f.service.path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError): f.reload()


def test_missing_registry_on_a_used_target_cannot_fall_through_to_agent(tmp_path):
    f = fixture(tmp_path)
    f.service.path.unlink()
    f.database.fresh = False
    rebuilt = f.reload()
    with pytest.raises(RuntimeError, match="requires review"):
        asyncio.run(rebuilt.initialize())
    with pytest.raises(RuntimeError): rebuilt.handles(f.run["task_id"])


def test_durable_intent_failure_blocks_creation_and_fallback(tmp_path, monkeypatch):
    import evaluation_java_verifier
    f = fixture(tmp_path)
    asyncio.run(f.service.register(*f.args))
    def failed(*args): raise OSError("Private state storage unavailable")
    monkeypatch.setattr(evaluation_java_verifier.os, "replace", failed)
    with pytest.raises(OSError): asyncio.run(f.service.execute(f.run))
    assert f.sandboxes.creates == 0
    with pytest.raises(RuntimeError): f.service.handles(f.run["task_id"])


def test_target_only_registration_rejects_free_commands_before_service(tmp_path, monkeypatch):
    import httpx
    from app import agent_evaluation_environment, permissions, security
    from fastapi import FastAPI
    calls = []
    owner, namespace = uuid4(), uuid4()
    async def environment(request): return {"owner_id": owner, "sandbox_namespace": namespace}
    async def administrator(request): return {"id": owner}
    async def require_account(database, identity, permission): calls.append(permission)
    monkeypatch.setattr(agent_evaluation_environment, "environment", environment)
    monkeypatch.setattr(security, "administrator", administrator)
    monkeypatch.setattr(permissions, "require_account", require_account)
    class Service:
        async def register(self, *args):
            calls.append(args)
            return {"sealed": True}
    app = FastAPI()
    app.state.db = object()
    assert not app.routes[-1].path.startswith("/api/v1/agent-evaluations/profiles/java-verifiers")
    attach_java_verifier(app, Service())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            body = {"task_id": str(uuid4()), "source_run_id": str(uuid4()), "artifact_id": str(uuid4()), "source_sha256": "a"*64}
            response = await client.post("/api/v1/agent-evaluations/profiles/java-verifiers", json={**body, "command": "forbidden"})
            assert response.status_code == 422 and calls == []
            response = await client.post("/api/v1/agent-evaluations/profiles/java-verifiers", json=body)
            assert response.status_code == 200 and response.json()["sealed"] is True
            assert calls[:2] == ["evaluation.run", "work.execute"] and calls[2][:2] == (owner, str(namespace))
    asyncio.run(scenario())


def test_sdk_program_has_no_root_command_execution_branch():
    # Static structural check only. Actual Linux privilege/filesystem probes remain required.
    import ast
    source = Path(__file__).parents[1].joinpath("java_verifier_program.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    popen = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
    assert len(popen) == 1
    assert next(keyword.value.value for keyword in popen[0].keywords if keyword.arg == "user") == 1000
    assert next(keyword.value.value for keyword in popen[0].keywords if keyword.arg == "group") == 1000
    assert not any(keyword.arg == "preexec_fn" for keyword in popen[0].keywords)
    assert next(keyword.value.value for keyword in popen[0].keywords if keyword.arg == "close_fds") is True
    assert "-proc:none" in source and "getpwnam" in source and "getpwuid" in source


@pytest.mark.parametrize("mutation", ["same_name_unsealed", "source_model_changed", "binding_revoked"])
def test_exact_model_id_is_required_before_verifier_sandbox_creation(tmp_path, mutation):
    f = fixture(tmp_path)
    async def scenario():
        await f.service.register(*f.args)
        if mutation == "same_name_unsealed": f.run["model_id"] = "unsealed-id-with-same-model-name"
        elif mutation == "binding_revoked": f.service.profiles.models.pop("lead")
        else:
            f.row["model_id"] = "changed-id"
            f.service.profiles.models["changed-id"] = f.service.profiles.models["lead"].copy()
        with pytest.raises(ValueError): await f.service.execute(f.run)
        assert f.sandboxes.creates == f.commands.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, True, 1, ""])
def test_reconstruction_rejects_missing_or_untyped_sealed_model_identity(tmp_path, value):
    f = fixture(tmp_path)
    asyncio.run(f.service.register(*f.args))
    body = json.loads(f.service.path.read_text(encoding="utf-8"))
    job = body["jobs"][str(f.run["task_id"])]
    if value is None: job.pop("source_model_id")
    else: job["source_model_id"] = value
    f.service.path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError): f.reload()


@pytest.mark.parametrize("mutation", ["missing", "policy", "socket_allowed", "numeric_marker", "compat", "inherited_fd"])
def test_missing_network_isolation_cannot_mint_a_success(mutation):
    body = report("a"*64)
    if mutation == "missing": body.pop("isolation")
    elif mutation == "policy": body["isolation"]["policy_sha256"] = "0"*64
    elif mutation == "socket_allowed": body["isolation"]["denied_errno"]["execd_loopback"] = 0
    elif mutation == "numeric_marker": body["isolation"]["denied_errno"]["inet"] = True
    elif mutation == "compat": body["isolation"]["denied_errno"].pop("compat")
    else: body["isolation"]["no_inherited_sockets"] = False
    with pytest.raises((KeyError, ValueError)): validate_report(body, "a"*64)


def test_bpf_policy_closes_native_x32_compat_and_io_uring_paths_without_host_install():
    from java_verifier_launcher import ALLOW, AUDIT_AMD64, DENIED, ERRNO, policy
    def interpret(arch, number):
        accumulator, pointer = 0, 0
        rules = policy()
        for _ in range(len(rules) * 2):
            code, yes, no, value = rules[pointer]
            if code == 0x20: accumulator = arch if value == 4 else number
            elif code == 0x15: pointer += yes if accumulator == value else no
            elif code == 0x45: pointer += yes if accumulator & value else no
            elif code == 0x35: pointer += yes if accumulator >= value else no
            elif code == 0x06: return value
            else: raise AssertionError("Unexpected instruction")
            pointer += 1
        raise AssertionError("Policy did not terminate")
    assert interpret(AUDIT_AMD64, 39) == ALLOW
    assert all(interpret(AUDIT_AMD64, number) == ERRNO for number in DENIED)
    assert interpret(AUDIT_AMD64, 0x40000000 | 41) == ERRNO
    assert interpret(AUDIT_AMD64, 512) == ERRNO
    assert interpret(0x40000003, 20) == ERRNO  # i386 compat getpid
    assert interpret(0xC00000B7, 198) == ERRNO  # Another architecture cannot reinterpret the syscall table.


def test_known_candidate_exit_125_is_a_failed_test_not_missing_isolation():
    body = report("a"*64)
    for row in body["cases"]:
        row.update(exit_code=125, stdout_sha256=hashlib.sha256(b"").hexdigest(), passed=False)
    body["passed"] = False
    assert validate_report(body, "a"*64)["passed"] is False
    body.pop("isolation")
    with pytest.raises((KeyError, ValueError)):
        validate_report(body, "a"*64)


def test_command_preserves_125_without_running_host_java():
    import ast
    import tempfile
    source = Path(__file__).parents[1].joinpath("java_verifier_program.py").read_text(encoding="utf-8")
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "command")
    process = SimpleNamespace(pid=123, returncode=125, wait=lambda **kwargs: None)
    calls = []
    def popen(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return process
    def absent(*args): raise ProcessLookupError()
    namespace = {"subprocess": SimpleNamespace(Popen=popen, DEVNULL=-3, TimeoutExpired=TimeoutError),
        "tempfile": tempfile, "sys": SimpleNamespace(executable="never-executed"), "LAUNCHER": Path("fixed-launcher"),
        "ENV": {}, "os": SimpleNamespace(killpg=absent), "signal": SimpleNamespace(SIGKILL=9),
        "sha": lambda value: hashlib.sha256(value).hexdigest()}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "fixed-command-under-test", "exec"), namespace)  # noqa: S102 -- Owned fixed function only, with substituted Popen; no supplied code.
    receipt, output, error = namespace["command"](["java", "fixed-source"])
    assert receipt["exit_code"] == 125 and not receipt["timed_out"] and output == error == b""
    assert len(calls) == 1 and calls[0][1]["user"] == 1000
