"""Protocol/ASGI substitutes for platform metadata only; no PG, worker or evaluation task."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import agent_evaluation_platform_api as api
from app import agent_evaluation_platform_queue as queue
from app.agent_evaluation_platform_admission import Admission, Publication, checked_report, service
from app.agent_evaluation_platform_store import cursor_decode
from app.agent_evaluation_platform_types import (
    CandidateInput,
    DatasetInput,
    Definition,
    RunInput,
    candidate_preview,
    coverage,
)
from app.errors import APIError, fail

ROOT = Path(__file__).resolve().parents[4]


def definition():
    value = json.loads((ROOT / "services/evaluation/datasets/a20-fixed.json").read_text(encoding="utf-8"))
    value["cases"] = [next(case for case in value["cases"] if case["case_id"] == "AET-A02")]
    return value


@pytest.mark.parametrize(
    "payload",
    [
        {"owner_id": str(uuid4())},
        {"command": ["arbitrary"]},
        {"path": "C:/private"},
        {"token": "raw"},
        {"report": {"gate": "PASS"}},
        {"lease_token": str(uuid4())},
    ],
)
def test_run_body_cannot_set_authority_execution_or_results(payload):
    value = {"dataset_id": str(uuid4()), "version": 1, "request_id": str(uuid4()), **payload}
    with pytest.raises(ValidationError):
        RunInput.model_validate(value)


@pytest.mark.parametrize(
    "mode,repeats", [("engineering", 2), ("engineering", 3), ("effect", 1), ("effect", 2), ("engineering", True)]
)
def test_repeats_do_not_weaken_engineering_or_three_effect_repeats(mode, repeats):
    with pytest.raises(ValidationError):
        RunInput(dataset_id=uuid4(), version=1, request_id=uuid4(), execution_mode=mode, repeats=repeats)


def test_dataset_normalization_preserves_core_definition_for_a_valid_declared_case():
    from evaluation_runner.contracts import Dataset

    value = definition()
    assert Definition.model_validate(value).model_dump(mode="json") == Dataset.model_validate(value).model_dump(
        mode="json"
    )
    status = coverage(Definition.model_validate(value))
    assert status["semantic_review_status"] == "PENDING" and status["acceptance_complete"] is False


@pytest.mark.parametrize("field,value", [("revision", True), ("owner_id", str(uuid4()))])
def test_dataset_writes_reject_boolean_revision_and_client_owner(field, value):
    body = {"name": "测试集", "revision": 0, "definition": definition(), field: value}
    with pytest.raises(ValidationError):
        DatasetInput.model_validate(body)


@pytest.mark.parametrize("number", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_fixture_data_is_not_persistable(number):
    value = definition()
    value["cases"][0]["fixture_parameters"] = {"score": number}
    with pytest.raises(ValidationError):
        Definition.model_validate(value)


def test_incomplete_or_self_asserted_publication_does_not_admit_execution():
    owner = uuid4()
    assert Admission().inspect(owner)["status"] == "BLOCKED"
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(agent_evaluation_platform_admission={"stage_a_accepted": True, "runner_ready": True})
        )
    )
    assert service(request).inspect(owner)["stage_a_accepted"] is False
    receipt = json.dumps({"schema_version": 1, "gate": "PASS", "independent_review": "CLOSED", "cases": []}).encode()
    pub = Publication(receipt, hashlib.sha256(receipt).hexdigest(), lambda _: True)
    assert pub.valid() is False
    forged = Publication(receipt, "0" * 64, lambda _: True)
    assert forged.valid() is False


def test_candidate_preview_removes_obvious_secrets_and_never_returns_raw_material():
    body = CandidateInput(
        name="失败候选",
        authorization_note="本人授权",
        redaction_confirmed=True,
        material={
            "input": "Authorization: Bearer abcdefghijklmnop email tester@example.com https://name:password@local/a?token=secret",
            "expected_behavior": "password=super-secret must not leak",
        },
    )
    value = candidate_preview(body)
    encoded = json.dumps(value)
    for secret in ("abcdefghijklmnop", "tester@example.com", "name:password", "super-secret", "token=secret"):
        assert secret not in encoded
    assert value["redaction_summary"]["removed_fields"]


@pytest.mark.parametrize("cursor", ["not-a-cursor", "..", "A" * 600])
def test_invalid_cursor_has_no_path_or_query_interpretation(cursor):
    with pytest.raises(APIError) as captured:
        cursor_decode(cursor)
    assert captured.value.status == 422


def test_a_verified_flag_cannot_make_foreign_or_replaced_report_exportable():
    owner, run = uuid4(), uuid4()
    files = {"json": "{}", "junit": "<testsuite/>", "markdown": "report"}
    manifest = {
        "kind": "agent-platform-report-v1",
        "run_id": str(run),
        "owner_id": str(owner),
        "ready_verified": True,
        "files_sha256": {key: hashlib.sha256(text.encode()).hexdigest() for key, text in files.items()},
    }
    row = {"manifest": manifest, "files": files, "verified": True, "redacted": True}
    assert checked_report(row, run, owner)[1] == files
    with pytest.raises(APIError):
        checked_report(row, run, uuid4())
    files["json"] = '{"gate":"PASS"}'
    with pytest.raises(APIError):
        checked_report(row, run, owner)


def harness(monkeypatch, *, permissions=None):
    owner, dataset = uuid4(), uuid4()
    writes = []
    required = []
    allowed = (
        permissions
        if permissions is not None
        else {"evaluation.read", "evaluation.run", "chat.read", "chat.run", "agents.manage"}
    )

    async def identity(request):
        return {"id": owner}

    async def require(database, identifier, permission):
        required.append(permission)
        assert identifier == owner
        if permission not in allowed:
            fail(403, "PERMISSION_REQUIRED", "未授权")
        return {"id": owner, "permissions": list(allowed), "is_admin": False}

    class Store:
        def __init__(self, db):
            self.db = db

        async def require_schema(self):
            pass

        async def installed(self):
            return True

        async def version(self, identifier, version, actor):
            assert actor == owner
            if identifier != dataset:
                fail(404, "NOT_FOUND", "版本不存在")
            return {
                "id": uuid4(),
                "definition": Definition.model_validate(definition()).model_dump(mode="json"),
                "semantic_review_status": "PENDING",
            }

        async def prior_request(self, *args):
            return None

        async def enqueue(self, *args):
            writes.append(args)
            raise AssertionError("Blocked admission cannot enqueue")

    monkeypatch.setattr(api, "identity", identity)
    monkeypatch.setattr(api, "require_account", require)
    monkeypatch.setattr(queue, "require_account", require)
    monkeypatch.setattr(api, "Store", Store)
    app = FastAPI()
    app.state.db = SimpleNamespace()
    app.include_router(api.router)

    @app.exception_handler(APIError)
    async def error(request, value):
        return JSONResponse({"error": {"code": value.code, "message": value.message}}, status_code=value.status)

    async def request(method, path, body=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, json=body)

    return request, owner, dataset, writes, required


def test_default_admission_returns503_and_creates_no_empty_batch_or_samples(monkeypatch):
    request, _, dataset, writes, required = harness(monkeypatch)
    response = asyncio.run(
        request(
            "POST",
            "/api/v1/agent-evaluations/runs",
            {"dataset_id": str(dataset), "version": 1, "request_id": str(uuid4()), "repeats": 1},
        )
    )
    assert response.status_code == 503 and response.json()["error"]["code"] == "EVALUATION_STAGE_A_NOT_ACCEPTED"
    assert writes == [] and "evaluation.run" in required and "evaluation.manage" not in required


def test_foreign_dataset_is404_even_while_global_execution_is_blocked(monkeypatch):
    request, _, _, writes, _ = harness(monkeypatch)
    response = asyncio.run(
        request(
            "POST",
            "/api/v1/agent-evaluations/runs",
            {"dataset_id": str(uuid4()), "version": 1, "request_id": str(uuid4())},
        )
    )
    assert response.status_code == 404 and writes == []


def test_read_role_cannot_submit_and_no_public_report_upload_exists(monkeypatch):
    request, _, dataset, writes, _ = harness(monkeypatch, permissions={"evaluation.read"})
    response = asyncio.run(
        request(
            "POST",
            "/api/v1/agent-evaluations/runs",
            {"dataset_id": str(dataset), "version": 1, "request_id": str(uuid4())},
        )
    )
    assert response.status_code == 403 and writes == []
    response = asyncio.run(request("POST", f"/api/v1/agent-evaluations/runs/{uuid4()}/report", {"gate": "PASS"}))
    assert response.status_code == 405


def test_capabilities_reports_real_closed_admission_and_explicit_budget_defaults(monkeypatch):
    request, _, _, writes, _ = harness(monkeypatch)
    response = asyncio.run(request("GET", "/api/v1/agent-evaluations/capabilities"))
    value = response.json()
    assert response.status_code == 200 and value["admission"]["status"] == "BLOCKED"
    assert value["default_budgets"] == {
        "max_seconds": 300,
        "max_model_calls": 20,
        "max_tokens": 20000,
        "max_sandboxes": 2,
    }
    assert value["thresholds"]["effect_repeats"] == 3 and value["thresholds"]["b100"]["total"] == 100 and writes == []


def test_sample_revision_is_real_and_missing_usage_is_not_fabricated_zero(monkeypatch):
    request, owner, _, _, _ = harness(monkeypatch)
    run_id, sample_id = uuid4(), uuid4()

    async def sample(self, run, identifier, actor):
        assert (run, identifier, actor) == (run_id, sample_id, owner)
        return {"id": sample_id, "run_id": run_id, "revision": 7, "usage": None, "task_outcome": "NOT_RUN"}

    async def review(self, *args):
        return None

    monkeypatch.setattr(api.Store, "sample", sample, raising=False)
    monkeypatch.setattr(api.Store, "latest_review", review, raising=False)
    response = asyncio.run(request("GET", f"/api/v1/agent-evaluations/runs/{run_id}/samples/{sample_id}"))
    value = response.json()
    assert response.status_code == 200 and value["revision"] == 7
    assert value["usage"] == {"calls": None, "input_tokens": None, "output_tokens": None, "completeness": "NOT_RUN"}


def test_aggregate_budget_preserves_original_case_limits_and_separate_reservations():
    from app.agent_evaluation_platform_types import default_budget

    value = Definition.model_validate(definition())
    case = value.cases[0]
    assert default_budget(value, 3) == {
        "max_seconds": case.budgets.timeout_seconds * 3,
        "max_model_calls": case.budgets.max_model_calls * 3,
        "max_tokens": case.budgets.max_tokens * 3,
        "max_sandboxes": 6,
    }


def test_unknown_profile_is_schema_valid_but_explicitly_unverified_and_blocked(monkeypatch):
    request, _, _, writes, _ = harness(monkeypatch, permissions={"evaluation.read", "evaluation.manage"})
    data = definition()
    data["cases"][0]["fixture_profile"] = {"id": "unknown-profile", "version": 99}
    response = asyncio.run(
        request(
            "POST", "/api/v1/agent-evaluations/datasets/preview", {"name": "未登记", "definition": data, "revision": 0}
        )
    )
    result = response.json()
    assert response.status_code == 200 and result["schema_valid"] is True and writes == []
    readiness = result["execution_readiness"]
    assert readiness["status"] == "BLOCKED" and readiness["parameters_schema_verified"] is False
    assert readiness["missing_profiles"] == ["unknown-profile@99"]


def test_publication_without_driver_is_explicitly_nonexecutable(monkeypatch):
    request, _, dataset, _, _ = harness(monkeypatch, permissions={"evaluation.read", "evaluation.manage"})

    async def publish(self, identifier, owner, body):
        return {"id": uuid4(), "dataset_id": identifier, "version": 1, "definition": definition()}

    monkeypatch.setattr(api.Store, "publish", publish, raising=False)
    response = asyncio.run(request("POST", f"/api/v1/agent-evaluations/datasets/{dataset}/versions", {"revision": 1}))
    assert response.status_code == 201 and response.json()["executable"] is False
    assert response.json()["execution_readiness"]["parameters_schema_verified"] is False


def publication_v2():
    from app.agent_evaluation_platform_admission import EXPECTED

    rows = [
        {
            "case_id": case,
            "acceptance_verdict": "PASS",
            "actual_task_outcome": "PASS",
            "actual_gate": "PASS",
            "expected_gate": "PASS",
            "actual_cleanup": "CLEAN",
            "expected_cleanup": "CLEAN",
            "evidence_complete": True,
            "cleanup_verified": True,
            "evidence_digest": "a" * 64,
            "definition_digest": "b" * 64,
        }
        for case in sorted(EXPECTED)
    ]
    row = next(value for value in rows if value["case_id"] == "AET-W01")
    row.update(actual_task_outcome="FAIL", actual_gate="FAIL", expected_gate="FAIL")
    return {
        "schema_version": 2,
        "kind": "agent-evaluation-stage-a-publication-v2",
        "acceptance_verdict": "PASS",
        "independent_review": "CLOSED",
        "cases": rows,
    }


def valid_publication_contract(value, verifier=lambda _: True):
    raw = json.dumps(value).encode()
    return Publication(raw, hashlib.sha256(raw).hexdigest(), verifier).valid()


def test_publication_v2_preserves_negative_task_fail_and_separates_acceptance():
    value = publication_v2()
    assert valid_publication_contract(value) is True
    negative = next(row for row in value["cases"] if row["case_id"] == "AET-W01")
    assert negative["actual_task_outcome"] == negative["actual_gate"] == "FAIL"
    assert valid_publication_contract(value, lambda _: False) is False


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("actual_gate", "PASS"),
        ("acceptance_verdict", "FAIL"),
        ("evidence_complete", False),
        ("evidence_digest", None),
        ("cleanup_verified", False),
    ],
)
def test_expected_negative_control_cannot_excuse_different_or_unverified_actual_evidence(field, replacement):
    value = publication_v2()
    negative = next(row for row in value["cases"] if row["case_id"] == "AET-W01")
    negative[field] = replacement
    assert valid_publication_contract(value) is False


@pytest.mark.parametrize(
    "answer", [{}, {"status": "BLOCKED", "parameters_schema_verified": False}, "unavailable", 1, True, False, None, []]
)
def test_only_literal_true_from_registered_definition_verifier_can_mark_fixed_parameters_verified(answer):
    class Driver:
        def validate_definition(self, owner, definition, repeats):
            return answer

        def readiness(self, owner):
            return {
                key: True
                for key in (
                    "ready",
                    "source_verified",
                    "lease_enforced",
                    "owner_permissions_per_effect",
                    "cooperative_cancel",
                    "ready_reader",
                    "precise_cleanup",
                )
            }

    raw = json.dumps(publication_v2()).encode()
    pub = Publication(raw, hashlib.sha256(raw).hexdigest(), lambda _: True)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_evaluation_platform_admission=Admission(pub, Driver())))
    )
    readiness = api.definition_readiness(definition(), request, uuid4())
    assert readiness["parameters_schema_verified"] is (answer is True)
    assert readiness["status"] == ("READY" if answer is True else "BLOCKED")
