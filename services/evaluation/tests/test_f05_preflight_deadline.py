"""F05 preparatory readiness cannot stand in for a bound business deadline."""

import json

import pytest
from test_f05_flow_code import Fixture, command


def test_preflight_is_ready_for_init_but_has_no_business_deadline_or_effect(tmp_path, monkeypatch):
    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "preflight") == 0
    proof = json.loads((tmp_path / "output/fixed-preflight.json").read_text(encoding="utf-8"))
    assert proof["status"] == "READY_FOR_INIT"
    assert fixture.posts == [] and not (tmp_path / "case-deadline.json").exists()


@pytest.mark.parametrize("fault", ["installed", "boundary", "hash"])
def test_uninstalled_or_different_actual_target_deadline_blocks_before_model_creation(tmp_path, monkeypatch, fault):
    fixture = Fixture()
    original = fixture.deadline_capability

    def invalid():
        value = original()
        if fault == "installed":
            value["installed"] = False
            value["dispatch_allowed"] = False
        elif fault == "boundary":
            value["boundaries"]["sandbox"] = False
            value["dispatch_allowed"] = False
        else:
            value["record_sha256"] = "0" * 64
        return value

    fixture.deadline_capability = invalid
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert fixture.posts == [] and fixture.roots == {}
