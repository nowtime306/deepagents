"""Actual PostgreSQL callback settlement, no provider, consumer or sandbox execution."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from app.errors import APIError
from app.storage import Database
from app.usage import UsageRecorder
from app.work_budget import WorkBudget
from app.work_shared_budget_guard import require_budget


@pytest_asyncio.fixture
async def state(monkeypatch, request):
    url = os.environ.get("TEST_DATABASE_URL", "")
    config = conninfo_to_dict(url)
    if not (
        config.get("dbname", "").startswith("owner_agent_usage_fix_")
        and config.get("dbname", "").endswith("_test")
        and config.get("host") in {"localhost", "127.0.0.1", "::1"}
    ):
        pytest.skip("Explicit owned local owner_agent_usage_fix_<uuid>_test database required")
    db = Database(url)
    await db.pool.open()
    await db.pool.wait()
    ids = SimpleNamespace(
        owner=uuid4(),
        foreign=uuid4(),
        task=uuid4(),
        run=uuid4(),
        lease=uuid4(),
        model="synthetic-" + uuid4().hex,
        role="usage-fix-" + uuid4().hex,
    )
    await db.execute(
        "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)",
        (ids.role, ids.role, Jsonb(["work.execute"])),
    )
    for owner in (ids.owner, ids.foreign):
        await db.execute(
            "INSERT INTO users(id,username,password_hash,role_id) VALUES(%s,%s,%s,%s)",
            (owner, "qa-" + owner.hex, "synthetic-no-login", ids.role),
        )
    await db.execute(
        "INSERT INTO model_configs(id,name,model,base_url) VALUES(%s,%s,'synthetic','http://127.0.0.1:29871/never')",
        (ids.model, ids.model),
    )
    await db.execute(
        "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,'Synthetic','No execution')", (ids.task, ids.owner)
    )
    await db.execute(
        "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,status,lease_token,"
        "lease_until,token_limit,step_limit) VALUES(%s,%s,%s,'Synthetic','No execution',1,'RUNNING',"
        "%s,now()+interval '10 minutes',20000,10)",
        (ids.run, ids.task, ids.owner, ids.lease),
    )
    monkeypatch.setattr("app.usage.message_tokens", lambda *a: 10)
    monkeypatch.setattr("app.usage.text_tokens", lambda *a: 0)
    monkeypatch.setattr("app.local_tokenizer.encoder", lambda *a: None)
    run = {"id": ids.run, "owner_id": ids.owner, "lease_token": ids.lease}
    recorder = UsageRecorder(
        db, ids.owner, ids.run, ids.model, "work", team_budget=True, lease_token=ids.lease, revision=1, reserve=20
    )
    before = request.session.testsfailed
    try:
        yield SimpleNamespace(db=db, ids=ids, run=run, recorder=recorder, budget=WorkBudget(db, run))
    finally:
        if request.session.testsfailed == before:
            await db.execute("DELETE FROM model_usage WHERE owner_id=%s", (ids.owner,))
            await db.execute("DELETE FROM work_queue_runs WHERE id=%s", (ids.run,))
            await db.execute("DELETE FROM work_tasks WHERE id=%s", (ids.task,))
            await db.execute("DELETE FROM model_configs WHERE id=%s", (ids.model,))
            await db.execute("DELETE FROM users WHERE id=ANY(%s)", ([ids.owner, ids.foreign],))
            await db.execute("DELETE FROM platform_roles WHERE id=%s", (ids.role,))
        await db.pool.close()


async def row(state):
    return (
        await state.db.fetch(
            "SELECT resource_budgeted,usage_unknown,used_tokens,model_calls FROM work_queue_runs WHERE id=%s",
            (state.ids.run,),
        )
    )[0]


async def start(state):
    call = uuid4()
    await state.budget.on_chat_model_start({}, [[HumanMessage(content="synthetic")]])
    await state.recorder.on_chat_model_start({}, [[HumanMessage(content="synthetic")]], run_id=call)
    return call


def result():
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="synthetic", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
                    )
                )
            ]
        ]
    )


@pytest.mark.parametrize("native_first", [True, False])
async def test_native_enrollment_persists_and_known_callbacks_do_not_double_count(state, native_first):
    assert (await row(state))["resource_budgeted"] is False
    call = await start(state)
    assert (await row(state))["resource_budgeted"] is True
    if native_first:
        await state.recorder.on_llm_end(result(), run_id=call)
        await state.budget.on_llm_end(result())
    else:
        await state.budget.on_llm_end(result())
        await state.recorder.on_llm_end(result(), run_id=call)
    assert await row(state) == {"resource_budgeted": True, "usage_unknown": False, "used_tokens": 15, "model_calls": 1}


async def test_known_known_unknown_preserves_native_partial_usage_and_blocks_next_effect(state):
    for _ in range(2):
        call = await start(state)
        await state.recorder.on_llm_end(result(), run_id=call)
        await state.budget.on_llm_end(result())
    call = await start(state)
    await state.recorder.on_llm_error(TimeoutError("synthetic, no request sent"), run_id=call)
    assert await row(state) == {"resource_budgeted": True, "usage_unknown": True, "used_tokens": 30, "model_calls": 3}
    native = await state.db.fetch(
        "SELECT status,input_tokens,output_tokens,estimated_input_tokens,output_token_reserve "
        "FROM model_usage WHERE call_id=%s",
        (call,),
    )
    assert native == [
        {
            "status": "UNKNOWN",
            "input_tokens": None,
            "output_tokens": None,
            "estimated_input_tokens": 10,
            "output_token_reserve": 20,
        }
    ]
    with pytest.raises(APIError) as caught:
        await require_budget(state.db, state.run)
    assert caught.value.code == "WORK_USAGE_UNAVAILABLE"
    assert (await state.db.fetch("SELECT count(*) AS n FROM model_usage WHERE run_id=%s", (state.ids.run,)))[0][
        "n"
    ] == 3


@pytest.mark.parametrize("fault", ["foreign_owner", "wrong_lease", "expired", "cancelled", "stopped"])
async def test_enrollment_rejects_non_owned_or_inactive_lease_without_native_call(state, fault):
    if fault == "foreign_owner":
        state.recorder.owner = state.ids.foreign
    elif fault == "wrong_lease":
        state.recorder.lease_token = uuid4()
    elif fault == "expired":
        await state.db.execute(
            "UPDATE work_queue_runs SET lease_until=now()-interval '1 second' WHERE id=%s", (state.ids.run,)
        )
    elif fault == "cancelled":
        await state.db.execute("UPDATE work_queue_runs SET cancel_requested=true WHERE id=%s", (state.ids.run,))
    else:
        await state.db.execute(
            "UPDATE work_queue_runs SET status='NEEDS_REVIEW',lease_token=NULL,lease_until=NULL WHERE id=%s",
            (state.ids.run,),
        )
    with pytest.raises(APIError):
        await state.recorder.on_chat_model_start({}, [[HumanMessage(content="synthetic")]], run_id=uuid4())
    assert (await row(state))["resource_budgeted"] is False
    assert (await state.db.fetch("SELECT count(*) AS n FROM model_usage WHERE run_id=%s", (state.ids.run,)))[0][
        "n"
    ] == 0


async def test_budget_rejection_rolls_back_enrollment_in_same_transaction(state):
    await state.db.execute("UPDATE work_queue_runs SET step_limit=1 WHERE id=%s", (state.ids.run,))
    await state.db.execute(
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status,input_tokens,output_tokens) "
        "VALUES(%s,%s,%s,'work',%s,'KNOWN',10,5)",
        (uuid4(), state.ids.run, state.ids.owner, state.ids.model),
    )
    with pytest.raises(APIError) as caught:
        await state.recorder.on_chat_model_start({}, [[HumanMessage(content="synthetic")]], run_id=uuid4())
    assert caught.value.code == "WORK_BUDGET_EXCEEDED"
    assert (await row(state))["resource_budgeted"] is False
    assert (await state.db.fetch("SELECT count(*) AS n FROM model_usage WHERE run_id=%s", (state.ids.run,)))[0][
        "n"
    ] == 1
