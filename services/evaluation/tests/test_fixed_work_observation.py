"""W01 deterministic observation checks; not a real Work execution."""

import copy
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from evaluation_runner import fixed_work_observation as work
from evaluation_runner.client import Platform
from evaluation_runner.contracts import Target, outcome


def context():
    target = Target(environment_id=str(uuid4()), owner_id=str(uuid4()), api_base_url="http://127.0.0.1:1",
                    platform_commit="a" * 40, required_migrations=[], fixture_profile={"id": "controlled-v1", "version": 1})
    return target, {name: str(uuid4()) for name in ("run", "task", "snapshot", "namespace", "artifact", "operation")}


def server(target, ids, requests):
    def respond(request):
        requests.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json={**target.model_dump(include={"owner_id", "environment_id", "platform_commit", "fixture_profile"}),
                                             "database_is_test": True, "schema_migrations": [], "permissions": ["work.read"],
                                             "sandbox_namespace": ids["namespace"]})
        if path.endswith("/events"):
            rows = [] if request.url.params["after"] == "2" else [
                {"event_id": 1, "event": "run.started", "data": {"secret": "DO_NOT_RETAIN"}},
                {"event_id": 2, "event": "run.completed", "data": {"secret": "DO_NOT_RETAIN"}}]
            return httpx.Response(200, json={"items": rows, "next_after": 2})
        if path.endswith("/artifacts"):
            return httpx.Response(200, json={"items": [{"id": ids["artifact"], "path": "/secret-filename.txt",
                                                        "digest": "b" * 64, "size": 7}]})
        if path.endswith("/operations"):
            return httpx.Response(200, json={"items": [{"id": ids["operation"], "name": "read_file", "status": "SUCCEEDED",
                                                        "args": {"secret": "DO_NOT_RETAIN"}, "output": "DO_NOT_RETAIN"}]})
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": {"calls": 1, "reported_calls": 1, "pending_calls": 0,
                                                        "unknown_calls": 0, "input_tokens": 10, "output_tokens": 5}})
        if path.endswith("/observations"):
            return httpx.Response(200, json={"mode": "work", "run_id": ids["run"], "status": "COMPLETED",
                                             "controlled_model": {"recorded": True, "complete": True, "case_id": "AET-W01",
                                                                  "provider_calls": 1, "tokens": 15},
                                             "context_assertions": [{"secret": "DO_NOT_RETAIN"}],
                                             "sandbox_sessions": {"complete": True, "states": {"RELEASED": 1}}})
        return httpx.Response(200, json={"id": ids["run"], "task_id": ids["task"], "owner_id": target.owner_id,
                                         "environment": "coding", "status": "COMPLETED", "goal": "DO_NOT_RETAIN",
                                         "result": {"snapshot_id": ids["snapshot"], "text": "DO_NOT_RETAIN"}})
    return respond


def observation():
    target, ids = context()
    requests = []
    platform = Platform(target, transport=httpx.MockTransport(server(target, ids, requests)))
    try:
        return work.collect(platform, ids["run"], ids["task"]), requests
    finally:
        platform.close()


def grades(value):
    return {result.registered_id: result.status for result in work.evaluate(value)}


def test_completed_queue_and_claims_do_not_replace_the_missing_file():
    value, requests = observation()
    facts = work.evaluate(value)
    assert grades(value) == {"work_completed_snapshot": "PASS", "artifact_exists": "FAIL", "work_queue_lifecycle": "PASS",
                             "work_known_usage": "PASS", "work_resources_released": "PASS"}
    assert outcome(facts, [], "CLEAN") == "FAIL"
    assert all(method == "GET" for method, _ in requests)
    assert "DO_NOT_RETAIN" not in json.dumps(value) and "secret-filename" not in json.dumps(value)


@pytest.mark.parametrize("change", ["missing_items", "partial_page", "wrong_owner", "wrong_observer", "event_gap"])
def test_unobserved_or_cross_scope_evidence_is_not_defaulted_to_empty(change):
    target, ids = context()
    respond = server(target, ids, [])

    def altered(request):
        response = respond(request)
        body = response.json()
        if change == "missing_items" and request.url.path.endswith("/artifacts"):
            body = {}
        if change == "partial_page" and request.url.path.endswith("/artifacts"):
            body["next_after"] = "unobserved-next-page"
        if change == "wrong_owner" and request.url.path == "/api/v1/work-runs/" + ids["run"]:
            body["owner_id"] = str(uuid4())
        if change == "wrong_observer" and request.url.path.endswith("/observations"):
            body["mode"] = "ask"
        if change == "event_gap" and request.url.path.endswith("/events") and body["items"]:
            body["items"][1]["event_id"] = 3
        return httpx.Response(200, json=body)

    platform = Platform(target, transport=httpx.MockTransport(altered))
    try:
        with pytest.raises(ValueError):
            work.collect(platform, ids["run"], ids["task"])
    finally:
        platform.close()


@pytest.mark.parametrize("field", ["artifacts_complete", "snapshot_id", "status"])
def test_incomplete_or_live_export_is_not_a_known_missing_artifact(field):
    value, _ = observation()
    value[field] = {"artifacts_complete": False, "snapshot_id": None, "status": "RUNNING"}[field]
    assert grades(value)["artifact_exists"] == "BLOCKED"


def test_existing_file_is_not_claimed_success_without_content_verification():
    value, _ = observation()
    value["artifacts"][0]["path_sha256"] = hashlib.sha256(b"/report.txt").hexdigest()
    assert grades(value)["artifact_exists"] == "PASS" and grades(value)["w01_control"] == "BLOCKED"
    assert outcome(work.evaluate(value), [], "CLEAN") == "NEEDS_REVIEW"


@pytest.mark.parametrize("mutate", [
    lambda v: v["usage"].update(unknown_calls=1),
    lambda v: v["provider"].update(complete=False),
    lambda v: v["usage"].update(output_tokens=None),
    lambda v: v["sandbox_states"].update(RELEASED=0, READY=1),
    lambda v: v["operations"][0].update(status="UNKNOWN"),
])
def test_unknown_usage_or_unreleased_resources_stay_blocked(mutate):
    value, _ = observation()
    mutate(value)
    assert "BLOCKED" in grades(value).values()


def test_numeric_booleans_extra_fields_and_nonfinite_data_are_rejected():
    original, _ = observation()
    for section, field, replacement in [("usage", "unknown_calls", False), ("usage", "calls", True),
                                        ("provider", "tokens", float("nan"))]:
        value = copy.deepcopy(original)
        value[section][field] = replacement
        with pytest.raises(ValueError):
            work.evaluate(value)
    with pytest.raises(ValueError):
        work.evaluate({**original, "schema_version": True})
    with pytest.raises(ValueError):
        work.evaluate({**original, "raw_private_output": "not allowed"})


@pytest.mark.parametrize("status", ["RUNNING", "UNRECOGNIZED", "PENDING", ""])
def test_unknown_operation_states_cannot_authorize_recovery(status):
    value, _ = observation()
    value["operations"][0]["status"] = status
    with pytest.raises(ValueError):
        work.evaluate(value)
