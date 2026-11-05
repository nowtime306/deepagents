"""Fixed formal HTTP F07 init/run/observe/cleanup; opt-in only, no CLI registration."""

import json
import time
from uuid import uuid4

from evaluation_runner.fixed_evidence import binding, load, store
from evaluation_runner.fixed_fixtures import FixedFixtures
from evaluation_runner.journal import UnknownSubmission
from evaluation_runner.profiles_v2.f07.contract import (
    BASE,
    CASE,
    EXECUTION_LIMITS,
    INPUT,
    LIMITS,
    OUTPUT,
    check_specimen,
    digest,
    graph,
)


def deadline(attempt):
    if not attempt["f07_started"] <= time.time() < attempt["f07_expires"]:
        raise ValueError("Original entire F07 deadline elapsed; never reset")


def unsettled(attempt):
    if any(row["status"] != "CONFIRMED" for row in attempt.get("fixture_intents", {}).values()):
        raise UnknownSubmission("An original F07 operation is unsettled; do not replay")


def usage_total(value, root):
    if (
        not isinstance(value, dict)
        or value.get("complete") is not True
        or value.get("scope") != {"mode": "workflow", "run_id": root}
        or not isinstance(value.get("total"), dict)
    ):
        raise ValueError("Complete original standalone workflow usage wrapper required")
    total = value["total"]
    counts = ("calls", "reported_calls", "unknown_calls", "pending_calls")
    if (
        set(total) != {*counts, "input_tokens", "output_tokens"}
        or any(type(total.get(key)) is not int or total[key] < 0 for key in counts)
        or total["calls"] != sum(total[key] for key in counts[1:])
        or total["pending_calls"] != 0
        or any(
            total[key] is not None and (type(total[key]) is not int or total[key] < 0)
            for key in ("input_tokens", "output_tokens")
        )
    ):
        raise ValueError("Native usage counts or completeness differ")
    return total


def observe(platform, attempt, scene, root):
    row = attempt["fixture"][scene]
    base = "/api/v1/workflows/" + row["id"]
    native = platform.get(base + "/runs", params={"run_id": root})
    if native["next_cursor"] is not None or len(native["items"]) != 1:
        raise ValueError("A complete original native root is required")
    native = native["items"][0]
    if (
        native["id"] != root
        or native["workflow_id"] != row["id"]
        or native["snapshot"]["definition"] != graph(scene)
        or native["snapshot"]["model_id"] != attempt["model_id"]
    ):
        raise ValueError("Native workflow business snapshot differs")
    events = platform.get(base + "/runs/" + root + "/events")["items"]
    usage_wrapper = platform.get("/api/v1/agent-evaluations/workflow-runs/" + root + "/usage")
    usage = usage_total(usage_wrapper, root)
    expected = "FAILED" if scene == "model" else "SUCCEEDED"
    if (
        native["status"] != expected
        or not events
        or len(events) > 32
        or len({e["event_id"] for e in events}) != len(events)
    ):
        raise ValueError("Native root/events are incomplete")
    if scene == "model":
        if (
            native["output"] is not None
            or native["error_node"] != "action"
            or any(event["event"] == "node.handled" or event["node_id"] == "fallback" for event in events)
        ):
            raise ValueError("Unknown model effect must fail without fallback")
        if (
            usage["calls"] != 1
            or usage["unknown_calls"] != 1
            or usage["reported_calls"] != 0
            or usage["pending_calls"] != 0
            or usage["input_tokens"] is not None
            or usage["output_tokens"] is not None
        ):
            raise ValueError("Native model usage UNKNOWN/null must be retained")
    else:
        code = "NODE_EXECUTION_FAILED" if scene == "json" else "WORKFLOW_PURE_TIMEOUT"
        handled = [event for event in events if event["node_id"] == "action" and event["event"] == "node.handled"]
        handled_code = "TRANSFORM_FAILED" if scene == "json" else "WORKFLOW_PURE_TIMEOUT"
        failures = [event for event in events if event["node_id"] == "action" and event["event"] == "node.failed"]
        if (
            native["output"] != ("已处理错误" if scene == "json" else OUTPUT)
            or len(failures) != 1
            or failures[0]["data"]["error_code"] != code
            or len(handled) != 1
            or json.loads(handled[0]["data"].get("output", "null")) != {"error_code": handled_code}
            or any(event["node_id"] == "normal" for event in events)
            or any(usage[key] != 0 for key in usage)
        ):
            raise ValueError("The actual declared error path was not observed")
    return {"run_id": root, "scene": scene, "native": native, "events": events, "usage": usage_wrapper}


def retain(journal, attempt, key, value):
    blob = {"binding": binding(journal.data, attempt), "kind": "f07-v2", "body": value}
    reference = store(journal.directory, "f07-v2", blob)
    attempt.setdefault("f07_refs", {})[key] = reference
    attempt.setdefault("evidence_refs", []).append(reference)
    journal.save()


def run(platform, journal, attempt, scene, key, published=False):
    deadline(attempt)
    unsettled(attempt)
    original = attempt.setdefault("f07_runs", {})
    if key in original:
        raise UnknownSubmission("Original dispatched F07 roots are not automatically replayed")
    root = str(uuid4())
    original[key] = {"root_id": root, "scene": scene, "state": "SUBMITTING"}
    journal.save()
    row = attempt["fixture"][scene]
    path = "/api/v1/workflows/" + row["id"]
    path += ("/versions/" + str(row["version"]) + "/runs") if published else "/test-runs"
    body = {"text": "not-json" if scene == "json" else INPUT, "run_id": root}
    if not published:
        body["revision"] = 1
    fixtures = FixedFixtures(platform, journal, attempt)
    # A formal 502 is only a known failure after the native run/events corroborate it below.
    fixtures.mutate("f07-run-" + key, "POST", path, body=body, accepted=(200, 502))
    deadline(attempt)
    result = observe(platform, attempt, scene, root)
    original[key]["state"] = "OBSERVED"
    retain(journal, attempt, key, result)
    return result


def admit(platform, journal):
    proof = platform.attest()
    value = platform.get(BASE + "/observations")
    state = value["state"]
    if (
        proof["fixture_profile"] != {"id": "handled-transform-error", "version": 2}
        or digest(proof.get("execution_limits")) != digest(EXECUTION_LIMITS)
        or digest(value.get("limits")) != digest(LIMITS)
        or state.get("phase") != "NEW"
        or value.get("reconstructed") is not False
        or (state.get("environment_id"), proof["environment_id"]) != (journal.data["environment_id"],) * 2
        or (state.get("owner_id"), proof["owner_id"]) != (journal.data["owner_id"],) * 2
        or state.get("namespace") != proof["sandbox_namespace"]
        or state.get("roots") != {}
        or state.get("pure") != {}
        or state.get("provider") != {"calls": 0, "reserved_tokens": 0, "status": "UNUSED"}
        or value.get("runs") != []
        or value.get("usage") != []
        or value.get("counts")
        != dict.fromkeys(("work_tasks", "sandboxes", "operations", "ask_runs", "work_runs", "workflow_runs"), 0)
    ):
        raise ValueError("Original zero-effect F07 v2 environment and actual limits required")


def initialize(platform, journal, attempt, specimen):
    check_specimen(specimen)
    if "f07_started" in attempt or attempt.get("started") is True:
        raise UnknownSubmission("Do not restart an initialized F07 case")
    attempt.update(f07_started=time.time(), case_id=CASE)
    attempt["f07_expires"] = attempt["f07_started"] + 120
    journal.save()
    admit(platform, journal)
    deadline(attempt)
    fixtures = FixedFixtures(platform, journal, attempt)
    fixtures.mutate(
        "f07-activate",
        "POST",
        BASE + "/activate",
        body={"started": attempt["f07_started"], "expires": attempt["f07_expires"]},
    )
    model = fixtures.create(
        "f07-model",
        "/api/v1/admin/models",
        {
            "name": "F07 " + fixtures.prefix,
            "model": "eval::AET-F07::v2",
            "base_url": platform.target.api_base_url + "/controlled-model",
            "kind": "chat",
            "provider": "custom",
            "protocol": "openai",
            "enabled": True,
            "supports_tools": False,
            "max_output_tokens": 64,
        },
    )
    attempt["model_id"] = model
    fixtures.mutate("f07-seal", "POST", BASE + "/seal-model", body={"model_id": model})
    attempt["fixture"] = {}
    for scene in ("json", "timer", "model"):
        identifier = fixtures.create(
            "f07-workflow-" + scene,
            "/api/v1/workflows",
            {"name": "F07 " + scene + " " + fixtures.prefix, "model_id": model, "definition": graph(scene)},
        )
        attempt["fixture"][scene] = {"id": identifier}
        journal.save()
    run(platform, journal, attempt, "timer", "timer-init")
    version = fixtures.mutate(
        "f07-publish-timer",
        "POST",
        "/api/v1/workflows/" + attempt["fixture"]["timer"]["id"] + "/publish",
        body={"revision": 1},
    )
    attempt["fixture"]["timer"]["version"] = version["version"]
    journal.save()
    return attempt["fixture"]


def execute(platform, journal, attempt, specimen):
    check_specimen(specimen)
    run(platform, journal, attempt, "timer", "timer-main", published=True)
    run(platform, journal, attempt, "json", "json-companion")
    # This is last: the unknown provider effect can never be followed by another model call.
    run(platform, journal, attempt, "model", "model-companion")
    value = platform.get(BASE + "/observations")
    state = value["state"]
    if (
        len(state["roots"]) != 4
        or state["provider"]["calls"] != 1
        or state["provider"]["reserved_tokens"] != 15
        or state["provider"]["status"] != "UNKNOWN"
        or len(state["pure"]) != 2
        or any(not entry["cancelled"] for entry in state["pure"].values())
    ):
        raise ValueError("Independent actual-timer/provider observations differ")
    if (
        value["counts"]
        != {"work_tasks": 0, "sandboxes": 0, "operations": 0, "ask_runs": 0, "work_runs": 0, "workflow_runs": 4}
        or len(value["runs"]) != 4
        or len(value["usage"]) != 1
    ):
        raise ValueError("Unregistered effects or extra native roots/usage")
    retain(journal, attempt, "final-observation", value)
    attempt["usage_completeness"] = "UNKNOWN"
    attempt["task_outcome"] = "EXPECTED_UNKNOWN_PROPAGATED"
    journal.save()
    return value


def cleanup(platform, journal, attempt, specimen):
    check_specimen(specimen)
    if "closed-observation" in attempt.get("f07_refs", {}):
        unsettled(attempt)
        closed = load(journal.directory, attempt["f07_refs"]["closed-observation"], "f07-v2")
        if closed["binding"] != binding(journal.data, attempt):
            raise ValueError("Foreign confirmed cleanup evidence")
        if digest(platform.get(BASE + "/observations")["state"]) != digest(closed["body"]):
            raise ValueError("Confirmed closed state changed")
        return "CLOSED_UNKNOWN_RETAINED"
    deadline(attempt)
    unsettled(attempt)
    if set(attempt.get("f07_refs", {})) != {
        "timer-init",
        "timer-main",
        "json-companion",
        "model-companion",
        "final-observation",
    }:
        return "NEEDS_REVIEW"
    for key in ("timer-init", "timer-main", "json-companion", "model-companion"):
        blob = load(journal.directory, attempt["f07_refs"][key], "f07-v2")
        if blob["binding"] != binding(journal.data, attempt):
            raise ValueError("Foreign F07 retained evidence")
        now = observe(platform, attempt, blob["body"]["scene"], blob["body"]["run_id"])
        if digest(now) != digest(blob["body"]):
            raise ValueError("Original run facts changed")
    FixedFixtures(platform, journal, attempt).mutate("f07-close", "POST", BASE + "/close", body={})
    # FixedFixtures returns only safe recognized fields; read independent state for close proof.
    state = platform.get(BASE + "/observations")["state"]
    if state["phase"] != "CLOSED" or state["provider"]["status"] != "UNKNOWN":
        raise ValueError("Close must preserve original UNKNOWN")
    retain(journal, attempt, "closed-observation", state)
    return "CLOSED_UNKNOWN_RETAINED"
