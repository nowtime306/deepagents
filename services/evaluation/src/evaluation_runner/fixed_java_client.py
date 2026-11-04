"""Inactive private W02 client candidate; tested only through controlled HTTP stand-ins."""

import difflib
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from uuid import UUID

from evaluation_runner import adapters, fixed_audit, network
from evaluation_runner.contracts import Result
from evaluation_runner.fixed_evidence import binding, load, store
from evaluation_runner.fixed_fixtures import FixedFixtures
from evaluation_runner.journal import digest as intent_digest

SHA = re.compile(r"^[a-f0-9]{64}$")
ORIGINAL = "public class Max { public static int max(int a,int b){return a;} }"
EXPECTED = (8, -3, 4)
PROGRAM_SHA = "2c91f045fd9bdd7713255efed8b52588464c07bddad190d21d0b743008f14a62"  # Private oracle correction candidate; pending review and physical controls.
LAUNCHER_SHA = "f0523eb3cf4f92123621b7c7e647b181e9952112609c9a86c5970e5dc64a1dbe"
TESTS_SHA = hashlib.sha256(b"[[2,8,8],[-7,-3,-3],[4,4,4]]").hexdigest()
PATH = "/api/v1/agent-evaluations/profiles/java-verifiers"
KINDS = {"java-candidate": "java_candidate_ref", "java-registration": "java_registration_ref", "java-final": "java_final_ref"}
GOAL = "独立验证固定 Max.java 成果。"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def source_bytes(content, metadata):
    if (not isinstance(content, bytes) or not 0 < len(content) <= 65536
            or type(metadata["size"]) is not int or metadata["size"] != len(content)
            or metadata["path"] != "/Max.java" or metadata["digest"] != sha(content)):
        raise ValueError("Latest owned Max.java bytes/size/digest are required")
    text = content.decode("utf-8")
    patch = "".join(difflib.unified_diff(ORIGINAL.splitlines(keepends=True), text.splitlines(keepends=True),
                                        fromfile="original/Max.java", tofile="candidate/Max.java"))
    return {"source_utf8": text, "source_sha256": sha(content), "source_size": len(content),
            "original_sha256": sha(ORIGINAL.encode()), "diff_sha256": sha(patch.encode()), "changed": text != ORIGINAL}


def command(row):
    if (set(row) != {"exit_code", "timed_out", "truncated", "stdout_sha256", "stderr_sha256"}
            or type(row["exit_code"]) is not int or type(row["timed_out"]) is not bool
            or type(row["truncated"]) is not bool or not all(isinstance(row[key], str) and SHA.fullmatch(row[key])
                for key in ("stdout_sha256", "stderr_sha256"))):
        raise ValueError("Command receipt is missing or mistyped")
    return row["exit_code"] == 0 and not row["timed_out"] and not row["truncated"]


def protection(report):
    fixed = {"uid": 1000, "gid": 1000, "blocked": True, "comparator_unchanged": True, "cwd": "/workspace",
             "workspace_writable": True, "parent_replace_blocked": True}
    markers = {"schema_version": 1, "architecture": "amd64", "uid": 1000, "gid": 1000, "euid": 1000, "egid": 1000,
               "saved_uid": 1000, "saved_gid": 1000, "no_supplementary_groups": True, "no_new_privs": 1,
               "seccomp_mode": 2, "no_inherited_sockets": True, "native_getpid_allowed": True, "policy_sha256": LAUNCHER_SHA}
    isolation = report["isolation"]
    if digest(report["protection"]) != digest(fixed) or set(isolation) != {*markers, "denied_errno"}:
        raise ValueError("Protected comparator/isolation evidence is missing")
    if digest({key: isolation[key] for key in markers}) != digest(markers):
        raise ValueError("Protected child identity or policy differs")
    denied = dict.fromkeys(("inet", "inet6", "socketpair", "execd_loopback", "connect", "x32", "compat",
                           "syscall_425", "syscall_426", "syscall_427", "syscall_438"), 1)
    if digest(isolation["denied_errno"]) != digest(denied):
        raise ValueError("Actual denied-syscall probe is incomplete")


def protocol(report, source_sha256):
    if PROGRAM_SHA is None:
        raise ValueError("Draft only: signature-hardened program has not been frozen and reviewed")
    required = {"schema_version", "protocol", "source_sha256", "program_sha256", "tests_sha256", "protection", "isolation",
                "compile", "cases", "passed", "javac_version_sha256", "java_version_sha256"}
    if (set(report) not in (required, required | {"adapter_compile"}, required | {"adapter_compile", "class_protection"})
            or type(report["schema_version"]) is not int or report["schema_version"] != 1
            or report["protocol"] != "java-max-v2" or report["source_sha256"] != source_sha256
            or report["program_sha256"] != PROGRAM_SHA or report["tests_sha256"] != TESTS_SHA
            or type(report["passed"]) is not bool or any(not isinstance(report[key], str) or not SHA.fullmatch(report[key])
                for key in ("javac_version_sha256", "java_version_sha256"))):
        raise ValueError("Verifier protocol/source/program/test binding differs")
    protection(report)
    compiled = command(report["compile"])
    if "adapter_compile" in report:
        compiled = command(report["adapter_compile"]) and compiled
    elif compiled:
        raise ValueError("Missing adapter compilation receipt")
    if not isinstance(report["cases"], list) or len(report["cases"]) != (3 if compiled else 0):
        raise ValueError("Incomplete fixed test receipts")
    if compiled and digest(report.get("class_protection")) != digest({
            "adapter_blocked": True, "candidate_blocked": True, "digests_unchanged": True}):
        raise ValueError("Compiled class protection is incomplete")
    outcomes = []
    keys = {"exit_code", "timed_out", "truncated", "stdout_sha256", "stderr_sha256"}
    for index, row in enumerate(report["cases"]):
        if set(row) != {*keys, "case_index", "passed", "expected_sha256"}:
            raise ValueError("Unexpected test receipt fields")
        expected = sha(str(EXPECTED[index]).encode())
        passed = command({key: row[key] for key in keys}) and row["stdout_sha256"] == expected
        if (type(row["case_index"]) is not int or row["case_index"] != index or type(row["passed"]) is not bool
                or row["passed"] != passed or row["expected_sha256"] != expected):
            raise ValueError("Declared test outcome differs from actual exit/output receipts")
        outcomes.append(passed)
    passed = compiled and len(outcomes) == 3 and all(outcomes)
    if report["passed"] != passed:
        raise ValueError("Declared overall verdict differs from protected test receipts")
    return "PASS" if passed else "FAIL"


def native_usage(value, *, calls):
    keys = {"calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens"}
    expected = {"calls": calls, "reported_calls": calls, "unknown_calls": 0, "pending_calls": 0,
                "input_tokens": calls * 10, "output_tokens": calls * 5}
    if set(value) != keys or digest(value) != digest(expected):
        raise ValueError("Native candidate/verifier usage must be explicitly known and reconciled")
    return expected


def cleanup_observation(value, candidate, verifier):
    if digest(value["namespace_inventory"]) != digest({"complete": True, "count": 0}):
        return False
    rows = value["sessions"]
    return (value["complete"] is True and isinstance(rows, list) and len(rows) == 2
            and {row["run_id"] for row in rows} == {candidate, verifier}
            and len({row["session_id"] for row in rows}) == 2
            and len({row["sandbox_id"] for row in rows}) == 2
            and all(row["state"] == "RELEASED" and row["namespace"] == value["namespace"] for row in rows)
            and digest(value["case_provider"]) == digest({"observed": True, "complete": True,
                "calls": 3, "tokens": 45, "pending": 0, "unknown_roots": 0}))


def keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ValueError("Unexpected private W02 evidence fields")


def reference(root, task):
    root, task = str(UUID(root)), str(UUID(task))
    return {"mode": "work", "id": root, "fixture_id": task, "path": "/api/v1/work-runs/" + root}


def readiness(platform, specimen, *, capabilities=None):
    if PROGRAM_SHA is None:
        raise adapters.Blocked("Signature-hardened W02 program is not reviewed; no business admission")
    capabilities = capabilities if capabilities is not None else platform.get("/api/v1/agent-evaluations/profiles/capabilities")
    expected = {"protocol": "java-max-v2", "initialized": True, "program_sha256": PROGRAM_SHA,
                "launcher_sha256": LAUNCHER_SHA, "tests_sha256": TESTS_SHA, "observer": "java-verifier-owned-v1"}
    bound = capabilities.get("case_limits", {})
    if (digest(capabilities.get("java_verifier")) != digest(expected)
            or bound.get("schema_version") != 1 or type(bound.get("schema_version")) is not int
            or bound.get("enforcement") != "controlled-case-reserve-v1" or bound.get("scope") != "evaluation-owner-and-case"
            or digest(bound.get("profiles", {}).get("AET-W02")) != digest({"max_model_calls": 6, "max_tokens": 90, "max_roots": 2})
            or specimen.budgets.max_model_calls < 6 or specimen.budgets.max_tokens < 90):
        raise adapters.Blocked("Actual fixed service, observer and whole-case W02 budget proof are required")


def retain(journal, attempt, kind, body):
    try:
        value = {"schema_version": 1, "kind": kind, "binding": binding(journal.data, attempt), "body": body}
        ref = store(journal.directory, kind, value)
        attempt.setdefault("evidence_refs", []).append(ref)
        attempt[KINDS[kind]] = ref
        journal.save()
        return body
    except BaseException:
        attempt["java_evidence_failed"] = True
        raise


def retained(directory, data, attempt, kind):
    if attempt.get("java_evidence_failed"):
        raise adapters.Blocked("W02 evidence persistence failed; no new effects or cleanup")
    value = load(directory, attempt[KINDS[kind]], kind)
    keys(value, "schema_version kind binding body")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != kind or digest(value["binding"]) != digest(binding(data, attempt)):
        raise ValueError("W02 evidence belongs to another immutable attempt")
    return value["body"]


def events(platform, root):
    cursor, projected = 0, []
    for _ in range(11):
        page = platform.get(root["path"] + "/events", params={"after": cursor})
        if not isinstance(page.get("items"), list) or len(page["items"]) > 100 or type(page.get("next_after")) is not int:
            raise adapters.Blocked("Complete native Work event pages are required")
        if not page["items"]:
            if page["next_after"] != cursor:
                raise adapters.Blocked("Native Work event tail is unobserved")
            return projected
        for row in page["items"]:
            if type(row["event_id"]) is not int or row["event_id"] != cursor + 1 or len(projected) >= 1000:
                raise adapters.Blocked("Native Work events are incomplete")
            item = {"event_id": row["event_id"], "event": row["event"]}
            if row["event"] in {"tool.requested", "tool.completed"}:
                payload = row["data"]
                if payload["name"] not in {"read_file", "edit_file"} or not re.fullmatch(r"ae-[a-f0-9]{16}", payload["call_id"]):
                    raise adapters.Blocked("Unexpected W02 tool event")
                item.update(name=payload["name"], call_id=payload["call_id"])
                if row["event"] == "tool.completed":
                    if not isinstance(payload["output"], str) or len(payload["output"]) > 8000 or payload["truncated"] is not False:
                        raise adapters.Blocked("Tool result observation is incomplete")
                    item.update(output_sha256=sha(payload["output"].encode()), truncated=False)
            projected.append(item)
            cursor = row["event_id"]
        if page["next_after"] != cursor:
            raise adapters.Blocked("Native Work event cursor differs")
    raise adapters.Blocked("Work event history exceeds its fixed bound")


def collect_root(platform, attempt, root, *, verifier=False):
    proof, row = platform.attest(), platform.get(root["path"])
    if (root != reference(root["id"], root["fixture_id"]) or row["id"] != root["id"] or row["task_id"] != root["fixture_id"]
            or row["owner_id"] != attempt["owner_id"] or row["model_id"] != attempt["fixture"]["model_id"]
            or row["environment"] != "coding" or row["status"] != "COMPLETED"
            or proof["sandbox_namespace"] != attempt["sandbox_namespace"]):
        raise ValueError("W02 root identity, model or terminal state differs")
    usage = platform.get(root["path"] + "/usage")["total"]
    native_usage(usage, calls=0 if verifier else 3)
    operations = platform.get(root["path"] + "/operations")
    if (not isinstance(operations.get("items"), list) or len(operations["items"]) > 1000
            or operations.get("next_after") or operations.get("next_before")):
        raise adapters.Blocked("Complete operation evidence is required")
    ops = [{key: item[key] for key in ("id", "name", "status")} for item in operations["items"]]
    if ops:
        raise adapters.Blocked("The fixed read/edit and SDK verifier paths must not create operation receipts")
    trace = platform.get("/api/v1/resource-runs/work/" + root["id"] + "/configurations")
    if trace["configuration_scope"] != {"mode": "work", "run_id": root["id"]} or len(trace["items"]) != 1:
        raise ValueError("W02 must have only its exact used/resolved model scope")
    model = trace["items"][0]
    called = [{key: item[key] for key in ("call_id", "run_id", "mode", "model_id", "model_revision", "status", "input_tokens", "output_tokens")}
              for item in model["calls"]]
    result = {"run_id": row["id"], "task_id": row["task_id"], "owner_id": row["owner_id"], "model_id": row["model_id"],
              "status": row["status"], "environment": row["environment"], "task_revision": row["task_revision"], "usage": usage, "operations": ops,
              "events": events(platform, root), "model": {"resource_kind": model["resource_kind"], "resource_id": model["resource_id"],
                "snapshot_id": model["snapshot_id"], "revision": model["revision"], "used_at": model["used_at"], "calls": called},
              "result": row["result"] if verifier else {"snapshot_id": row["result"]["snapshot_id"]}}
    return result


def collect_candidate(platform, attempt):
    root = collect_root(platform, attempt, attempt["platform_root"])
    page = platform.get(attempt["platform_root"]["path"] + "/artifacts")
    if not isinstance(page.get("items"), list) or len(page["items"]) > 100 or page.get("next_before") or page.get("next_after"):
        raise adapters.Blocked("Complete latest artifacts are required")
    selected = [row for row in page["items"] if row["path"] == "/Max.java"]
    if len(selected) != 1:
        raise adapters.Blocked("One actual latest Max.java artifact is required")
    artifact = selected[0]
    identifier = str(UUID(artifact["id"]))
    payload = bytearray()
    with platform.http.stream("GET", "/api/v1/work-artifacts/" + identifier + "/download") as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(chunk_size=65537):
            if len(payload) + len(chunk) > 65536:
                raise adapters.Blocked("Candidate source exceeds64KiB")
            payload.extend(chunk)
    return {"root": root, "artifact_id": identifier, **source_bytes(bytes(payload), artifact)}


def posted(attempt, key, path, payload, run_id):
    intent = attempt.get("submission_intents", {}).get(key, {})
    if (intent.get("status") != "CONFIRMED" or intent.get("path") != path or intent.get("payload_digest") != intent_digest(payload)
            or intent.get("response", {}).get("run_id") != run_id):
        raise adapters.Blocked("Original Work submission is unconfirmed or belongs to another root")


def fixture_intent(attempt, key, path, body, *, returned):
    intent = attempt.get("fixture_intents", {}).get(key, {})
    expected = intent_digest({"method": "POST", "path": path, "body": body, "content": None, "params": None, "files": []})
    if (intent.get("status") != "CONFIRMED" or intent.get("method") != "POST" or intent.get("path") != path
            or intent.get("digest") != expected or digest(intent.get("response")) != digest(returned)
            or type(intent.get("http_status")) is not int or intent["http_status"] != (201 if path == "/api/v1/work-tasks" else 200)):
        raise adapters.Blocked("Original verifier fixture/seal intent is unconfirmed or changed")


def payload(specimen, attempt, revision):
    return {"model_id": attempt["fixture"]["model_id"], "revision": revision, "environment": "coding",
            "step_limit": specimen.budgets.max_model_calls, "token_limit": specimen.budgets.max_tokens}


def observer_binding(candidate, attempt, verifier_run=None):
    return {"source_run_id": candidate["root"]["run_id"], "source_task_id": candidate["root"]["task_id"],
            "source_snapshot_id": candidate["root"]["result"]["snapshot_id"], "source_model_id": attempt["fixture"]["model_id"],
            "artifact_id": candidate["artifact_id"], "source_sha256": candidate["source_sha256"],
            "verifier_task_id": attempt["java_verifier_task"], "verifier_run_id": verifier_run,
            "program_sha256": PROGRAM_SHA, "launcher_sha256": LAUNCHER_SHA, "tests_sha256": TESTS_SHA}


def observer(value, candidate, attempt, *, final=False):
    keys(value, "schema_version case_id environment_id owner_id namespace binding phase complete zero_verifier native sessions namespace_inventory case_provider")
    expected = observer_binding(candidate, attempt, attempt["java_verifier_root"]["id"] if final else None)
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["case_id"] != "AET-W02"
            or value["environment_id"] != attempt["environment_id"] or value["owner_id"] != attempt["owner_id"]
            or value["namespace"] != attempt["sandbox_namespace"] or digest(value["binding"]) != digest(expected)
            or value["phase"] != ("COMPLETED" if final else "REGISTERED")):
        raise ValueError("Verifier ledger, consumer phase or sealed source identity differs")
    keys(value["native"], "source verifier")
    native_usage(value["native"]["source"], calls=3)
    expected_zero = {"observed": final, "confirmed": final}
    if digest(value["zero_verifier"]) != digest(expected_zero):
        raise adapters.Blocked("Missing provider root is not a confirmed zero-model executor")
    if final:
        native_usage(value["native"]["verifier"], calls=0)
    elif value["native"]["verifier"] is not None:
        raise ValueError("A not-yet-admitted verifier cannot have fabricated zero native usage")
    expected_provider = {"observed": True, "complete": True, "calls": 3, "tokens": 45, "pending": 0, "unknown_roots": 0}
    if digest(value["case_provider"]) != digest(expected_provider) or value["complete"] is not True:
        raise adapters.Blocked("Whole-case provider/consumer proof is incomplete or unknown")
    if digest(value["namespace_inventory"]) != digest({"complete": True, "count": 0}):
        raise adapters.Blocked("Actual registered namespace is not confirmed empty")
    sessions = value["sessions"]
    if not isinstance(sessions, list) or len(sessions) != (2 if final else 1):
        raise adapters.Blocked("Exact source/verifier sessions must be observed")
    run_ids = {candidate["root"]["run_id"]} | ({attempt["java_verifier_root"]["id"]} if final else set())
    for row in sessions:
        keys(row, "session_id run_id owner_id namespace sandbox_id state")
        for field in ("session_id", "run_id", "owner_id", "namespace"):
            UUID(row[field])
        if not isinstance(row["sandbox_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", row["sandbox_id"]):
            raise ValueError("A bounded actual sandbox identifier is required")
        if row["owner_id"] != attempt["owner_id"] or row["namespace"] != value["namespace"] or row["state"] != "RELEASED":
            raise adapters.Blocked("Actual source/verifier session remains unconfirmed")
    if ({row["run_id"] for row in sessions} != run_ids or len({row["session_id"] for row in sessions}) != len(sessions)
            or len({row["sandbox_id"] for row in sessions}) != len(sessions)):
        raise ValueError("Session/sandbox association is duplicated or belongs to another root")
    return value


def validate_root(root, attempt, *, verifier=False):
    keys(root, "run_id task_id owner_id model_id status environment task_revision usage operations events model result")
    expected = attempt["java_verifier_root"] if verifier else attempt["platform_root"]
    if (root["run_id"] != expected["id"] or root["task_id"] != expected["fixture_id"] or root["owner_id"] != attempt["owner_id"]
            or root["model_id"] != attempt["fixture"]["model_id"] or root["status"] != "COMPLETED" or root["environment"] != "coding"
            or type(root["task_revision"]) is not int or root["task_revision"] != (1 if verifier else 2)):
        raise ValueError("Source model/task/revision or verifier root changed")
    native_usage(root["usage"], calls=0 if verifier else 3)
    model = root["model"]
    keys(model, "resource_kind resource_id snapshot_id revision used_at calls")
    UUID(model["snapshot_id"])
    if (model["resource_kind"] != "model" or model["resource_id"] != root["model_id"] or type(model["revision"]) is not str
            or not re.fullmatch(r"[1-9][0-9]{0,18}", model["revision"])):
        raise ValueError("Native model identity does not match the recorded reference revision")
    if (not isinstance(model["calls"], list) or len(model["calls"]) != (0 if verifier else 3)
            or (model["used_at"] is None) != verifier):
        raise adapters.Blocked("Actual model call provenance is missing or zero-model executor invoked a model")
    call_ids = []
    for row in model["calls"]:
        keys(row, "call_id run_id mode model_id model_revision status input_tokens output_tokens")
        UUID(row["call_id"])
        call_ids.append(row["call_id"])
        if type(row["model_revision"]) is not int or row["model_revision"] <= 0 or str(row["model_revision"]) != model["revision"]:
            raise ValueError("Actual integer model revision differs from its canonical text reference")
        expected_call = {"call_id": row["call_id"], "run_id": root["run_id"], "mode": "work", "model_id": root["model_id"],
                         "model_revision": row["model_revision"], "status": "KNOWN", "input_tokens": 10, "output_tokens": 5}
        if digest(row) != digest(expected_call):
            raise ValueError("Native usage belongs to a different model, mode or root")
    if len(set(call_ids)) != len(call_ids):
        raise ValueError("Duplicate actual native call identity")
    if root["operations"] != []:
        raise adapters.Blocked("These two fixed executors require an explicitly observed empty operation collection")
    if not isinstance(root["events"], list) or not 2 <= len(root["events"]) <= 1000:
        raise ValueError("Complete event evidence is required")
    requested, completed, tool_sequence = [], [], []
    allowed = {"run.started", "run.completed"} | (set() if verifier else {"tool.requested", "tool.completed", "message.delta", "step.updated", "plan.updated"})
    for index, row in enumerate(root["events"], 1):
        if type(row["event_id"]) is not int or row["event_id"] != index or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", row["event"]):
            raise ValueError("Native Work event sequence is incomplete")
        if row["event"] not in allowed:
            raise adapters.Blocked("Unexpected event outside the fixed source/verifier path")
        fields = "event_id event"
        if row["event"] in {"tool.requested", "tool.completed"}:
            fields += " name call_id"
            if row["name"] not in {"read_file", "edit_file"} or not re.fullmatch(r"ae-[a-f0-9]{16}", row["call_id"]):
                raise ValueError("Unexpected source tool declaration")
            (requested if row["event"] == "tool.requested" else completed).append((row["name"], row["call_id"]))
            tool_sequence.append((row["event"], row["name"], row["call_id"]))
            if row["event"] == "tool.completed":
                fields += " output_sha256 truncated"
                if row["truncated"] is not False or not SHA.fullmatch(row["output_sha256"]):
                    raise adapters.Blocked("Tool completion was not fully observed")
        keys(row, fields)
    names = [row["event"] for row in root["events"]]
    if (names.count("run.started") != 1 or names.count("run.completed") != 1 or names[-1] != "run.completed"
            or any(name in names for name in ("run.failed", "run.cancelled", "run.interrupted"))
            or requested != completed or [name for name, _ in requested] != ([] if verifier else ["read_file", "edit_file"])):
        raise adapters.Blocked("Actual queue/tool lifecycle differs from the fixed source or zero-model verifier")
    expected_sequence = [(event, name, call_id) for name, call_id in requested for event in ("tool.requested", "tool.completed")]
    if len({call_id for _, call_id in requested}) != len(requested) or tool_sequence != expected_sequence:
        raise adapters.Blocked("Fixed read must complete before edit is requested, with unique call IDs")


def validate_candidate(value, attempt):
    keys(value, "root artifact_id source_utf8 source_sha256 source_size original_sha256 diff_sha256 changed")
    validate_root(value["root"], attempt)
    UUID(value["artifact_id"])
    projected = source_bytes(value["source_utf8"].encode("utf-8"), {
        "path": "/Max.java", "size": value["source_size"], "digest": value["source_sha256"]})
    if digest({key: value[key] for key in projected}) != digest(projected):
        raise ValueError("Retained original/candidate source or actual diff differs")
    UUID(value["root"]["result"]["snapshot_id"])


def validate_intents(attempt, specimen, candidate, *, final=False, registered=False):
    intents = [*attempt.get("fixture_intents", {}).values(), *attempt.get("submission_intents", {}).values()]
    if any(row.get("status") != "CONFIRMED" for row in intents):
        raise adapters.Blocked("Unknown/unconfirmed original intent cannot authorize another task or CLEAN")
    root, fixture = attempt["platform_root"], attempt["fixture"]
    if type(fixture["revision"]) is not int or fixture["revision"] != 2 or root != reference(root["id"], fixture["id"]):
        raise ValueError("Candidate must use its actual uploaded revision2")
    posted(attempt, "submit-run", f"/api/v1/work-tasks/{fixture['id']}/runs", payload(specimen, attempt, 2), root["id"])
    uploaded = attempt.get("fixture_intents", {}).get("input-0", {})
    expected = intent_digest({"method": "POST", "path": f"/api/v1/work-tasks/{fixture['id']}/files", "body": None,
                              "content": sha(ORIGINAL.encode()), "params": {"path": "/Max.java", "revision": 1}, "files": []})
    if (uploaded.get("method") != "POST" or uploaded.get("path") != f"/api/v1/work-tasks/{fixture['id']}/files"
            or uploaded.get("digest") != expected or type(uploaded.get("http_status")) is not int or uploaded["http_status"] != 201
            or uploaded.get("response", {}).get("revision") != 2 or type(uploaded.get("response", {}).get("revision")) is not int):
        raise ValueError("Original fixed source upload/revision was not confirmed")
    if not (final or registered):
        return
    task = attempt["java_verifier_task"]
    create_body = {"title": "AE " + attempt["attempt_id"][:12] + " Java verifier", "goal": GOAL}
    fixture_intent(attempt, "java-verifier-task", "/api/v1/work-tasks", create_body, returned={"id": task})
    seal = {"task_id": task, "source_run_id": root["id"], "artifact_id": candidate["artifact_id"], "source_sha256": candidate["source_sha256"]}
    fixture_intent(attempt, "register-java-verifier", PATH, seal, returned={"task_id": task, "sealed": True})
    if not final:
        return
    child = attempt["java_verifier_root"]
    if child != reference(child["id"], task) or child["id"] == root["id"] or task == fixture["id"]:
        raise ValueError("Verifier requires a separate owned task/root")
    posted(attempt, "submit-java-verifier", f"/api/v1/work-tasks/{task}/runs", payload(specimen, attempt, 1), child["id"])


def verified(directory, data, attempt, specimen):
    if attempt.get("java_evidence_failed") or PROGRAM_SHA is None:
        raise adapters.Blocked("Unconfirmed evidence or program prevents W02 acceptance")
    if attempt["owner_id"] != data["owner_id"] or attempt["environment_id"] != data["environment_id"]:
        raise ValueError("Attempt and retained target identity differ")
    refs = [attempt.get(slot) for slot in KINDS.values()]
    if any(ref is None for ref in refs) or digest(attempt.get("evidence_refs")) != digest(refs):
        raise ValueError("Retained source/registration/final reference order differs")
    candidate = retained(directory, data, attempt, "java-candidate")
    registration = retained(directory, data, attempt, "java-registration")
    final = retained(directory, data, attempt, "java-final")
    keys(final, "root observer")
    snapshot = {"candidate": candidate, "registration": registration, "verifier": final["root"], "observer": final["observer"],
                "usage": {key: candidate["root"]["usage"][key] + final["root"]["usage"][key] for key in candidate["root"]["usage"]}}
    evaluate(snapshot, specimen, attempt)
    return snapshot


def evaluate(snapshot, specimen, attempt):
    keys(snapshot, "candidate registration verifier observer usage")
    candidate, verifier = snapshot["candidate"], snapshot["verifier"]
    validate_candidate(candidate, attempt)
    validate_root(verifier, attempt, verifier=True)
    validate_intents(attempt, specimen, candidate, final=True)
    observer(snapshot["registration"], candidate, attempt)
    observer(snapshot["observer"], candidate, attempt, final=True)
    grade = protected_verdict(verifier, candidate)
    native_usage(snapshot["usage"], calls=3)
    adapters.known_usage({"observed": ["usage"], "usage": {"total": snapshot["usage"]}}, specimen)
    facts = [Result(registered_id="protected_tests", status=grade,
                    reason="Required actual source diff, protected receipts and sealed zero-model proof are evaluated together")]
    return facts, {"run": {"status": verifier["status"]}, "observed": ["usage"], "usage": {"total": snapshot["usage"]},
        "business_outcomes": [{"kind": "candidate", "run_id": candidate["root"]["run_id"], "status": candidate["root"]["status"]},
                              {"kind": "protected-verifier", "run_id": verifier["run_id"], "status": verifier["status"], "task_outcome": grade}]}


def protected_verdict(verifier, candidate):
    result = verifier["result"]
    keys(result, "case_id verifier_run_id source_run_id artifact_id source_sha256 verification")
    if (result["case_id"] != "AET-W02" or result["verifier_run_id"] != verifier["run_id"]
            or result["source_run_id"] != candidate["root"]["run_id"] or result["artifact_id"] != candidate["artifact_id"]
            or result["source_sha256"] != candidate["source_sha256"]):
        raise ValueError("Protected result belongs to another artifact or verifier")
    grade = protocol(result["verification"], candidate["source_sha256"])
    if candidate["changed"] is not True or candidate["source_sha256"] == candidate["original_sha256"] or candidate["diff_sha256"] == sha(b""):
        grade = "FAIL"
    return grade


def known_failure(journal, attempt, reason):
    attempt["fact_results"] = [Result(registered_id="protected_tests", status="FAIL", reason=reason).model_dump()]
    attempt["task_outcome"] = "FAIL"
    journal.save()


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    if attempt.get("java_evidence_failed"):
        raise adapters.Blocked("Evidence failure prohibits further Work/verification requests")
    pending = network.ACTIVE.data["session_id"] if network.ACTIVE else None
    fixed_audit.validate(journal.directory, journal.data, pending_session=pending)
    readiness(platform, specimen)
    if attempt.get("java_final_ref"):
        return verified(journal.directory, journal.data, attempt, specimen)
    attempt.setdefault("java_started_at", datetime.now(UTC).isoformat())
    journal.save()

    def remaining():
        value = specimen.budgets.timeout_seconds - max(0, (datetime.now(UTC) - datetime.fromisoformat(attempt["java_started_at"])).total_seconds())
        if value <= 0 or cancelled():
            raise adapters.Blocked("W02 whole-case time/cancellation budget exhausted")
        return value

    platform.expected_sandbox_namespace = attempt["sandbox_namespace"]
    platform.attest()
    if not attempt.get("submission_complete"):
        remaining()
        adapters.submit(platform, journal, attempt, specimen, attempt["fixture"])
    if not attempt.get("java_candidate_ref"):
        bounded = specimen.model_copy(update={"budgets": specimen.budgets.model_copy(update={"timeout_seconds": remaining()})})
        adapters.observe(platform, journal, attempt, bounded, cancelled=cancelled)
        retain(journal, attempt, "java-candidate", collect_candidate(platform, attempt))
    candidate = retained(journal.directory, journal.data, attempt, "java-candidate")
    validate_candidate(candidate, attempt)
    validate_intents(attempt, specimen, candidate)
    if candidate["changed"] is not True or candidate["diff_sha256"] == sha(b""):
        known_failure(journal, attempt, "Actual Max.java source did not change")
    fixtures = FixedFixtures(platform, journal, attempt)
    if not attempt.get("java_registration_ref"):
        remaining()
        task = fixtures.create("java-verifier-task", "/api/v1/work-tasks", {
            "title": "AE " + attempt["attempt_id"][:12] + " Java verifier", "goal": GOAL})
        attempt["java_verifier_task"] = task
        journal.save()
        empty = platform.get(f"/api/v1/work-tasks/{task}/files")
        if digest(empty) != digest({"revision": 1, "items": []}):
            raise ValueError("The second task must be separate, empty and revision1")
        remaining()
        fixtures.mutate("register-java-verifier", "POST", PATH, body={"task_id": task,
            "source_run_id": candidate["root"]["run_id"], "artifact_id": candidate["artifact_id"], "source_sha256": candidate["source_sha256"]})
        current = platform.get(PATH + "/" + task + "/observations")
        observer(current, candidate, attempt)
        retain(journal, attempt, "java-registration", current)
    observer(retained(journal.directory, journal.data, attempt, "java-registration"), candidate, attempt)
    validate_intents(attempt, specimen, candidate, registered=True)
    if "submit-java-verifier" not in attempt.get("submission_intents", {}):
        current = platform.get(PATH + "/" + attempt["java_verifier_task"] + "/observations")
        observer(current, candidate, attempt)
    remaining()
    child = journal.post(platform, attempt, f"/api/v1/work-tasks/{attempt['java_verifier_task']}/runs",
                         payload(specimen, attempt, 1), "submit-java-verifier")
    attempt["java_verifier_root"] = reference(child["run_id"], attempt["java_verifier_task"])
    journal.save()
    validate_intents(attempt, specimen, candidate, final=True)
    while True:
        platform.attest()
        row = adapters.read_run(platform, attempt["java_verifier_root"])
        if row["status"] in adapters.TERMINAL:
            break
        try:
            remaining()
        except adapters.Blocked:
            journal.post(platform, attempt, attempt["java_verifier_root"]["path"] + "/cancel", {}, "cancel-java-verifier")
            raise
        time.sleep(0.25)
    root = collect_root(platform, attempt, attempt["java_verifier_root"], verifier=True)
    validate_root(root, attempt, verifier=True)
    if protected_verdict(root, candidate) == "FAIL":
        known_failure(journal, attempt, "Confirmed protected result or required source diff failed")
    current = platform.get(PATH + "/" + attempt["java_verifier_task"] + "/observations")
    observer(current, candidate, attempt, final=True)
    retain(journal, attempt, "java-final", {"root": root, "observer": current})
    return verified(journal.directory, journal.data, attempt, specimen)


def cleanup_ready(directory, data, attempt, specimen):
    value = verified(directory, data, attempt, specimen)
    return cleanup_observation(value["observer"], attempt["platform_root"]["id"], attempt["java_verifier_root"]["id"])


def cleanup(platform, journal, attempt, specimen):
    snapshot = verified(journal.directory, journal.data, attempt, specimen)
    current = platform.get(PATH + "/" + attempt["java_verifier_task"] + "/observations")
    observer(current, snapshot["candidate"], attempt, final=True)
    return "CLEAN" if digest(current) == digest(snapshot["observer"]) and cleanup_ready(journal.directory, journal.data, attempt, specimen) else "NEEDS_REVIEW"
