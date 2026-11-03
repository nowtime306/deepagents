"""Behavioral coverage for sensitive recording import and fresh semantic selection."""

import json

import pytest

from app.browser_skill_trace import import_bundle, validate_draft
from app.browser_skill_work import resolve_target
from app.errors import APIError


def bundle(tmp_path, *, redacted=False, op="fill"):
    (tmp_path / "states").mkdir()
    (tmp_path / "states/s1.txt").write_text("@vom 1\nprivate VOM content", encoding="utf-8")
    trace = {
        "version": 3,
        "entry": {"start_url": "https://fixture.test/form"},
        "states": [{"id": "s1", "page": "s1.txt"}],
        "steps": [
            {
                "id": 1,
                "op": op,
                "state": "s1",
                "result": {"state": "s1"},
                "target": {"ref": "e12", "role": "textbox", "name": "Name"},
                "value": "FAKE_PRIVATE_8672",
                "redacted": redacted,
            }
        ],
    }
    (tmp_path / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    return trace


def test_import_discards_raw_values_vom_and_recorded_refs(tmp_path):
    bundle(tmp_path)
    draft = import_bundle(tmp_path)
    text = json.dumps(draft)
    assert "FAKE_PRIVATE_8672" not in text
    assert "private VOM" not in text
    assert "e12" not in text
    assert draft["steps"][0]["op"] == "navigate"
    assert draft["steps"][1]["parameter"] == "input_1"
    validate_draft(draft)


@pytest.mark.parametrize("variant", ["password", "unsupported", "missing_state", "traversal", "v2"])
def test_invalid_or_unreplayable_cannot_publish(tmp_path, variant):
    trace = bundle(tmp_path, redacted=variant == "password", op="scroll" if variant == "unsupported" else "fill")
    if variant == "missing_state":
        trace["steps"][0]["result"]["state"] = "s99"
    elif variant == "traversal":
        trace["states"][0]["page"] = "../private.txt"
    elif variant == "v2":
        trace["version"] = 2
    (tmp_path / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    with pytest.raises(APIError):
        validate_draft(import_bundle(tmp_path))


def test_unique_current_target_and_context_required():
    target = {"role": "button", "name": "Save", "ctx": "Form"}
    text = '@vom 1\n @e42 button "Save" [ctx: Form]\n @e43 button "Save" [ctx: Other]'
    assert resolve_target(text, target) == "@e42"
    with pytest.raises(APIError):
        resolve_target(text + '\n @e44 button "Save" [ctx: Form]', target)
    with pytest.raises(APIError):
        resolve_target('@vom 1\n @e44 button "Delete" [ctx: Form]', target)
