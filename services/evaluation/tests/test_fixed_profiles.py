import json
from uuid import uuid4

import httpx
import pytest
from test_core import attestation, target

from evaluation_runner.client import Platform
from evaluation_runner.fixed_fixtures import FixedFixtures
from evaluation_runner.fixed_run import catalog, cleanup, execute, initialize, readiness, suite_context
from evaluation_runner.journal import Journal, UnknownSubmission


def test_a02_formal_initialization_two_turn_observation_metric_and_retained_history(tmp_path):
    value = target()
    identifiers = {key: str(uuid4()) for key in ("model", "agent", "version", "conversation")}
    runs, calls = [], []
    proof = attestation(value)
    proof["permissions"] = ["evaluation.read", "evaluation.manage", "evaluation.run", "agents.manage",
                             "chat.read", "chat.manage", "chat.run"]

    def server(request):
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json={"observers": ["root-model-context-v1"], "sandbox_limits": None})
        if request.method == "POST":
            body = json.loads(request.content)
            calls.append((path, body))
            if path == "/api/v1/admin/models":
                assert body["model"] == "eval::AET-A02::v1"
                return httpx.Response(201, json={"id": identifiers["model"]})
            if path.endswith("/model-bindings"):
                assert body == {"case_id": "AET-A02", "role": "lead", "model_id": identifiers["model"]}
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/agents":
                return httpx.Response(201, json={"id": identifiers["agent"]})
            if path.endswith("/publish"):
                return httpx.Response(201, json={"id": identifiers["version"]})
            if path == "/api/v1/conversations":
                assert body["agent_version_id"] == identifiers["version"]
                return httpx.Response(201, json={"id": identifiers["conversation"]})
            if path.endswith("/runs"):
                identifier = str(uuid4())
                runs.append((identifier, body["message"]))
                return httpx.Response(202, json={"run_id": identifier})
            pytest.fail("Unexpected business mutation")
        if path.endswith("/messages"):
            messages = []
            for index, (identifier, text) in enumerate(runs):
                messages.extend([{"role": "user", "run_id": identifier, "content": {"text": text}},
                                 {"role": "assistant", "run_id": identifier,
                                  "content": {"data": {"content": json.dumps({"project": "星桥", "people": index + 2}),
                                                       "tool_calls": []}}}])
            return httpx.Response(200, json={"items": messages, "next_before": None})
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": {"calls": len(runs), "reported_calls": len(runs),
                                                       "unknown_calls": 0, "pending_calls": 0,
                                                       "input_tokens": 10 * len(runs), "output_tokens": 5 * len(runs)}})
        if path.endswith("/observations"):
            return httpx.Response(200, json={"controlled_model": {"complete": True}, "context_assertions": [
                {"human_turns": 2, "has_project_context": True, "has_update_to_three": True}]})
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    platform = Platform(value, transport=httpx.MockTransport(server))
    specimen = next(row for row in catalog().cases if row.case_id == "AET-A02")
    journal = Journal(tmp_path)
    attempt = {"attempt_id": str(uuid4()), "case_id": specimen.case_id, "fact_results": [], "metric_results": []}
    journal.data["attempts"].append(attempt)
    try:
        initialize(platform, journal, attempt, specimen)
        initialize(platform, journal, attempt, specimen)
        execute(platform, journal, attempt, specimen, tmp_path)
        execute(platform, journal, attempt, specimen, tmp_path)
        assert len(runs) == 2
        assert attempt["task_outcome"] == "PASS"
        assert attempt["metric_results"][0]["status"] == "PASS"
        assert "judge.generate=0" in attempt["metric_results"][0]["reason"]
        assert cleanup(platform, journal, attempt) == "CLEAN"
        assert len(attempt["retained_resources"]) == 3
        assert len(calls) == 7  # Five fixture requests and two actual conversation turns; no duplicate replay.
    finally:
        journal.close()
        platform.close()


def test_unknown_fixture_response_is_never_reposted(tmp_path):
    value, calls = target(), []

    def server(request):
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=attestation(value))
        calls.append(request.url.path)
        raise httpx.ReadError("Response disconnected after remote write")

    platform = Platform(value, transport=httpx.MockTransport(server))
    journal = Journal(tmp_path)
    attempt = {"attempt_id": str(uuid4())}
    fixtures = FixedFixtures(platform, journal, attempt)
    try:
        with pytest.raises(UnknownSubmission):
            fixtures.model("AET-A02")
        with pytest.raises(UnknownSubmission):
            fixtures.model("AET-A02")
        assert calls == ["/api/v1/admin/models"]
    finally:
        journal.close()
        platform.close()


def test_readonly_preflight_names_unimplemented_case_and_checks_no_business_post():
    value, methods = target(), []
    proof = attestation(value)

    def server(request):
        methods.append(request.method)
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": False})
        return httpx.Response(404, json={})

    platform = Platform(value, transport=httpx.MockTransport(server))
    try:
        result = readiness(platform, next(row for row in catalog().cases if row.case_id == "AET-A03"))
        assert result["status"] == "BLOCKED" and len(result["missing"]) >= 2
        assert set(methods) == {"GET"}
    finally:
        platform.close()


def test_suite_context_rejects_changed_input_and_reuses_same_standalone_identity():
    dataset, value = catalog(), target()
    specimen = dataset.cases[1]
    context = suite_context(None, specimen, dataset, value)
    assert suite_context(None, specimen, dataset, value, context) == context
    changed = specimen.model_copy(update={"input": "Changed task input"})
    with pytest.raises(ValueError):
        suite_context(None, changed, dataset, value, context)
