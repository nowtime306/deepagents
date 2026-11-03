"""A01 retained lifecycle: actual Agent-created draft, explicit enable and real occurrence."""

import time
from datetime import UTC, datetime
from uuid import UUID, uuid5

from . import adapters
from . import fixed_automation_observation as observation
from .contracts import Result
from .fixed_evidence import binding, load, store, text_digest
from .fixed_fixtures import FixedFixtures
from .journal import digest

KINDS = {"a01-initialization", "a01-draft", "a01-enabled", "a01-dispatched", "a01-facts"}
SLOTS = {"a01-initialization": "automation_initialization_ref", "a01-draft": "automation_draft_ref",
         "a01-enabled": "automation_enabled_ref", "a01-dispatched": "automation_dispatched_ref", "a01-facts": "automation_final_ref"}
LIMITS = {"max_model_calls": 8, "max_tokens": 120, "max_roots": 2}


def enforce_case_budget(capabilities, specimen):
    proof = capabilities.get("case_limits") or {}
    limits = proof.get("profiles", {}).get(specimen.case_id, {})
    if (type(proof.get("schema_version")) is not int or proof["schema_version"] != 1
            or proof.get("enforcement") != "controlled-case-reserve-v1" or proof.get("scope") != "evaluation-owner-and-case"
            or digest(limits) != digest(LIMITS) or specimen.budgets.max_model_calls < limits["max_model_calls"]
            or specimen.budgets.max_tokens < limits["max_tokens"]):
        raise adapters.Blocked("Attested whole-case Ask+occurrence budget enforcement is required")


def persist(journal, attempt, kind, body):
    value = {"schema_version": 1, "kind": kind, "binding": binding(journal.data, attempt), "body": body}
    try:
        reference = store(journal.directory, kind, value)
        attempt.setdefault("evidence_refs", []).append(reference)
        attempt[SLOTS[kind]] = reference
        journal.save()
    except BaseException:
        attempt["automation_evidence_failed"] = True
        raise
    return reference


def read(directory, data, attempt, reference):
    kind = reference["kind"]
    if kind not in KINDS or attempt.get("automation_evidence_failed"):
        raise adapters.Blocked("A01 retained evidence is unavailable")
    value = load(directory, reference, kind)
    if (set(value) != {"schema_version", "kind", "binding", "body"} or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["kind"] != kind
            or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("A01 evidence identity changed")
    return value["body"]


def initialize(platform, journal, attempt, specimen):
    fixture = attempt["fixture"]
    if attempt.get("automation_initialization_ref"):
        body = read(journal.directory, journal.data, attempt, attempt["automation_initialization_ref"])
    else:
        versions = platform.get(f"/api/v1/agents/{fixture['agent_id']}/versions")["items"]
        if len(versions) != 1:
            raise adapters.Blocked("The fixed A01 Agent must have its original publication only")
        version = versions[0]
        model = next(row for row in platform.get("/api/v1/admin/models")["items"] if row["id"] == fixture["model_id"])
        body = {"agent_id": fixture["agent_id"], "agent_version_id": version["id"], "version": version["version"],
                "model_id": version["snapshot"]["model_id"], "published_model_revision": version["snapshot"]["model_revision"],
                "current_model_revision": model["revision"], "temperature": model["temperature"], "conversation_id": fixture["id"]}
        attempt["automation_initialization_ref"] = persist(journal, attempt, "a01-initialization", body)
        journal.save()
    validate_initialization(body, attempt)


def validate_initialization(body, attempt):
    fixture = attempt["fixture"]
    expected = {"agent_id": fixture["agent_id"], "agent_version_id": fixture["agent_version_id"], "version": 1,
                "model_id": fixture["model_id"], "published_model_revision": 1, "current_model_revision": 2,
                "temperature": 0.4, "conversation_id": fixture["id"]}
    if digest(body) != digest(expected):
        raise ValueError("A01 original publication and actual model maintenance were not retained")


def validate_intents(attempt, specimen, stage):
    fixture, root = attempt["fixture"], attempt["platform_root"]
    run_id = observation.identifier(root["id"])
    if digest(root) != digest({"mode": "ask", "id": run_id, "fixture_id": fixture["id"], "path": "/api/v1/runs/" + run_id}):
        raise ValueError("A01 original submitted root binding changed")
    submitted = attempt.get("submission_intents", {}).get("submit-run", {})
    if (submitted.get("status") != "CONFIRMED" or submitted.get("path") != f"/api/v1/conversations/{fixture['id']}/runs"
            or submitted.get("payload_digest") != digest({"message": specimen.input, "model_id": fixture["model_id"]})
            or submitted.get("response", {}).get("run_id") != run_id):
        raise adapters.Blocked("A01 original Ask submission was not confirmed with its exact root/input")
    actions = [("enable-automation", "PUT", f"/api/v1/automations/{attempt['automation_id']}/state", {"enabled": True}),
               ("step-automation", "POST", f"/api/v1/agent-evaluations/profiles/automations/{attempt['automation_id']}/step", {"source_run_id": run_id})]
    for key, method, path, body in actions[:0 if stage == "draft" else 1 if stage == "enabled" else 2]:
        intent = attempt.get("fixture_intents", {}).get(key, {})
        expected = digest({"method": method, "path": path, "body": body, "content": None, "params": None, "files": []})
        if (intent.get("status") != "CONFIRMED" or intent.get("method") != method or intent.get("path") != path
                or intent.get("digest") != expected or type(intent.get("http_status")) is not int or intent["http_status"] != 200):
            raise adapters.Blocked("A01 lifecycle write was not confirmed with its exact source/payload")


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    if attempt.get("automation_evidence_failed"):
        raise adapters.Blocked("A01 evidence persistence failed; manual review required")
    if any(row["kind"] == "a01-facts" for row in attempt.get("evidence_refs", [])):
        return verified(journal.directory, journal.data, attempt, specimen)
    attempt.setdefault("automation_started_at", datetime.now(UTC).isoformat())
    journal.save()
    def remaining():
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(attempt["automation_started_at"])).total_seconds()
        seconds = specimen.budgets.timeout_seconds - max(0, elapsed)
        if seconds <= 0:
            raise adapters.Blocked("Whole-case automation deadline exhausted; no further dispatch")
        return seconds
    initialization = read(journal.directory, journal.data, attempt, attempt["automation_initialization_ref"])
    validate_initialization(initialization, attempt)
    if not attempt.get("submission_complete"):
        remaining()
        adapters.submit(platform, journal, attempt, specimen, attempt["fixture"])
    if not attempt.get("automation_draft_ref"):
        bounded = specimen.model_copy(update={"budgets": specimen.budgets.model_copy(update={"timeout_seconds": remaining()})})
        adapters.observe(platform, journal, attempt, bounded, cancelled=cancelled)
        root = attempt["platform_root"]
        original = observation.root(platform, attempt["fixture"], specimen, root["id"], root["fixture_id"])
        schedule = observation.schedules(platform)
        attempt["automation_id"] = schedule["id"]
        body = {"initialization": initialization, "source": original, "schedule": schedule, "occurrences": observation.occurrences(platform, schedule["id"]),
                "profile": observation.profile(platform, schedule["id"], root["id"])}
        attempt["automation_draft_ref"] = persist(journal, attempt, "a01-draft", body)
        journal.save()
    draft = read(journal.directory, journal.data, attempt, attempt["automation_draft_ref"])
    validate_draft(draft, journal.data, attempt, specimen)
    fixtures = FixedFixtures(platform, journal, attempt)
    automation, source = attempt["automation_id"], attempt["platform_root"]["id"]
    if cancelled():
        raise adapters.Blocked("Cancelled before enabling the actual draft")
    if not attempt.get("automation_enabled_ref"):
        remaining()
        fixtures.mutate("enable-automation", "PUT", f"/api/v1/automations/{automation}/state", body={"enabled": True})
        body = {"schedule": observation.schedules(platform, automation), "occurrences": observation.occurrences(platform, automation),
                "profile": observation.profile(platform, automation, source)}
        attempt["automation_enabled_ref"] = persist(journal, attempt, "a01-enabled", body)
        journal.save()
    enabled = read(journal.directory, journal.data, attempt, attempt["automation_enabled_ref"])
    validate_enabled(enabled, journal.data, attempt, specimen)
    if cancelled():
        raise adapters.Blocked("Cancelled before fixed single-schedule dispatch")
    remaining()
    fixtures.mutate("step-automation", "POST", f"/api/v1/agent-evaluations/profiles/automations/{automation}/step",
                    body={"source_run_id": source})
    if not attempt.get("automation_dispatched_ref"):
        body = {"schedule": observation.schedules(platform, automation), "occurrences": observation.occurrences(platform, automation),
                "profile": observation.profile(platform, automation, source)}
        attempt["automation_dispatched_ref"] = persist(journal, attempt, "a01-dispatched", body)
        journal.save()
    dispatched = read(journal.directory, journal.data, attempt, attempt["automation_dispatched_ref"])
    occurrence = single_occurrence(dispatched, attempt)
    reference = {"mode": "ask", "id": occurrence["ask_run_id"], "fixture_id": occurrence["target_id"],
                 "path": "/api/v1/runs/" + occurrence["ask_run_id"]}
    attempt["scheduled_root"] = reference
    journal.save()
    deadline = time.monotonic() + remaining()
    while True:
        platform.attest()
        row = adapters.read_run(platform, reference)
        if row.get("status") in adapters.TERMINAL:
            break
        if cancelled() or time.monotonic() >= deadline:
            journal.post(platform, attempt, reference["path"] + "/cancel", {}, "cancel-occurrence")
            raise adapters.Blocked("Occurrence cancelled or timed out; terminal observation still required")
        time.sleep(0.25)
    final = observation.root(platform, attempt["fixture"], specimen, reference["id"], reference["fixture_id"], occurrence_id=occurrence["id"])
    body = {"draft": draft, "enabled": enabled, "dispatched": dispatched, "execution": final,
            "schedule": observation.schedules(platform, automation), "occurrences": observation.occurrences(platform, automation),
            "profile": observation.profile(platform, automation, source)}
    persist(journal, attempt, "a01-facts", body)
    return verified(journal.directory, journal.data, attempt, specimen)


def require_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError("Unexpected A01 retained projection fields")


def validate_profile(value, data, attempt):
    require_keys(value, "schema_version environment_id owner_id namespace automation_id source_run_id agent_version_id enabled next_due_at completed_at revision budget occurrences native provider counts")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["environment_id"] != data["environment_id"] or value["owner_id"] != data["owner_id"]
            or value["namespace"] != attempt["sandbox_namespace"] or value["automation_id"] != attempt["automation_id"]
            or value["source_run_id"] != attempt["platform_root"]["id"]
            or value["agent_version_id"] != attempt["fixture"]["agent_version_id"]):
        raise ValueError("A01 profile scope differs")
    if (type(value["enabled"]) is not bool or type(value["revision"]) is not int
            or digest(value["budget"]) != digest({"model_calls": 4, "token_limit": 10000})):
        raise ValueError("A01 profile policy is not typed")
    require_keys(value["counts"], "active_ask_runs work_runs unsettled_usage")
    if any(type(count) is not int or count < 0 for count in value["counts"].values()):
        raise ValueError("A01 counters must be explicitly observed integers")
    provider = value["provider"]
    require_keys(provider, "observed complete calls tokens pending unknown_roots roots")
    if (provider["observed"] is not True or type(provider["complete"]) is not bool
            or any(type(provider[key]) is not int or provider[key] < 0 for key in ("calls", "tokens", "pending", "unknown_roots"))
            or not isinstance(provider["roots"], list) or len(provider["roots"]) > 2):
        raise ValueError("A01 provider observation is incomplete")
    for root in provider["roots"]:
        require_keys(root, "mode run_id phase calls tokens")
        observation.identifier(root["run_id"])
        if root["mode"] != "ask" or root["phase"] not in {"draft", "occurrence"} or any(
                type(root[key]) is not int or root[key] < 0 for key in ("calls", "tokens")):
            raise ValueError("A01 provider root is not registered")
    if not isinstance(value["native"], list) or not 1 <= len(value["native"]) <= 2:
        raise adapters.Blocked("Actual native A01 roots must be observed")
    for row in value["native"]:
        require_keys(row, "run_id status calls reported_calls unknown_calls pending_calls input_tokens output_tokens")
        observation.identifier(row["run_id"])
        if any(type(row[key]) is not int or row[key] < 0 for key in observation.TOTAL):
            raise ValueError("A01 native counts are missing or mistyped")


def same_lifecycle(profile, schedule, occurrences):
    projected = [{key: value for key, value in row.items() if key != "work_run_id"} for row in occurrences]
    return (digest(profile["occurrences"]) == digest(projected) and all(
        digest(profile[key]) == digest(schedule[key]) for key in ("enabled", "next_due_at", "completed_at", "revision", "budget")))


def validate_schedule(row, attempt):
    require_keys(row, "id agent_version_id mode kind starts_at enabled revision next_due_at completed_at budget missed_policy overlap_policy notify_channel_ids network_domains timezone goal_digest")
    if (row["id"] != attempt["automation_id"] or row["agent_version_id"] != attempt["fixture"]["agent_version_id"]
            or row["mode"] != "ask" or row["kind"] != "once" or row["goal_digest"] != text_digest("整理虚拟日报")
            or row["missed_policy"] != "once" or row["overlap_policy"] != "skip" or row["timezone"] != "Asia/Shanghai"
            or digest(row["budget"]) != digest({"model_calls": 4, "token_limit": 10000})
            or row["notify_channel_ids"] != [] or row["network_domains"] != []
            or type(row["enabled"]) is not bool or type(row["revision"]) is not int):
        raise ValueError("The fixed paused once schedule differs")
    if datetime.fromisoformat(row["starts_at"]).tzinfo is None:
        raise ValueError("The fixed schedule must carry the server timezone")


def validate_root(row, attempt, *, scheduled=False):
    require_keys(row, "run_id conversation_id origin status occurrence_id agent_version_id messages events usage model")
    reference = attempt["scheduled_root"] if scheduled else attempt["platform_root"]
    if (row["run_id"] != reference["id"] or row["conversation_id"] != reference["fixture_id"]
            or row["agent_version_id"] != attempt["fixture"]["agent_version_id"]
            or row["origin"] != ("schedule" if scheduled else "interactive") or row["status"] not in adapters.TERMINAL):
        raise ValueError("Retained A01 root identity differs")
    require_keys(row["usage"], " ".join(observation.TOTAL))
    if any(type(count) is not int or count < 0 for count in row["usage"].values()):
        raise ValueError("A01 native usage is incomplete")
    require_keys(row["model"], "snapshot_id revision used_at temperature calls")
    model = row["model"]
    observation.identifier(model["snapshot_id"])
    if type(model["revision"]) is not int or model["revision"] != 2 or digest(model["temperature"]) != digest(0.4) or model["used_at"] is None:
        raise adapters.Blocked("A01 did not actually call the maintained model configuration")
    datetime.fromisoformat(model["used_at"])
    if not isinstance(model["calls"], list) or len(model["calls"]) != (1 if scheduled else 3):
        raise adapters.Blocked("Complete actual model calls are required")
    for call in model["calls"]:
        require_keys(call, "call_id run_id mode model_id model_revision status input_tokens output_tokens")
        observation.identifier(call["call_id"])
        if (call["run_id"] != row["run_id"] or call["mode"] != "ask" or call["model_id"] != attempt["fixture"]["model_id"]
                or type(call["model_revision"]) is not int or call["model_revision"] != 2 or call["status"] != "KNOWN"
                or type(call["input_tokens"]) is not int or call["input_tokens"] != 10
                or type(call["output_tokens"]) is not int or call["output_tokens"] != 5):
            raise adapters.Blocked("A01 model usage does not match its called configuration")
    calls = len(model["calls"])
    if digest(row["usage"]) != digest({"calls": calls, "reported_calls": calls, "unknown_calls": 0,
                                      "pending_calls": 0, "input_tokens": 10 * calls, "output_tokens": 5 * calls}):
        raise ValueError("Native usage differs from the actual traced model calls")
    require_keys(row["events"], "complete items")
    if row["events"]["complete"] is not True or not 1 <= len(row["events"]["items"]) <= 256:
        raise adapters.Blocked("The complete native A01 event stream is required")
    for index, event in enumerate(row["events"]["items"], 1):
        require_keys(event, "event_id event name call_id" if event["event"].startswith("tool.") else "event_id event")
        if type(event["event_id"]) is not int or event["event_id"] != index:
            raise adapters.Blocked("Native A01 event IDs are incomplete")
        if not isinstance(event["event"], str) or not observation.EVENT.fullmatch(event["event"]):
            raise ValueError("Unexpected native event name")
    if not isinstance(row["messages"], list) or not 1 <= len(row["messages"]) <= 16:
        raise adapters.Blocked("Native A01 messages are incomplete")
    for item in row["messages"]:
        require_keys(item, "role run_id text_digest calls" if item["role"] == "assistant" else
                     "role run_id text_digest name call_id output" if item["role"] == "tool" else "role run_id text_digest")
        if item["role"] not in {"user", "assistant", "tool"} or item["run_id"] != row["run_id"] or not fixed_digest(item["text_digest"]):
            raise ValueError("A01 message scope or text digest changed")
        if item["role"] == "tool":
            if item["name"] not in observation.NAMES or not observation.CALL.fullmatch(item["call_id"]):
                raise ValueError("Unknown A01 tool metadata")
            require_keys(item["output"], "items current_time" if item["name"] == "list_automation_agents" else "id enabled type")
    tools = [item for item in row["messages"] if item["role"] == "tool"]
    calls = [call for item in row["messages"] if item["role"] == "assistant" for call in item["calls"]]
    expected = [] if scheduled else [{"name": item["name"], "call_id": item["call_id"]} for item in tools]
    if (len(tools) != (0 if scheduled else 2) or digest(calls) != digest(expected)
            or len([item for item in row["messages"] if item["role"] == "user"]) != 1
            or len([item for item in row["messages"] if item["role"] == "assistant"]) != (1 if scheduled else 3)):
        raise adapters.Blocked("A01 actual assistant/tool correspondence is incomplete")
    native_names = [item["event"] for item in row["events"]["items"]]
    if (native_names.count("run.started") != 1 or native_names.count("run.completed") != 1
            or row["events"]["items"][-1]["event"] != "run.completed"
            or {"run.failed", "run.cancelled", "tool.interrupted"}.intersection(native_names)):
        raise adapters.Blocked("A01 terminal event lifecycle is incomplete")
    for event_name in ("tool.started", "tool.completed"):
        actual = [{"name": item["name"], "call_id": item["call_id"]} for item in row["events"]["items"] if item["event"] == event_name]
        if digest(actual) != digest(expected):
            raise adapters.Blocked("A01 native tool events do not match the actual tool results")
    return row


def fixed_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def validate_draft(body, data, attempt, specimen):
    validate_intents(attempt, specimen, "draft")
    require_keys(body, "initialization source schedule occurrences profile")
    validate_initialization(body["initialization"], attempt)
    validate_schedule(body["schedule"], attempt)
    validate_profile(body["profile"], data, attempt)
    validate_pre_dispatch(body["profile"], attempt)
    source = validate_root(body["source"], attempt)
    tools = [row for row in source["messages"] if row["role"] == "tool"]
    if (len(tools) != 2 or {row["name"] for row in tools} != observation.NAMES
            or source["status"] != "SUCCEEDED" or body["schedule"]["enabled"] is not False
            or body["schedule"]["revision"] != 1 or body["occurrences"] != []):
        raise adapters.Blocked("The real paused tool-created draft is not proved before enable")
    listed = next(row for row in tools if row["name"] == "list_automation_agents")
    created = next(row for row in tools if row["name"] == "create_automation_draft")
    if (digest(listed["output"]["items"]) != digest([{"id": attempt["fixture"]["agent_version_id"], "version": 1}])
            or digest(created["output"]) != digest({"id": attempt["automation_id"], "enabled": False, "type": "automation_draft"})
            or str(uuid5(UUID(source["run_id"]), created["call_id"])) != attempt["automation_id"]
            or (datetime.fromisoformat(listed["output"]["current_time"]) - datetime.fromisoformat(body["schedule"]["starts_at"])).total_seconds() != 5):
        raise ValueError("A01 tool source, listed business version or server-relative schedule differs")
    users = [row for row in source["messages"] if row["role"] == "user"]
    if len(users) != 1 or users[0]["text_digest"] != text_digest(specimen.input):
        raise ValueError("The real original Ask input differs")
    if (body["profile"]["enabled"] is not False or body["profile"]["occurrences"] != []
            or body["profile"]["provider"]["complete"] is not True or body["profile"]["provider"]["calls"] != 3
            or any(body["profile"]["counts"].values()) or not same_lifecycle(body["profile"], body["schedule"], body["occurrences"])):
        raise adapters.Blocked("The paused draft has unobserved or additional effects")


def validate_enabled(body, data, attempt, specimen):
    validate_intents(attempt, specimen, "enabled")
    require_keys(body, "schedule occurrences profile")
    validate_schedule(body["schedule"], attempt)
    validate_profile(body["profile"], data, attempt)
    validate_pre_dispatch(body["profile"], attempt)
    if (body["schedule"]["enabled"] is not True or body["occurrences"] != []
            or body["profile"]["enabled"] is not True or body["profile"]["occurrences"] != []
            or body["profile"]["provider"]["complete"] is not True or any(body["profile"]["counts"].values())
            or not same_lifecycle(body["profile"], body["schedule"], body["occurrences"])):
        raise adapters.Blocked("Explicit enable must be proved before the single controlled dispatch")


def validate_pre_dispatch(profile, attempt):
    run_id = attempt["platform_root"]["id"]
    provider = {"observed": True, "complete": True, "calls": 3, "tokens": 45, "pending": 0, "unknown_roots": 0,
                "roots": [{"mode": "ask", "run_id": run_id, "phase": "draft", "calls": 3, "tokens": 45}]}
    native = [{"run_id": run_id, "status": "SUCCEEDED", "calls": 3, "reported_calls": 3,
               "unknown_calls": 0, "pending_calls": 0, "input_tokens": 30, "output_tokens": 15}]
    if digest(profile["provider"]) != digest(provider) or digest(profile["native"]) != digest(native):
        raise adapters.Blocked("A01 pre-dispatch native/provider budget must be known, reconciled and unchanged")


def single_occurrence(body, attempt):
    rows = body["occurrences"]
    if not isinstance(rows, list) or len(rows) != 1:
        raise adapters.Blocked("One actual scheduled occurrence must exist")
    row = rows[0]
    require_keys(row, "id status ask_run_id work_run_id target_id result_status error_code")
    for key in ("id", "ask_run_id", "target_id"):
        observation.identifier(row[key])
    if row["status"] != "DISPATCHED" or row["work_run_id"] is not None:
        raise adapters.Blocked("The registered schedule did not create an actual Ask occurrence")
    return row


def verified(directory, data, attempt, specimen):
    validate_intents(attempt, specimen, "final")
    references = [attempt.get(slot) for slot in SLOTS.values()]
    if any(reference is None for reference in references) or digest(attempt.get("evidence_refs")) != digest(references):
        raise adapters.Blocked("A01 lifecycle reference set/order is incomplete")
    final = None
    for reference in attempt.get("evidence_refs", []):
        body = read(directory, data, attempt, reference)
        if reference["kind"] == "a01-initialization":
            validate_initialization(body, attempt)
        elif reference["kind"] == "a01-draft":
            validate_draft(body, data, attempt, specimen)
        elif reference["kind"] == "a01-enabled":
            validate_enabled(body, data, attempt, specimen)
        elif reference["kind"] == "a01-dispatched":
            require_keys(body, "schedule occurrences profile")
            validate_schedule(body["schedule"], attempt)
            validate_profile(body["profile"], data, attempt)
            single_occurrence(body, attempt)
        else:
            final = {"body": body, "usage": combined_usage(body)}
            evaluate(final, specimen, attempt)
    if final is None:
        raise adapters.Blocked("A01 complete retained execution facts are required")
    if digest(read(directory, data, attempt, attempt["automation_initialization_ref"])) != digest(final["body"]["draft"]["initialization"]):
        raise ValueError("The original initialization proof changed")
    for key, field in (("automation_draft_ref", "draft"), ("automation_enabled_ref", "enabled"), ("automation_dispatched_ref", "dispatched")):
        if digest(read(directory, data, attempt, attempt[key])) != digest(final["body"][field]):
            raise ValueError("An earlier A01 lifecycle boundary changed")
    return final


def combined_usage(body):
    return {key: body["draft"]["source"]["usage"][key] + body["execution"]["usage"][key] for key in observation.TOTAL}


def settled(body):
    profile = body["profile"]
    provider = profile["provider"]
    return (provider["complete"] is True and provider["pending"] == provider["unknown_roots"] == 0
            and all(value == 0 for value in profile["counts"].values())
            and all(row["status"] in adapters.TERMINAL and row["pending_calls"] == row["unknown_calls"] == 0
                    and row["calls"] == row["reported_calls"] for row in profile["native"])
            and body["schedule"]["enabled"] is False and body["schedule"]["next_due_at"] is None
            and body["schedule"]["completed_at"] is not None)


def evaluate(snapshot, specimen, attempt):
    validate_intents(attempt, specimen, "final")
    body = snapshot["body"]
    require_keys(body, "draft enabled dispatched execution schedule occurrences profile")
    identity = {"environment_id": attempt["environment_id"], "owner_id": attempt["owner_id"]}
    validate_draft(body["draft"], identity, attempt, specimen)
    validate_enabled(body["enabled"], identity, attempt, specimen)
    validate_profile(body["profile"], identity, attempt)
    validate_schedule(body["schedule"], attempt)
    execution = validate_root(body["execution"], attempt, scheduled=True)
    occurrence = single_occurrence(body, attempt)
    dispatched = single_occurrence(body["dispatched"], attempt)
    if (execution["occurrence_id"] != occurrence["id"] or execution["run_id"] != occurrence["ask_run_id"]
            or execution["conversation_id"] != occurrence["target_id"]
            or any(occurrence[key] != dispatched[key] for key in ("id", "ask_run_id", "target_id"))):
        raise ValueError("A01 final root is not its actual occurrence")
    usage = combined_usage(body)
    if digest(snapshot["usage"]) != digest(usage):
        raise ValueError("A01 whole-case native usage changed")
    adapters.known_usage({"usage": {"total": usage}, "observed": ["usage"]}, specimen)
    provider, native = body["profile"]["provider"], body["profile"]["native"]
    expected = {"calls": 4, "reported_calls": 4, "unknown_calls": 0, "pending_calls": 0, "input_tokens": 40, "output_tokens": 20}
    root_ids = {attempt["platform_root"]["id"], execution["run_id"]}
    metadata = {row["run_id"]: row for row in provider["roots"]}
    per_root = {attempt["platform_root"]["id"]: ("draft", 3, 45), execution["run_id"]: ("occurrence", 1, 15)}
    registered = set(metadata) == root_ids and all(
        (metadata[key]["phase"], metadata[key]["calls"], metadata[key]["tokens"]) == value for key, value in per_root.items())
    native_by_id = {row["run_id"]: row for row in native}
    native_matches = set(native_by_id) == root_ids and all(
        native_by_id[key]["status"] == "SUCCEEDED" and native_by_id[key]["calls"] == calls
        and native_by_id[key]["input_tokens"] == calls * 10 and native_by_id[key]["output_tokens"] == calls * 5
        for key, (_, calls, _) in per_root.items())
    actual = (execution["status"] == occurrence["result_status"] == "SUCCEEDED" and occurrence["error_code"] is None
              and settled(body) and digest(usage) == digest(expected)
              and provider["calls"] == 4 and provider["tokens"] == 60
              and registered
              and native_matches
              and sum(row["calls"] for row in native) == 4
              and sum(row["input_tokens"] + row["output_tokens"] for row in native) == 60
              and len([row for row in execution["messages"] if row["role"] == "assistant"]) == 1
              and all(row["role"] != "tool" and not row.get("calls") for row in execution["messages"])
              and any(row["role"] == "assistant" and row["text_digest"] == text_digest("虚拟日报已整理。") for row in execution["messages"]))
    expected_input = f"计划时刻：{body['schedule']['starts_at']}；任务时区：{body['schedule']['timezone']}。\n\n整理虚拟日报"
    actual = (actual and same_lifecycle(body["profile"], body["schedule"], body["occurrences"])
              and any(row["role"] == "user" and row["text_digest"] == text_digest(expected_input) for row in execution["messages"]))
    return [Result(registered_id="automation_lifecycle", status="PASS" if actual else "FAIL")], {
        "run": {"status": execution["status"]}, "usage": {"total": usage}, "observed": ["usage"],
        "business_outcomes": [{"kind": "draft-creation", "run_id": attempt["platform_root"]["id"], "status": "SUCCEEDED"},
                              {"kind": "scheduled-execution", "run_id": execution["run_id"], "status": execution["status"]}]}


def cleanup_ready(directory, data, attempt, specimen):
    return settled(verified(directory, data, attempt, specimen)["body"])


def cleanup(platform, journal, attempt, specimen):
    body = verified(journal.directory, journal.data, attempt, specimen)["body"]
    current = observation.profile(platform, attempt["automation_id"], attempt["platform_root"]["id"])
    return "CLEAN" if digest(current) == digest(body["profile"]) and settled(body) else "NEEDS_REVIEW"
