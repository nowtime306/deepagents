"""KB v3 lifecycle regressions using original verified MockTransport facts; no real target."""

import copy
import json
from pathlib import Path

import pytest
from test_fixed_knowledge_profiles import fixed_run, harness, module


def snapshot(tmp_path, monkeypatch):
    assert (
        Path(module.__file__).resolve()
        == Path(fixed_run.__file__).with_name("fixed_knowledge_evidence.py")
    )
    monkeypatch.setattr(
        fixed_run,
        "CATALOG_PATH",
        Path(
            str(Path(__file__).resolve().parents[1] / "datasets/a20-fixed.json")
        ),
    )
    command, output, *_ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(
        row for row in fixed_run.catalog().cases if row.case_id == "AET-A03"
    )
    return (
        copy.deepcopy(module.verified(output, data, attempt, specimen)),
        specimen,
        attempt,
    )


def renumber(events):
    for index, event in enumerate(events, 1):
        event["event_id"] = index


@pytest.mark.parametrize("mutation", ["reversed_tool_events", "extra_tool_event"])
def test_invalid_tool_lifecycle_cannot_pass(tmp_path, monkeypatch, mutation):
    value, specimen, attempt = snapshot(tmp_path, monkeypatch)
    events = value["body"]["events"]
    tools = [
        index
        for index, event in enumerate(events)
        if event["event"].startswith("tool.")
    ]
    assert len(tools) == 2
    if mutation == "reversed_tool_events":
        left, right = tools
        events[left], events[right] = events[right], events[left]
    else:
        events.insert(
            -1,
            {
                "event_id": 0,
                "event": "tool.unrecognized",
                "name": "execute",
                "call_id": "ae-1111111111111111",
            },
        )
    renumber(events)
    with pytest.raises((ValueError, module.adapters.Blocked)):
        module.evaluate(value, specimen, attempt)


@pytest.mark.parametrize("metadata", ["message.completed", "message.delta"])
def test_normal_non_tool_completion_or_streaming_metadata_is_preserved(
    tmp_path, monkeypatch, metadata
):
    value, specimen, attempt = snapshot(tmp_path, monkeypatch)
    events = value["body"]["events"]
    events.insert(-1, {"event_id": 0, "event": metadata})
    renumber(events)
    facts, _ = module.evaluate(value, specimen, attempt)
    assert facts and all(result.status == "PASS" for result in facts)
    assert events[-2]["event"] == metadata
