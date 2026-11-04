"""Comparison and candidate HTTP protocol tests; persistence is a scoped substitute, not PostgreSQL."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import agent_evaluation_platform_api as core
from app import agent_evaluation_platform_extension_api as api
from app.agent_evaluation_platform_admission import compare_reports
from app.errors import APIError, fail


def material():
    return {
        "dataset_digest": "a" * 64,
        "rules_digest": "b" * 64,
        "material_digest": "c" * 64,
        "repeats": 1,
        "execution_mode": "engineering",
        "model_identity": {
            "complete": True,
            "models": [
                {
                    "model_id": "11111111-1111-4111-8111-111111111111",
                    "effect_fingerprint": "d" * 64,
                    "kind": "chat",
                    "protocol": "openai",
                }
            ],
        },
        "business_identity": {
            "complete": True,
            "resources": [
                {
                    "kind": "agent",
                    "resource_id": "22222222-2222-4222-8222-222222222222",
                    "version": 1,
                    "definition_digest": "e" * 64,
                }
            ],
        },
        "platform_identity": {
            "complete": True,
            "platform_commit": "f" * 40,
            "source_digest": "1" * 64,
            "runner_lock_digest": "2" * 64,
        },
        "comparison_ready": True,
        "evidence_complete": True,
        "samples": [{"case_id": "AET-A02", "repeat_index": 1, "task_outcome": "FAIL"}],
    }


@pytest.mark.parametrize("key", ["dataset_digest", "rules_digest", "material_digest", "repeats", "execution_mode"])
def test_changed_comparison_conditions_never_produce_an_improvement(key):
    left = material()
    right = deepcopy(left)
    right[key] = "different"
    comparable, reasons, summary, changes = compare_reports(left, right, "auto")
    assert comparable is False and reasons and summary is None and changes == []


def test_comparison_preserves_failed_task_and_unknown_not_statistical_significance():
    left = material()
    right = deepcopy(left)
    right["samples"][0]["task_outcome"] = "PASS"
    comparable, reasons, summary, changes = compare_reports(left, right, "auto")
    assert comparable is True and reasons == [] and changes[0]["baseline"] == "FAIL"
    assert summary == {
        "paired_samples": 1,
        "baseline_pass": 0,
        "candidate_pass": 1,
        "statistical_significance": "NOT_ASSESSED",
    }


def test_multiple_changed_axes_and_duplicate_samples_are_not_comparable():
    left = material()
    right = deepcopy(left)
    right["model_identity"]["models"][0]["effect_fingerprint"] = "f" * 64
    right["platform_identity"]["platform_commit"] = "e" * 40
    assert compare_reports(left, right, "model")[0] is False
    right = deepcopy(left)
    right["samples"] *= 2
    assert compare_reports(left, right, "auto")[0] is False


@pytest.mark.parametrize("replacement", [None, {}, {"comparison_ready": True}])
def test_manifest_flags_alone_do_not_supply_comparison_facts(replacement):
    with pytest.raises(APIError):
        api.comparison_material({"comparison": replacement})


@pytest.mark.parametrize("field,value", [("repeat_index", True), ("task_outcome", "SCORED_HIGH"), ("case_id", None)])
def test_comparison_rejects_untyped_sample_facts(field, value):
    value0 = material()
    value0["samples"][0][field] = value
    with pytest.raises(APIError):
        api.comparison_material({"comparison": value0})


def harness(monkeypatch, permissions=None):
    owner = uuid4()
    records = {}
    writes = []
    allowed = permissions or {"evaluation.read", "evaluation.manage"}

    async def identity(request):
        return {"id": owner}

    async def require(db, identifier, permission):
        assert identifier == owner
        if permission not in allowed:
            fail(403, "PERMISSION_REQUIRED", "权限不足")
        return {"id": owner, "permissions": list(allowed)}

    class Store:
        def __init__(self, db):
            self.db = db

        async def require_schema(self):
            pass

        async def latest_review(self, *args):
            return None

        async def one(self, kind, identifier, actor):
            assert actor == owner
            if identifier not in records:
                fail(404, "NOT_FOUND", "记录不存在")
            return records[identifier]

        async def candidate_create(self, actor, body, preview, name, note):
            assert actor == owner
            if body.source_sample_id and body.source_sample_id not in records:
                fail(404, "NOT_FOUND", "来源不属于本人")
            row = {
                "id": uuid4(),
                "name": name,
                "status": "PENDING_REVIEW",
                "revision": 1,
                "created_at": None,
                "source_sample_id": body.source_sample_id,
                "authorization_note": note,
                **preview,
            }
            records[row["id"]] = row
            writes.append(row)
            return row

        async def review(self, actor, kind, identifier, revision, decision, reason):
            row = await self.one(kind, identifier, actor)
            if row["revision"] != revision:
                fail(409, "EVALUATION_REVIEW_CONFLICT", "版本冲突")
            row["revision"] += 1
            row["status"] = decision
            writes.append({"reason": reason, "decision": decision})

    monkeypatch.setattr(core, "identity", identity)
    monkeypatch.setattr(core, "require_account", require)
    monkeypatch.setattr(core, "Store", Store)
    app = FastAPI()
    app.state.db = SimpleNamespace()
    app.include_router(api.router)

    @app.exception_handler(APIError)
    async def error(request, value):
        return JSONResponse({"error": {"code": value.code}}, status_code=value.status)

    async def request(method, path, body=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, json=body)

    return request, records, writes


def body(**changes):
    return {
        "name": "候选",
        "authorization_note": "我已授权",
        "redaction_confirmed": True,
        "material": {
            "input": "Bearer abcdefghijklmnop tester@example.com",
            "expected_behavior": "不能泄漏password=super-secret",
        },
        **changes,
    }


def test_candidate_requires_human_confirmation_before_any_persistence(monkeypatch):
    request, _, writes = harness(monkeypatch)
    response = asyncio.run(
        request("POST", "/api/v1/agent-evaluations/failure-candidates", body(redaction_confirmed=False))
    )
    assert response.status_code == 422 and writes == []


def test_candidate_preview_is_nonpersistent_and_create_only_stores_redacted_material(monkeypatch):
    request, _, writes = harness(monkeypatch)
    path = "/api/v1/agent-evaluations/failure-candidates"
    response = asyncio.run(request("POST", path + "/preview", body()))
    assert response.status_code == 200 and writes == []
    response = asyncio.run(request("POST", path, body()))
    assert response.status_code == 201 and len(writes) == 1
    assert "abcdefghijklmnop" not in str(writes) and "tester@example.com" not in str(writes)
    assert "super-secret" not in str(writes) and "material" not in writes[0]


def test_foreign_candidate_source_and_foreign_detail_return404(monkeypatch):
    request, _, writes = harness(monkeypatch)
    base = "/api/v1/agent-evaluations/failure-candidates"
    response = asyncio.run(request("POST", base, body(source_sample_id=str(uuid4()))))
    assert response.status_code == 404 and writes == []
    assert asyncio.run(request("GET", base + "/" + str(uuid4()))).status_code == 404


def test_candidate_review_uses_actual_revision_and_does_not_execute_or_mutate_sample(monkeypatch):
    request, _, writes = harness(monkeypatch)
    base = "/api/v1/agent-evaluations/failure-candidates"
    response = asyncio.run(request("POST", base, body()))
    identifier = response.json()["id"]
    response = asyncio.run(
        request(
            "POST", base + "/" + identifier + "/review", {"revision": 1, "decision": "APPROVED", "reason": "人工确认"}
        )
    )
    assert response.status_code == 201 and response.json()["revision"] == 2 and response.json()["status"] == "APPROVED"
    count = len(writes)
    response = asyncio.run(
        request("POST", base + "/" + identifier + "/review", {"revision": 1, "decision": "REJECTED", "reason": "过期"})
    )
    assert response.status_code == 409 and len(writes) == count


def test_read_permission_cannot_create_candidate(monkeypatch):
    request, _, writes = harness(monkeypatch, {"evaluation.read"})
    response = asyncio.run(request("POST", "/api/v1/agent-evaluations/failure-candidates", body()))
    assert response.status_code == 403 and writes == []


@pytest.mark.parametrize("prefix", ["中文前缀", " ", "说明：", ""])
def test_secret_assignments_with_unicode_prefix_are_redacted(prefix):
    from app.agent_evaluation_platform_types import redact

    value, labels = redact(prefix + "password=super-secret")
    assert "super-secret" not in value and "secret_assignment" in labels


@pytest.mark.parametrize("indices", [[1], [1, 2], [1, 2, 2], [1, 2, 3, 3]])
def test_effect_comparison_requires_every_unique_independent_repeat(indices):
    value = material()
    value.update(execution_mode="effect", repeats=3)
    value["samples"] = [{**value["samples"][0], "repeat_index": index} for index in indices]
    with pytest.raises(APIError):
        api.comparison_material({"comparison": value})
    assert compare_reports(value, deepcopy(value), "auto")[0] is False


def test_complete_three_repeat_comparison_preserves_all_three_outcomes():
    left = material()
    left.update(execution_mode="effect", repeats=3)
    left["samples"] = [{**left["samples"][0], "repeat_index": index} for index in (1, 2, 3)]
    right = deepcopy(left)
    right["samples"][0]["task_outcome"] = "PASS"
    comparable, _, summary, changes = compare_reports(left, right, "auto")
    assert comparable is True and summary["paired_samples"] == 3
    assert summary["baseline_pass"] == 0 and summary["candidate_pass"] == 1 and len(changes) == 3


@pytest.mark.parametrize(
    "field",
    ["dataset_digest", "rules_digest", "material_digest", "model_identity", "business_identity", "platform_identity"],
)
@pytest.mark.parametrize("invalid", [None, {}, ""])
def test_null_empty_or_untyped_actual_identity_is_not_comparable(field, invalid):
    left = material()
    left[field] = invalid
    with pytest.raises(APIError):
        api.comparison_material({"comparison": left})
    assert compare_reports(left, deepcopy(left), "auto")[0] is False


@pytest.mark.parametrize("identity", ["model_identity", "business_identity", "platform_identity"])
def test_incomplete_observed_identity_does_not_become_a_comparison(identity):
    left = material()
    left[identity]["complete"] = False
    assert compare_reports(left, deepcopy(left), "auto")[0] is False


def test_zero_provider_identity_requires_explicit_complete_empty_model_list():
    left = material()
    left["model_identity"] = {"complete": True, "models": []}
    assert compare_reports(left, deepcopy(left), "auto")[0] is True
    left["model_identity"] = {"models": []}
    assert compare_reports(left, deepcopy(left), "auto")[0] is False


def test_known_fail_remains_comparable_but_unexecuted_or_unknown_task_does_not():
    left = material()
    assert compare_reports(left, deepcopy(left), "auto")[0] is True
    for outcome in ("NOT_RUN", "NEEDS_REVIEW"):
        right = deepcopy(left)
        right["samples"][0]["task_outcome"] = outcome
        assert compare_reports(left, right, "auto")[0] is False


@pytest.mark.parametrize(
    "header", ["Authorization", "authorization", "aUtHoRiZaTiOn", "中文Authorization", "Proxy-Authorization"]
)
@pytest.mark.parametrize("scheme", ["Basic", "Bearer", "Digest"])
def test_entire_authentication_header_value_is_removed(header, scheme):
    from app.agent_evaluation_platform_types import redact

    result, labels = redact(header + ": " + scheme + " Zm9vOmJhcg== more-auth-data")
    assert "Zm9vOmJhcg==" not in result and "more-auth-data" not in result
    assert "authentication_header" in labels


def test_actual_text_model_id_is_preserved_without_uuid_coercion():
    left = material()
    left["model_identity"]["models"][0]["model_id"] = "test/default"
    value = api.comparison_material({"comparison": left})
    assert value["model_identity"]["models"][0]["model_id"] == "test/default"
    assert compare_reports(value, deepcopy(value), "auto")[0] is True
