"""Work model usage budgets include all inherited model callbacks."""

from types import SimpleNamespace

import pytest
from test_api import client as client
from test_durable_sandboxes import cleanup, prepare

from app.work_budget import WorkBudget, WorkBudgetExceeded, WorkUsageUnavailable


def response(tokens):
    return SimpleNamespace(
        generations=[
            [
                SimpleNamespace(
                    message=SimpleNamespace(
                        usage_metadata=None
                        if tokens is None
                        else {"total_tokens": tokens, "input_tokens": tokens, "output_tokens": 0}
                    )
                )
            ]
        ]
    )


def test_budget_stops_new_calls_at_limit(client):
    connection, app, run = prepare(client)
    budget = WorkBudget(app.state.db, run)
    try:
        connection.portal.call(
            app.state.db.execute, "UPDATE work_queue_runs SET token_limit=1000 WHERE id=%s", (run["id"],)
        )
        connection.portal.call(budget.on_chat_model_start, {}, [])
        connection.portal.call(budget.on_llm_end, response(1000))
        with pytest.raises(WorkBudgetExceeded):
            connection.portal.call(budget.on_chat_model_start, {}, [])
        row = connection.portal.call(
            app.state.db.fetch, "SELECT used_tokens,model_calls FROM work_queue_runs WHERE id=%s", (run["id"],)
        )[0]
        assert row == {"used_tokens": 1000, "model_calls": 1}
    finally:
        cleanup(connection, app, run)


def test_missing_usage_fails_closed(client):
    connection, app, run = prepare(client)
    budget = WorkBudget(app.state.db, run)
    try:
        with pytest.raises(WorkUsageUnavailable):
            connection.portal.call(budget.on_llm_end, response(None))
        with pytest.raises(WorkBudgetExceeded):
            connection.portal.call(budget.on_chat_model_start, {}, [])
    finally:
        cleanup(connection, app, run)
