"""The original chat automation path accepts an unchanged published agent after model maintenance."""

from datetime import UTC, datetime, timedelta

from test_api import ORIGIN
from test_api import client as client
from test_ask_automations import claim
from test_automations import version


def test_chat_draft_keeps_published_agent_after_model_revision_changes(client):
    connection, app = client
    published = version(connection)
    before = connection.portal.call(app.state.db.fetch,
                                    "SELECT snapshot FROM agent_versions WHERE id=%s", (published,))[0]["snapshot"]
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET revision=revision+1,temperature=0.73 WHERE id='test'")
    run, token = claim(connection, app)
    prefix = f"/api/v1/internal/ask-runs/{run}"
    options = connection.post(prefix + "/automation-targets", json={"token": token})
    assert options.status_code == 200, options.text
    assert published in {item["id"] for item in options.json()["items"]}
    response = connection.post(prefix + "/automation-drafts", json={"token": token, "call_id": "maintained-model",
        "schedule": {"name": "保留发布版的定时草稿", "agent_version_id": published, "goal": "test without provider",
                     "starts_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}})
    assert response.status_code == 200, response.text
    assert response.json()["type"] == "automation_draft" and not response.json()["enabled"]
    after = connection.portal.call(app.state.db.fetch,
                                   "SELECT snapshot FROM agent_versions WHERE id=%s", (published,))[0]["snapshot"]
    assert after == before
    assert connection.delete(f"/api/v1/automations/{response.json()['id']}", headers=ORIGIN).status_code == 200
    connection.portal.call(app.state.db.execute, "UPDATE task_runs SET status='CANCELLED' WHERE id=%s", (run,))
