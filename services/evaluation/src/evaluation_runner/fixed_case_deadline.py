"""F05-only parent record and direct-child supervision; no target PID killing or remote cancellation."""

import hashlib
import json
import os
import stat
import subprocess
import time
from uuid import UUID

from . import f05_deadline_contract as authentication
from .contracts import Target
from .journal import atomic_json, digest

CASE = "AET-F05"
ENV = "EVALUATION_F05_DEADLINE_SHA256"


def bounded(path):
    if any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents)):
        raise ValueError("Linked F05 supervisor record")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("F05 supervisor record is not a regular file")
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Oversized F05 supervisor record")
    return raw


def scope(target, context, namespace):
    if context["case_id"] != CASE or context["candidate_commit"] != target.platform_commit:
        raise ValueError("F05 supervisor case/candidate differs")
    return {"environment_id": target.environment_id, "owner_id": target.owner_id,
            "target_digest": digest(target.model_dump()), "suite_context": context, "namespace": str(UUID(namespace))}


def validate(record, expected_scope):
    if (set(record) != {"schema_version", "kind", "scope", "started_at_ms", "expires_at_ms", "limit_seconds"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["kind"] != "f05-parent-deadline" or digest(record["scope"]) != digest(expected_scope)
            or any(type(record[name]) is not int for name in ("started_at_ms", "expires_at_ms", "limit_seconds"))
            or record["limit_seconds"] != 300 or record["started_at_ms"] <= 0
            or record["expires_at_ms"] != record["started_at_ms"] + 300_000):
        raise ValueError("Invalid fixed F05 parent deadline")
    return record


def seal(child, target, context, namespace):
    value = {"schema_version": 1, "kind": "f05-parent-deadline", "scope": scope(target, context, namespace),
             "started_at_ms": time.time_ns() // 1_000_000, "limit_seconds": 300}
    value["expires_at_ms"] = value["started_at_ms"] + 300_000
    path = child / "case-deadline.json"
    if any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents)):
        raise ValueError("Linked F05 supervisor output")
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    authentication.sign_record(child, value, hashlib.sha256(raw).hexdigest())
    return value, hashlib.sha256(raw).hexdigest()


def load(child, checksum, expected_scope=None):
    raw = bounded(child / "case-deadline.json")
    if not isinstance(checksum, str) or len(checksum) != 64 or hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError("F05 parent deadline bytes changed")
    value = json.loads(raw.decode("utf-8"))
    return validate(value, value["scope"] if expected_scope is None else expected_scope)


class Supervisor:
    """Only the direct Popen object passed by this case may be terminated; never reopen a PID."""

    def __init__(self, child, state, result, attempt, parent_deadline):
        target = Target.model_validate_json(bounded(state / "target-manifest.json").decode("utf-8"))
        context = json.loads(bounded(child / "suite-context.json").decode("utf-8"))
        actual = json.loads(bounded(state / "target-state.json").decode("utf-8"))
        if actual["environment_id"] != target.environment_id or actual["owner_id"] != target.owner_id:
            raise ValueError("F05 target state identity differs")
        self.child, self.state, self.result, self.attempt = child, state, result, attempt
        self.record, self.checksum = seal(child, target, context, actual["sandbox_namespace"])
        self.deadline = min(parent_deadline, time.monotonic() + max(0, (self.record["expires_at_ms"] - time.time_ns() // 1_000_000) / 1000))

    def expired(self):
        load(self.child, self.checksum, self.record["scope"])
        now = time.time_ns() // 1_000_000
        return time.monotonic() >= self.deadline or not self.record["started_at_ms"] <= now < self.record["expires_at_ms"]

    def retain(self, label, reason, process=None):
        proof = {"phase": label, "reason": reason, "pid": process.pid if process else None,
                 "deadline_sha256": self.checksum,
                 "actual_exit_code": process.poll() if process else None, "close_verified": process is None}
        self.attempt.update(unknown_submission=True, cleanup="RETAINED", f05_supervision=proof)
        if self.attempt.get("status") != "FAIL":
            self.attempt["status"] = "NEEDS_REVIEW"
        try:
            atomic_json(self.child / "case-supervision.json", proof)
            self.result.mkdir(exist_ok=True)
            (self.result / "cancel.request").touch()
        except OSError as error:
            proof["receipt_error"] = type(error).__name__
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()  # Windows Popen retains its actual process handle; no PID lookup.
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                proof.update(actual_exit_code=process.returncode, close_verified=process.poll() is not None)
            except (OSError, subprocess.SubprocessError) as error:
                proof["close_error"] = type(error).__name__
            try:
                atomic_json(self.child / "case-supervision.json", proof)
            except OSError as error:
                proof["receipt_error"] = type(error).__name__
        return 124

    def before_spawn(self, label):
        try:
            if label not in {"init", "run"} or self.expired():
                return self.retain(label, "DEADLINE_BEFORE_SPAWN")
        except (OSError, ValueError, KeyError):
            return self.retain(label, "INVALID_DEADLINE_BEFORE_SPAWN")
        return None

    def wait(self, process, label, cancel_path):
        try:
            while True:
                if self.expired() or cancel_path.exists() or (self.result / "cancel.request").exists():
                    return self.retain(label, "DEADLINE_OR_CANCEL", process)
                if process.poll() is not None:
                    if self.expired():
                        return self.retain(label, "LATE_PROCESS_EXIT", process)
                    return process.returncode
                time.sleep(0.05)
        except BaseException:
            self.retain(label, "SUPERVISOR_INTERRUPTED", process)
            raise


def request_owned_stop(attempt, state, result):
    """A timeout skips potentially blocking formal cleanup; a stop request is not an exit receipt."""
    attempt.update(unknown_submission=True, cleanup="RETAINED")
    if attempt.get("status") != "FAIL":
        attempt["status"] = "NEEDS_REVIEW"
    try:
        record = load(state.parent, attempt["f05_supervision"]["deadline_sha256"])
        actual = json.loads(bounded(state / "target-state.json").decode("utf-8"))
        expected = record["scope"]
        if (expected["suite_context"]["case_id"] != CASE or attempt["case_id"] != CASE
                or expected["suite_context"]["candidate_commit"] != attempt["candidate_commit"]
                or actual["environment_id"] != expected["environment_id"] or actual["owner_id"] != expected["owner_id"]
                or actual["sandbox_namespace"] != expected["namespace"]
                or actual["database_name"] != "owner_agent_evaluation_" + UUID(expected["environment_id"]).hex + "_test"):
            raise ValueError("Foreign F05 stop scope")
        stop = state / "stop.request"
        if stop.is_symlink() or stop.is_junction():
            raise ValueError("Linked F05 stop request")
        stop.touch()
        attempt["teardown"] = {"stop_requested": True, "server_exit": "UNVERIFIED", "database_drop": "NOT_ATTEMPTED"}
        result.mkdir(exist_ok=True)
        (result / "cancel.request").touch()
    except (OSError, ValueError, KeyError, TypeError) as error:
        attempt["teardown"] = {"stop_error": type(error).__name__, "server_exit": "UNVERIFIED", "database_drop": "NOT_ATTEMPTED"}
