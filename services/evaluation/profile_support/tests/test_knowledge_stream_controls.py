"""Real run_streams.frame wire shape over MockTransport; no real API/DB/task or supplier."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.run_streams import frame

from evaluation_runner import fixed_knowledge_evidence as module


def wire(run):
    rows = [
        {"event": "run.started", "data": {}},
        {"event": "tool.started", "data": {"name": "search_knowledge", "call_id": "ae-1111111111111111"}},
        {"event": "tool.completed", "data": {"name": "search_knowledge", "call_id": "ae-1111111111111111"}},
        {"event": "run.completed", "data": {}},
    ]
    return [
        frame(row["event"], row["data"], {**row, "event_id": index, "run_id": run}) for index, row in enumerate(rows, 1)
    ]


def snapshot(run, status="SUCCEEDED"):
    # Fields are the actual run_streams.snapshot/run_state projection, not invented native event metadata.
    return frame(
        "stream.snapshot",
        {
            "run": {
                "id": run,
                "conversation_id": str(uuid4()),
                "status": status,
                "created_at": None,
                "claimed_at": None,
                "finished_at": None,
                "error_code": None,
            },
            "diagnostics": {"execution": {}},
            "usage": {"total": {}},
            "team": {"items": []},
        },
    )


def read(run, frames):
    def handle(request):
        assert request.method == "GET" and request.url.path == "/api/v1/runs/" + run + "/events"
        return httpx.Response(200, text="".join(frames), headers={"Content-Type": "text/event-stream"})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://test") as client:
        return module.events(SimpleNamespace(http=client), run)


def test_formal_snapshot_control_is_bound_and_not_counted_as_a_native_event():
    run = str(uuid4())
    frames = wire(run) + [snapshot(run), frame("stream.end", {"status": "SUCCEEDED"})]
    rows = read(run, frames)
    assert [row["event_id"] for row in rows] == [1, 2, 3, 4]
    assert [row["event"] for row in rows] == ["run.started", "tool.started", "tool.completed", "run.completed"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"run": {}},
        {"run": {"id": "foreign", "status": "SUCCEEDED"}},
        {"run": {"status": "SUCCEEDED"}},
        {"run": {"id": None, "status": "SUCCEEDED"}},
    ],
)
def test_foreign_or_malformed_snapshot_is_rejected(payload):
    run = str(uuid4())
    with pytest.raises((ValueError, module.adapters.Blocked)):
        read(run, wire(run) + [frame("stream.snapshot", payload), frame("stream.end", {"status": "SUCCEEDED"})])


@pytest.mark.parametrize("status", ["RUNNING", "FAILED", "CANCELLED", None])
def test_snapshot_must_match_the_original_succeeded_terminal_root(status):
    run = str(uuid4())
    with pytest.raises((ValueError, module.adapters.Blocked)):
        read(run, wire(run) + [snapshot(run, status), frame("stream.end", {"status": "SUCCEEDED"})])


def test_explicit_stream_error_is_blocked_even_after_native_completion():
    run = str(uuid4())
    with pytest.raises(module.adapters.Blocked):
        read(
            run,
            wire(run)
            + [frame("stream.error", {"status_code": 403, "code": "PERMISSION_REQUIRED", "message": "revoked"})],
        )


def test_unknown_stream_control_is_never_accepted_as_a_native_event():
    run = str(uuid4())
    with pytest.raises(ValueError):
        read(run, wire(run) + [frame("stream.unknown", {}), frame("stream.end", {"status": "SUCCEEDED"})])


def test_native_data_after_end_cannot_be_replayed_into_the_sealed_sequence():
    run = str(uuid4())
    extra = frame("run.completed", {}, {"run_id": run, "event_id": 5, "event": "run.completed", "data": {}})
    with pytest.raises(ValueError):
        read(run, wire(run) + [frame("stream.end", {"status": "SUCCEEDED"}), extra])


def test_repeated_valid_snapshot_does_not_change_native_ids_or_tool_projection():
    run = str(uuid4())
    frames = wire(run)
    frames.insert(2, snapshot(run))
    frames += [snapshot(run), frame("stream.end", {"status": "SUCCEEDED"})]
    assert len(read(run, frames)) == 4
