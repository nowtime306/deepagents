"""Real API quote/schema functions on selected metadata, no DB/worker/HTTP target."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app import agent_evaluation_platform_api as api
from app import agent_evaluation_platform_types as types
from f05_support import ROOT
from pydantic import ValidationError


def definition():
    value = json.loads((ROOT / "services/evaluation/datasets/a20-fixed.json").read_text(encoding="utf-8"))
    value["cases"] = [case for case in value["cases"] if case["case_id"] == "AET-F05"]
    return types.Definition.model_validate(value)


def test_case_specific_required_quote_preserves_other_case_and_default_budgets():
    value = definition()
    assert types.default_budget(value, 1)["max_sandboxes"] == 10
    assert types.default_budget(value, 3)["max_sandboxes"] == 30
    old = value.model_copy(deep=True)
    old.cases[0].fixture_profile.version = 1
    assert types.default_budget(old, 1)["max_sandboxes"] == 2
    assert api.DEFAULT_BUDGETS["max_sandboxes"] == 2


def test_aggregate_above_existing_600_remains_blocked_instead_of_silently_reducing_or_raising_limit():
    value = definition()
    base = value.cases[0].model_dump(mode="json")
    cases = [base]
    for number in range(1, 100):
        cases.append(
            {
                **base,
                "case_id": f"AET-A{number:02}",
                "mode": "ask",
                "fixture_profile": {"id": "ask-plain", "version": 1},
                "budgets": {"timeout_seconds": 1, "max_model_calls": 0, "max_tokens": 0},
            }
        )
    value = types.Definition.model_validate({**value.model_dump(mode="json"), "cases": cases})
    with pytest.raises(ValidationError):
        types.default_budget(value, 3)  # 99*2*3 + 10*3 = 624: retain the current 600 ceiling.
    quote = {"max_seconds": 300, "max_model_calls": 20, "max_tokens": 20000, "max_sandboxes": 600}
    assert types.RunBudget.model_validate(quote).max_sandboxes == 600
    with pytest.raises(ValidationError):
        types.RunBudget.model_validate({**quote, "max_sandboxes": 601})
    with pytest.raises(ValidationError):
        types.RunBudget.model_validate({**quote, "max_sandboxes": True})


async def test_published_version_quote_uses_immutable_definition_instead_of_draft_count(monkeypatch):
    value = definition().model_dump(mode="json")
    owner, dataset = uuid4(), uuid4()

    class Store:
        async def version(self, identifier, version, requested_owner):
            assert (identifier, version, requested_owner) == (dataset, 1, owner)
            return {"id": str(uuid4()), "dataset_id": str(dataset), "version": 1, "definition": value, "case_count": 1}

    async def access(*args):
        return {"id": owner}, Store()

    monkeypatch.setattr(api, "access", access)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    response = await api.version(dataset, 1, request)
    assert response["budget_quote"]["engineering"]["max_sandboxes"] == 10
    assert response["budget_quote"]["effect"]["max_sandboxes"] == 30
    assert response["execution_readiness"]["status"] == "BLOCKED" and response["executable"] is False
