import json
from uuid import uuid4
from xml.etree import ElementTree as ET

import httpx
import portalocker
import pytest

from evaluation_runner import adapters
from evaluation_runner.client import Platform
from evaluation_runner.contracts import Case, Check, Dataset, Result, Target, outcome
from evaluation_runner.engine import run
from evaluation_runner.journal import Journal, UnknownSubmission
from evaluation_runner.metrics import tool_correctness
from evaluation_runner.reports import write_report


def target():
    return Target(environment_id=str(uuid4()), api_base_url="http://127.0.0.1:8199", platform_commit="a" * 40,
                  owner_id=str(uuid4()), required_migrations=["084_vector_compatibility.sql"],
                  fixture_profile={"id": "controlled-v1", "version": 1}, resource_bindings={"AET-W01": "fake"})


def attestation(value):
    return {"environment_id": value.environment_id, "owner_id": value.owner_id,
            "platform_commit": value.platform_commit, "schema_migrations": value.required_migrations,
            "database_is_test": True, "permissions": ["evaluation.read"],
            "fixture_profile": value.fixture_profile.model_dump(),
            "execution_limits": {"schema_version": 1, "enforcement": "controlled-model-factory-v1",
                                 "scope": "resource-run-root", "profiles": {
                                     "AET-A02": {"mode": "ask", "max_model_calls": 1, "max_tokens": 15},
                                     "AET-F01": {"mode": "workflow", "max_model_calls": 4, "max_tokens": 60}}}}


def case(mode="work"):
    return Case(case_id="AET-W01", name="Missing file", mode=mode, requirements=["AE-11"], tags=["negative"],
                input="Create the requested file", fixture_profile={"id": "work-coding", "version": 1},
                validators=[{"registered_id": "artifact_exists", "parameters": {"path": "/outputs/result.txt"}}])


@pytest.mark.parametrize("mutation", ["owner_id", "environment_id", "platform_commit", "fixture_profile", "database_is_test"])
def test_false_or_wrong_attestation_rejected(mutation):
    value = target()
    returned = attestation(value)
    returned[mutation] = False if mutation == "database_is_test" else "wrong"
    platform = Platform(value, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=returned)))
    with pytest.raises(ValueError):
        platform.attest()
    platform.close()


def test_committed_post_with_lost_response_is_not_resubmitted(tmp_path):
    calls = []

    def committed_then_disconnected(request):
        calls.append(request.url.path)
        raise httpx.ReadError("Response lost after the remote write")

    client = httpx.Client(transport=httpx.MockTransport(committed_then_disconnected), base_url="http://127.0.0.1")
    journal = Journal(tmp_path)
    attempt = {}
    journal.data["attempts"].append(attempt)
    with pytest.raises(UnknownSubmission):
        journal.post(client, attempt, "/submit", {"input": "once"}, "submit")
    journal.close()
    journal = Journal(tmp_path)
    with pytest.raises(UnknownSubmission):
        journal.post(client, journal.data["attempts"][0], "/submit", {"input": "once"}, "submit")
    assert calls == ["/submit"]
    assert journal.data["attempts"][0]["submission_intents"]["submit"]["status"] == "UNKNOWN"
    journal.close()


def test_succeeded_claim_without_file_fails_real_adapter_path(tmp_path):
    value = target()
    posts = []

    def server(request):
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=attestation(value))
        if request.method == "POST":
            posts.append(path)
            return httpx.Response(202, json={"id": "task"} if path == "/api/v1/work-tasks" else {"run_id": "run"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith(("/artifacts", "/operations")):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"status": "COMPLETED", "result": {"text": "I created the file"}})

    platform = Platform(value, transport=httpx.MockTransport(server))
    report = run(platform, value, Dataset(dataset_id="negative", dataset_version=1, cases=[case()]), tmp_path)
    assert report["gate"] == "FAIL"
    assert report["attempts"][0]["fact_results"][0]["status"] == "FAIL"
    assert posts == ["/api/v1/work-tasks", "/api/v1/work-tasks/task/runs"]
    assert ET.parse(tmp_path / "results.xml").find("testcase/failure") is not None
    platform.close()


@pytest.mark.parametrize("called", [
    [{"name": "write", "input_parameters": {"path": "wrong"}, "output": "ok"}],
    [{"name": "write", "input_parameters": {"path": "allowed"}, "output": "wrong"}],
    [{"name": "delete", "input_parameters": {"path": "allowed"}, "output": "ok"}],
])
def test_actual_deepeval_wrong_tool_parameters_output_rejected(called):
    expected = [{"name": "write", "input_parameters": {"path": "allowed"}, "output": "ok"}]
    result = tool_correctness(Check(registered_id="tool_correctness", parameters={"expected_tools": expected}),
                              {"tools_complete": True, "tools": called})
    assert result.status == "FAIL"
    assert "judge.generate=0" in result.reason


def test_actual_deepeval_positive_and_extra_tool_rejected():
    tools = [{"name": "read", "input_parameters": {"path": "allowed"}, "output": "ok"}]
    spec = Check(registered_id="tool_correctness", parameters={"expected_tools": tools})
    assert tool_correctness(spec, {"tools_complete": True, "tools": tools}).status == "PASS"
    assert tool_correctness(spec, {"tools_complete": True, "tools": tools + tools}).status == "FAIL"


def test_required_score_error_or_missing_fact_never_green(tmp_path):
    fact = Result(registered_id="fact", status="FAIL")
    high_score = Result(registered_id="judge", status="PASS", score=1.0)
    assert outcome([fact], [high_score], "CLEAN") == "FAIL"
    assert outcome([high_score], [Result(registered_id="judge", status="ERROR")], "CLEAN") == "NEEDS_REVIEW"
    assert outcome([], [high_score], "CLEAN") == "NEEDS_REVIEW"
    result = write_report(tmp_path, {"expected_attempts": 20, "attempts": [
        {"case_id": "AET-A01", "repeat_index": 1, "task_outcome": "PASS", "cleanup_status": "CLEAN"}]})
    assert result["gate"] == "NEEDS_REVIEW"
    assert result["unexecuted_attempts"] == 19
    assert ET.parse(tmp_path / "results.xml").find("testcase/error") is not None


def test_cleanup_preserves_unknown_write_and_resource(tmp_path):
    journal = Journal(tmp_path)
    attempt = {"submission_intents": {"submit": {"status": "UNKNOWN"}},
               "created_resources": [{"kind": "work-task", "id": "keep", "path": "/api/v1/work-tasks/keep"}]}
    assert adapters.cleanup(None, journal, attempt) == "NEEDS_REVIEW"
    journal.close()


def test_journal_lock_prevents_second_consumer(tmp_path):
    first = Journal(tmp_path)
    with pytest.raises(portalocker.exceptions.LockException):
        Journal(tmp_path)
    first.close()


@pytest.mark.parametrize("url", ["https://example.com:443", "http://127.0.0.1:8199/path", "http://user:secret@127.0.0.1:8199"])
def test_external_or_credential_bearing_target_rejected(url):
    data = target().model_dump()
    data["api_base_url"] = url
    with pytest.raises(ValueError):
        Target.model_validate(data)


def test_case_snapshot_is_independent_of_source_mutation(tmp_path):
    original = case()
    snapshot = json.loads(original.model_dump_json())
    original.input = "Changed later"
    assert snapshot["input"] != original.input
