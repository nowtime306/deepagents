"""Versioned CLI definitions and immutable control evidence; no service connections."""

import copy
import json
import shutil
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

import pytest

from evaluation_runner import fixed_run
from evaluation_runner.profiles_v2.lifecycle import control_report
from evaluation_runner.profiles_v2.registry import CASES, selection
from evaluation_runner.reports import write_report


@pytest.mark.parametrize("case_id", CASES)
def test_default_v1_retains_original_definition_and_unimplemented_claim(case_id):
    specimen, dataset, description = selection(case_id)
    assert description == fixed_run.describe(specimen, dataset)
    assert description["implemented"] is False
    assert description["rules_id"] == "controlled-a20-v1"


@pytest.mark.parametrize("case_id", CASES)
def test_explicit_v2_is_a_separate_cli_only_definition(case_id):
    specimen, dataset, description = selection(case_id, 2)
    assert specimen.fixture_profile.version == dataset.dataset_version == 2
    assert description["status"] == "CLI_CONTROLLED_ONLY"
    assert description["rules_id"] == "controlled-a20-v2"
    assert description["business_admission"] == "NOT_GRANTED_BY_PROFILE_REGISTRATION"


@pytest.mark.parametrize("version", [True, None, "2", 0, 3, "arbitrary.module"])
def test_unregistered_version_is_rejected(version):
    with pytest.raises(ValueError):
        selection("AET-W08", version)


def test_changed_handler_is_refused_before_import(tmp_path):
    shutil.copytree(files("evaluation_runner.profiles_v2"), tmp_path / "copy")
    package = tmp_path / "copy"
    with (package / "w08/handler.py").open("ab") as stream:
        stream.write(b"\n")
    with patch("evaluation_runner.profiles_v2.registry.files", return_value=package), pytest.raises(
        ValueError, match="source changed"
    ):
        selection("AET-W08", 2)


def fixture(case_id, tmp_path):
    source = Path(__file__).with_name("fixtures") / "profiles_v2" / case_id.lower()
    directory = tmp_path / "case"
    shutil.copytree(source, directory)
    data = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))["data"]
    return directory, data


@pytest.mark.parametrize("case_id", ["AET-W07", "AET-W08", "AET-F07"])
def test_control_verdict_retains_unknown_business_cost_and_cleanup(case_id, tmp_path):
    directory, data = fixture(case_id, tmp_path)
    original = copy.deepcopy(data)
    assert control_report(directory, data)["control_gate"] == "PASS"
    report = write_report(directory, data)
    assert report["control_gate"] == "PASS"
    assert report["gate"] == ("PASS" if case_id == "AET-W08" else "NEEDS_REVIEW")
    assert data == original
    if case_id != "AET-W08":
        attempt = data["attempts"][0]
        assert attempt["cleanup_status"] != "CLEAN"
        assert attempt["cost"] is None
        assert "NEEDS_REVIEW" in (directory / "results.xml").read_text(encoding="utf-8")


@pytest.mark.parametrize("damage", ["partial", "missing", "unknown", "cleanup", "tokens", "version"])
def test_uncertain_or_reinterpreted_record_never_passes_control_gate(damage, tmp_path):
    directory, data = fixture("AET-F07", tmp_path)
    attempt = data["attempts"][0]
    if damage == "partial":
        data["expected_attempts"] = 2
    if damage == "missing":
        attempt.pop("v2_cleanup")
    if damage == "unknown":
        attempt["versioned_submission_unknown"] = True
    if damage == "cleanup":
        attempt["cleanup_status"] = "CLEAN"
    if damage == "tokens":
        attempt["usage"]["input_tokens"] = 0
    if damage == "version":
        attempt["profile_version"] = 1
    assert control_report(directory, data)["control_gate"] == "NEEDS_REVIEW"


def test_cross_version_suite_context_cannot_adopt_original_definition():
    from types import SimpleNamespace

    specimen, dataset, _ = selection("AET-W08", 2)
    with pytest.raises(ValueError):
        fixed_run.suite_context(
            None,
            specimen,
            dataset,
            SimpleNamespace(platform_commit="a" * 40),
            {"schema_version": 1, "profile_version": 1, "rules_id": "controlled-a20-v1"},
        )
