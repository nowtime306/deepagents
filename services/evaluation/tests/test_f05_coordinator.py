"""Pure coordinator/driver budget helpers; never invoke their process or database entrypoints."""

import sys
from uuid import uuid4

import pytest
from f05_support import ROOT, load_script

from evaluation_runner import fixed_run

sys.path.insert(0, str(ROOT / "scripts"))
suite = load_script("evaluation-suite.py")
from evaluation_runner import platform_driver as driver


def test_initialized_roots_are_not_erased_by_started_false_and_unknown_fixture_write_is_visible():
    draft, parent, main = str(uuid4()), str(uuid4()), str(uuid4())
    attempt = {}
    suite.observe_started(attempt, {"attempts": [{"started": False,
        "fixture": {"versions": [{"init_run_id": draft}, {"init_run_id": parent}]},
        "flow_code_roots": {"draft": {"run_id": draft}}, "prior_run_refs": {"main": {"id": main}},
        "fixture_intents": {"unknown-work-start": {"status": "UNKNOWN"}}, "submission_intents": {}}]})
    assert attempt == {"business_started": True, "root_run_ids": sorted([draft, parent, main]), "unknown_submission": True}


def test_no_root_reference_is_not_synthesized_from_created_resource_or_client_run_id():
    attempt = {}
    suite.observe_started(attempt, {"attempts": [{"started": False, "client_run_id": str(uuid4()),
        "created_resources": [{"id": str(uuid4())}], "fixture_intents": {"known-reject": {"status": "REJECTED"}}}]})
    assert attempt == {"business_started": False, "root_run_ids": [], "unknown_submission": False}


def test_parent_reserves_ten_before_dispatch_and_never_silently_raises_nine():
    case = next(case for case in fixed_run.catalog().cases if case.case_id == "AET-F05")
    ledger = suite.Ledger(20, 20000, 9, 300)
    assert ledger.reserve(case, 10, 0) is False and ledger.reserved["sandboxes"] == 0
    ledger = suite.Ledger(20, 20000, 10, 300)
    assert ledger.reserve(case, 10, 0) is True and ledger.reserved["sandboxes"] == 10


@pytest.mark.parametrize("budget,passed", [(2, False), (9, False), (10, True), (600, True), (601, False)])
def test_proposed_driver_uses_real_descriptor_sum_and_preserves_600_ceiling(budget, passed):
    dataset = fixed_run.catalog()
    specimen = next(case for case in dataset.cases if case.case_id == "AET-F05")
    selected = dataset.model_copy(update={"cases": [specimen]})
    request = {"schema_version": 1, "run_id": str(uuid4()), "owner_id": str(uuid4()), "candidate_commit": "a" * 40,
        "definition": selected.model_dump(mode="json"), "budgets": {"max_seconds": 300, "max_model_calls": 20,
        "max_tokens": 20000, "max_sandboxes": budget}, "repeats": 1, "version_set": {}}
    if passed:
        actual, _ = driver.request_definition(request)
        assert actual.cases[0].case_id == "AET-F05"
    else:
        with pytest.raises(ValueError):
            driver.request_definition(request)
