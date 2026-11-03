"""No-progress stopping and context telemetry do not expose user/tool payloads."""

from langchain_core.messages import AIMessage
from test_api import ORIGIN
from test_api import client as client

from tests.unit_tests.test_harness import ToolModel


def test_identical_tool_results_stop_before_another_execution(client):
    connection, app = client
    app.state.runtime.model_factory = lambda _: ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": f"repeat-{index}", "name": "text_length", "args": {"text": "private repeated tool input"}}
                ],
                usage_metadata={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
            )
            for index in range(4)
        ]
    )
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"model_id": "test", "message": "private prompt"},
        headers=ORIGIN,
    ).json()["run_id"]
    assert "run.failed" in connection.get(f"/api/v1/runs/{run}/events").text
    assert connection.get(f"/api/v1/runs/{run}").json()["error_code"] == "ASK_NO_PROGRESS"
    events = connection.portal.call(
        app.state.db.fetch,
        "SELECT event,data FROM run_events WHERE run_id=%s "
        "AND event IN ('context.observed','tool.completed','tool.repetition_stopped')",
        (run,),
    )
    assert sum(event["event"] == "tool.completed" for event in events) == 2
    assert sum(event["event"] == "tool.repetition_stopped" for event in events) == 1
    assert "private" not in str(events)
