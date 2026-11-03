import json
from pathlib import Path

import httpx
from test_core import attestation, target

from evaluation_runner.client import Platform
from evaluation_runner.contracts import Case, Dataset
from evaluation_runner.engine import run


def test_formal_ask_two_turns_preserve_context_and_no_second_post_on_rerun(tmp_path):
    value = target()
    value.resource_bindings = {"AET-A02": "fake-multiturn"}
    posts = []

    def server(request):
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=attestation(value))
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": {"calls": 2, "reported_calls": 2, "unknown_calls": 0,
                                                       "pending_calls": 0, "input_tokens": 20, "output_tokens": 10}})
        if request.method == "POST":
            posts.append((path, json.loads(request.content)))
            if path == "/api/v1/conversations":
                return httpx.Response(201, json={"id": "conversation"})
            return httpx.Response(202, json={"run_id": "second" if len(posts) == 3 else "first"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/messages"):
            return httpx.Response(200, json={"items": [
                {"role": "user", "content": {"text": "Hangzhou, three days, train"}},
                {"role": "user", "content": {"text": "Change to two days"}},
                {"role": "assistant", "run_id": "second", "content": {"data": {"content": "Hangzhou;2;train"}}},
            ], "next_before": None})
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    specimen = Case(case_id="AET-A02", name="Context", mode="ask", input="Change to two days",
                    requirements=["AE-03"], tags=["context"], fixture_profile={"id": "ask-plain", "version": 1},
                    fixture_parameters={"prior_messages": ["Hangzhou, three days, train"]},
                    validators=[{"registered_id": "output_equals", "parameters": {"expected": "Hangzhou;2;train"}},
                                {"registered_id": "user_message_count", "parameters": {"expected": 2}}])
    data = Dataset(dataset_id="context", dataset_version=1, cases=[specimen])
    platform = Platform(value, transport=httpx.MockTransport(server))
    assert run(platform, value, data, tmp_path)["gate"] == "PASS"
    assert run(platform, value, data, tmp_path)["gate"] == "PASS"
    assert len(posts) == 3
    assert posts[1][1]["message"] == "Hangzhou, three days, train"
    assert posts[2][1]["message"] == "Change to two days"
    platform.close()


def test_formal_workflow_uses_explicit_business_version_and_actual_run_query(tmp_path):
    value = target()
    value.resource_bindings = {"AET-F01": "fake", "fixed-workflow": "workflow"}
    effects = []
    identifier = None

    def server(request):
        nonlocal identifier
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=attestation(value))
        if request.url.path.endswith("/usage"):
            return httpx.Response(200, json={"total": {"calls": 0, "reported_calls": 0, "unknown_calls": 0,
                                                       "pending_calls": 0, "input_tokens": None, "output_tokens": None}})
        if request.method == "POST":
            effects.append(request.url.path)
            identifier = json.loads(request.content)["run_id"]
            return httpx.Response(200, json={"run_id": identifier})
        assert request.url.params["run_id"] == identifier
        return httpx.Response(200, json={"items": [{"id": identifier, "status": "SUCCEEDED", "output": "V2"}]})

    specimen = Case(case_id="AET-F01", name="Version", mode="workflow", input="Input", business_version=2,
                    requirements=["AE-15"], tags=["version"],
                    fixture_profile={"id": "workflow-published", "version": 1},
                    fixture_parameters={"workflow_binding": "fixed-workflow"},
                    validators=[{"registered_id": "output_equals", "parameters": {"expected": "V2"}}])
    platform = Platform(value, transport=httpx.MockTransport(server))
    result = run(platform, value, Dataset(dataset_id="versions", dataset_version=1, cases=[specimen]), tmp_path)
    assert result["gate"] == "PASS"
    assert effects == ["/api/v1/workflows/workflow/versions/2/runs"]
    platform.close()


def test_a20_is_a_task_specification_not_a_claim_of_twenty_passes():
    path = Path(__file__).parents[1] / "datasets/a20.json"
    dataset = Dataset.model_validate_json(path.read_text(encoding="utf-8"))
    assert len(dataset.cases) == 20
    assert {mode: sum(row.mode == mode for row in dataset.cases) for mode in ("ask", "work", "workflow")} == {
        "ask": 5, "work": 8, "workflow": 7}
    assert len({row.input for row in dataset.cases}) == 20
    assert any(row.dependencies for row in dataset.cases)
