"""Fake clocks and exact fake Popen handles only; never create a process/target/database."""

import json
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from f05_support import ROOT, load_script, target

from evaluation_runner import f05_deadline_contract as authentication
from evaluation_runner import fixed_case_deadline as case
from evaluation_runner import fixed_run
from evaluation_runner.journal import atomic_json

sys.path.insert(0, str(ROOT / "scripts"))
suite = load_script("evaluation-suite.py")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clock = SimpleNamespace(ms=1_700_000_000_000, mono=0)
    monkeypatch.setattr(case.time, "time_ns", lambda: clock.ms * 1_000_000)
    monkeypatch.setattr(case.time, "monotonic", lambda: clock.mono)

    def tick(_):
        clock.ms += 100
        clock.mono += 0.1

    monkeypatch.setattr(case.time, "sleep", tick)
    state, result = tmp_path / "target", tmp_path / "result"
    state.mkdir()
    value = target()
    namespace = str(uuid4())
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == case.CASE)
    context = fixed_run.suite_context(None, specimen, fixed_run.catalog(), value)
    atomic_json(state / "target-manifest.json", value.model_dump())
    actual = {"environment_id": value.environment_id, "owner_id": value.owner_id, "sandbox_namespace": namespace,
              "database_name": "owner_agent_evaluation_" + UUID(value.environment_id).hex + "_test"}
    data = authentication.data_directory(tmp_path, value.environment_id)
    data.mkdir()
    anchor_sha = authentication.provision(tmp_path, {"environment_id": value.environment_id, "owner_id": value.owner_id,
        "namespace": namespace, "candidate_commit": value.platform_commit, "suite_context": context})
    actual.update(f05_guard_required=True, f05_anchor_sha256=anchor_sha)
    atomic_json(state / "target-state.json", actual)
    atomic_json(tmp_path / "suite-context.json", context)
    attempt = {"case_id": case.CASE, "candidate_commit": value.platform_commit, "status": "PASS"}
    supervisor = case.Supervisor(tmp_path, state, result, attempt, 1000)
    return SimpleNamespace(child=tmp_path, state=state, result=result, clock=clock, supervisor=supervisor, attempt=attempt, actual=actual)


class Process:
    pid = 987654  # Never looked up; this object is the only allowed receiver.

    def __init__(self, *, resist=False, denied=False):
        self.returncode, self.actions, self.resist, self.denied = None, [], resist, denied

    def poll(self):
        return self.returncode

    def terminate(self):
        self.actions.append("terminate")
        if self.denied:
            raise PermissionError("controlled exact-handle refusal")
        if not self.resist:
            self.returncode = -15

    def wait(self, timeout):
        self.actions.append(("wait", timeout))
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fixed-engineering-only", timeout)
        return self.returncode

    def kill(self):
        self.actions.append("kill")
        self.returncode = -9


@pytest.mark.parametrize("resist", [False, True])
def test_blocked_cli_is_closed_only_through_held_handle_with_bounded_wait(setup, resist):
    process = Process(resist=resist)
    setup.clock.mono = 300
    assert setup.supervisor.wait(process, "init", setup.child / "parent-cancel") == 124
    assert process.actions == (["terminate", ("wait", 2), "kill", ("wait", 2)] if resist else ["terminate", ("wait", 2)])
    assert setup.attempt["f05_supervision"]["close_verified"] is True
    assert setup.attempt["unknown_submission"] is True and setup.attempt["cleanup"] == "RETAINED"
    assert setup.attempt["status"] == "NEEDS_REVIEW"


def test_same_parent_record_does_not_restart_between_cli_phases(setup):
    original = (setup.child / "case-deadline.json").read_bytes()
    setup.clock.ms += 299_000
    setup.clock.mono = 299
    assert setup.supervisor.before_spawn("run") is None
    assert setup.supervisor.deadline == 300
    setup.clock.ms += 1000
    setup.clock.mono = 300
    assert setup.supervisor.before_spawn("run") == 124
    assert (setup.child / "case-deadline.json").read_bytes() == original


def test_late_zero_exit_is_not_pass_and_preserves_actual_zero(setup):
    class Late(Process):
        def poll(self):
            setup.clock.mono = 300
            self.returncode = 0
            return 0

    process = Late()
    assert setup.supervisor.wait(process, "run", setup.child / "parent-cancel") == 124
    assert setup.attempt["f05_supervision"]["actual_exit_code"] == 0
    assert process.actions == [] and setup.attempt["status"] == "NEEDS_REVIEW"


def test_precise_handle_close_failure_retains_without_other_signal(setup):
    process = Process(denied=True)
    setup.clock.mono = 300
    assert setup.supervisor.wait(process, "run", setup.child / "parent-cancel") == 124
    assert process.actions == ["terminate"]
    assert setup.attempt["f05_supervision"]["close_verified"] is False
    assert setup.attempt["f05_supervision"]["close_error"] == "PermissionError"


def test_receipt_write_failure_does_not_skip_exact_child_close(setup, monkeypatch):
    def fail(*args):
        raise OSError("controlled disk failure")

    monkeypatch.setattr(case, "atomic_json", fail)
    setup.clock.mono = 300
    process = Process()
    assert setup.supervisor.wait(process, "init", setup.child / "parent-cancel") == 124
    assert process.actions == ["terminate", ("wait", 2)]
    assert setup.attempt["f05_supervision"]["receipt_error"] == "OSError"


def test_timeout_skips_formal_cleanup_so_stop_is_requested_without_drop(setup):
    setup.clock.mono = 300
    setup.supervisor.before_spawn("run")

    def forbidden(*args):
        pytest.fail("No formal cleanup/stop wrapper/target-cleanup may block this timeout path")

    suite.finalize_target(setup.attempt, setup.state, setup.result, forbidden)
    assert (setup.state / "stop.request").exists()
    assert setup.attempt["teardown"] == {"stop_requested": True, "server_exit": "UNVERIFIED", "database_drop": "NOT_ATTEMPTED"}
    assert (setup.state / "target-state.json").exists() and setup.attempt["cleanup"] == "RETAINED"


def test_foreign_target_stop_identity_receives_no_stop_request(setup):
    setup.clock.mono = 300
    setup.supervisor.before_spawn("run")
    atomic_json(setup.state / "target-state.json", {**setup.actual, "owner_id": str(uuid4())})
    suite.finalize_target(setup.attempt, setup.state, setup.result, lambda *args: pytest.fail("No external action"))
    assert not (setup.state / "stop.request").exists()
    assert setup.attempt["teardown"]["stop_error"] == "ValueError"


@pytest.mark.parametrize("field,value", [("expires_at_ms", 9_999_999_999_999), ("limit_seconds", 600), ("schema_version", True)])
def test_mutated_parent_record_blocks_before_any_spawn(setup, field, value):
    path = setup.child / "case-deadline.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    atomic_json(path, record)
    assert setup.supervisor.before_spawn("init") == 124
    assert setup.attempt["f05_supervision"]["reason"] == "INVALID_DEADLINE_BEFORE_SPAWN"


def test_invoke_refuses_expired_phase_without_creating_popen(setup, monkeypatch):
    monkeypatch.setattr(suite.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No expired child creation"))
    setup.clock.mono = 300
    assert suite.invoke(["fixed"], setup.child, "run", setup.result / "cancel.request", 1000, supervisor=setup.supervisor) == 124


def test_invoke_non_f05_default_keeps_normal_zero_exit_without_kill(setup, monkeypatch):
    process = Process()
    process.returncode = 0
    monkeypatch.setattr(suite.subprocess, "Popen", lambda *args, **kwargs: process)
    assert suite.invoke(["fixed"], setup.child, "run", setup.result / "cancel.request", 1000) == 0
    assert process.actions == [] and "f05_supervision" not in setup.attempt


def test_actual_invoke_forwards_the_sealed_record_digest_only_to_its_child(setup, monkeypatch):
    process, calls = Process(), []
    process.returncode = 0

    def spawn(args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["env"][case.ENV] == setup.supervisor.checksum
        return process

    monkeypatch.setattr(suite.subprocess, "Popen", spawn)
    assert suite.invoke(["fixed-cli"], setup.child, "init", setup.result / "cancel.request", 1000, supervisor=setup.supervisor) == 0
    assert len(calls) == 1 and process.actions == []


def test_parent_deadline_can_only_shorten_the_fixed_case_window(setup):
    setup.supervisor.deadline = 100
    setup.clock.mono = 100
    assert setup.supervisor.before_spawn("init") == 124
    assert setup.supervisor.record["limit_seconds"] == 300


def test_cancel_closes_same_handle_and_never_signals_other_pid(setup):
    cancel = setup.child / "parent-cancel"
    cancel.touch()
    process = Process()
    assert setup.supervisor.wait(process, "run", cancel) == 124
    assert process.actions == ["terminate", ("wait", 2)]


def test_direct_cli_without_parent_record_cannot_create_a_fixture(tmp_path, monkeypatch):
    from test_f05_flow_code import Fixture, command

    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert fixture.posts == [] and fixture.roots == {}


def test_second_seal_never_overwrites_original_deadline(setup):
    target_value = target()
    with pytest.raises(FileExistsError):
        case.seal(setup.child, target_value, setup.supervisor.record["scope"]["suite_context"], setup.actual["sandbox_namespace"])
