import json

import httpx
import pytest
from test_core import attestation, case, target

from evaluation_runner import engine
from evaluation_runner.contracts import Budget, Check, Dataset
from evaluation_runner.facts import verify


@pytest.mark.parametrize("evidence", [
    {"run": {"status": "SUCCEEDED"}},
    {"observed": ["run"], "operations": [], "run": {"status": "SUCCEEDED"}},
    {"observed": ["operations"], "run": {"status": "SUCCEEDED"}},
])
def test_r1_missing_operation_evidence_is_not_zero(evidence):
    result = verify(Check(registered_id="operation_count", parameters={"expected": 0}), evidence, None)
    assert result.status == "BLOCKED"


def test_r1_authoritatively_observed_empty_operations_is_zero():
    result = verify(Check(registered_id="operation_count", parameters={"expected": 0}),
                    {"observed": ["operations"], "operations": []}, None)
    assert result.status == "PASS"


@pytest.mark.parametrize("limits", [(0, 0), (4, 1000), (5, 999)])
def test_r2_work_budget_below_platform_minimum_rejected_before_any_post(tmp_path, limits):
    value = target()
    calls = []

    def server(request):
        calls.append(request.method)
        return httpx.Response(200, json=attestation(value))

    from evaluation_runner.client import Platform
    platform = Platform(value, transport=httpx.MockTransport(server))
    specimen = case()
    specimen.budgets = Budget(max_model_calls=limits[0], max_tokens=limits[1])
    result = engine.run(platform, value, Dataset(dataset_id="minimum", dataset_version=1, cases=[specimen]), tmp_path)
    assert "POST" not in calls
    assert result["gate"] == "NEEDS_REVIEW"
    assert result["attempts"][0]["started"] is False
    platform.close()


@pytest.mark.parametrize("mode", ["ask", "workflow"])
def test_r2_no_server_proof_means_no_dispatch(mode):
    from evaluation_runner.adapters import Blocked, enforce_budget
    specimen = case(mode)
    with pytest.raises(Blocked):
        enforce_budget(specimen, {})


def test_r2_multi_turn_reserves_all_root_caps_and_rejects_zero():
    from evaluation_runner.adapters import Blocked, enforce_budget
    specimen = case("ask")
    specimen.case_id = "AET-A02"
    specimen.fixture_parameters = {"prior_messages": ["first"]}
    proof = attestation(target())
    specimen.budgets = Budget(max_model_calls=1, max_tokens=30)
    with pytest.raises(Blocked):
        enforce_budget(specimen, proof)
    specimen.budgets = Budget(max_model_calls=2, max_tokens=30)
    enforce_budget(specimen, proof)
    specimen.budgets = Budget(max_model_calls=0, max_tokens=0)
    with pytest.raises(Blocked):
        enforce_budget(specimen, proof)


def pipeline_server(value, effects, *, unknown=False):
    def handle(request):
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=attestation(value))
        if request.method == "POST":
            effects.append((path, json.loads(request.content)))
            return httpx.Response(202, json={"id": "task"} if path == "/api/v1/work-tasks" else {"run_id": "run"})
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": {"calls": 1, "reported_calls": 0 if unknown else 1,
                                                       "unknown_calls": int(unknown), "pending_calls": 0,
                                                       "input_tokens": None if unknown else 10,
                                                       "output_tokens": None if unknown else 5}})
        if path.endswith(("/artifacts", "/operations")):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"status": "COMPLETED"})
    return handle


def test_r2_work_budget_is_submitted_exactly_and_unknown_usage_stops_next_case(tmp_path):
    from evaluation_runner.client import Platform
    value = target()
    effects = []
    specimen = case()
    specimen.budgets = Budget(max_model_calls=5, max_tokens=1000)
    second = case()
    second.case_id = "AET-W02"
    value.resource_bindings[second.case_id] = "fake2"
    platform = Platform(value, transport=httpx.MockTransport(pipeline_server(value, effects, unknown=True)))
    result = engine.run(platform, value, Dataset(dataset_id="usage", dataset_version=1, cases=[specimen, second]), tmp_path)
    assert len(effects) == 2
    assert effects[1][1]["step_limit"] == 5
    assert effects[1][1]["token_limit"] == 1000
    assert result["unexecuted_attempts"] == 1
    assert result["attempts"][0]["usage_completeness"] == "UNKNOWN"
    assert result["gate"] == "FAIL"  # The already-observed missing file is not erased by unknown usage.
    platform.close()


def test_r3_required_fact_fail_is_durable_when_metric_raises(tmp_path, monkeypatch):
    from evaluation_runner.client import Platform
    value = target()
    effects = []
    specimen = case()
    specimen.metrics = [Check(registered_id="tool_correctness")]
    platform = Platform(value, transport=httpx.MockTransport(pipeline_server(value, effects)))

    def broken_metric(*args):
        saved = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
        assert saved["attempts"][0]["fact_results"][0]["status"] == "FAIL"
        raise RuntimeError("Controlled scorer failure")

    monkeypatch.setattr(engine, "score", broken_metric)
    result = engine.run(platform, value, Dataset(dataset_id="scorer", dataset_version=1, cases=[specimen]), tmp_path)
    attempt = result["attempts"][0]
    assert attempt["fact_results"][0]["status"] == "FAIL"
    assert attempt["metric_results"][0]["status"] == "ERROR"
    assert attempt["task_outcome"] == result["gate"] == "FAIL"
    platform.close()
