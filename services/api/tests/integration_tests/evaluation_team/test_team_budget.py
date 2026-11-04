"""Private engineering contracts using real production closures and SDK substitutes only."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from typing import ClassVar
from uuid import uuid4

import evaluation_team_scripts as scripts
import pytest
from evaluation_runner import team_budget_protocol as protocol
from evaluation_target import ProfileState
from langchain_core.messages import HumanMessage, ToolMessage

from app import work_shared_budget_guard as guard
from app.errors import APIError
from app.resource_runtime import ResourceRun, active_resources
from app.team_context import active_member
from app.team_runtime import DelegationControl
from app.usage import UsageRecorder
from app.work_backend import WorkBackend


def test_w05_member_reads_before_timeout_and_lead_cannot_cover_it():
    message, cost = scripts.step("AET-W05", "lead", [HumanMessage(content="delegate")])
    assert message.tool_calls[0]["name"] == "task" and cost == 15
    read, cost = scripts.step("AET-W05", "reviewer", [HumanMessage(content="review")])
    assert read.tool_calls[0]["name"] == "read_file" and cost == 15
    with pytest.raises(ValueError, match="actual fixed input"):
        scripts.step(
            "AET-W05", "reviewer", [ToolMessage(content="fake", tool_call_id="other")]
        )
    with pytest.raises(TimeoutError, match="after real input read"):
        scripts.step(
            "AET-W05",
            "reviewer",
            [ToolMessage(content="team-evidence", tool_call_id="ae-member-read")],
        )
    with pytest.raises(RuntimeError, match="Unsettled"):
        scripts.step(
            "AET-W05",
            "lead",
            [ToolMessage(content="done", tool_call_id="ae-team-delegate")],
        )


def test_w06_real_query_precedes_the_exact_16000_exhaustion_command():
    first, parent = scripts.step("AET-W06", "lead", [])
    read, child_read = scripts.step("AET-W06", "reviewer", [])
    assert read.tool_calls[0]["name"] == "read_file"
    read_result = ToolMessage(content="bounded", tool_call_id="ae-member-read")
    query, child = scripts.step("AET-W06", "reviewer", [read_result])
    assert first.tool_calls[0]["name"] == "task"
    assert query.tool_calls[0]["name"] == "search_knowledge"
    assert query.tool_calls[0]["args"] == {"query": "星桥 保修"}
    _, child_final = scripts.step(
        "AET-W06",
        "reviewer",
        [
            read_result,
            ToolMessage(
                content="real retained retrieval", tool_call_id="ae-member-query"
            ),
        ],
    )
    denied, final = scripts.step(
        "AET-W06",
        "lead",
        [ToolMessage(content="member complete", tool_call_id="ae-team-delegate")],
    )
    assert parent + child_read + child + 7 + child_final + final == 16000
    assert denied.tool_calls[0]["name"] == "write_file"
    assert scripts.native_usage(final)["total_tokens"] == 15933


def test_real_profile_state_shares_parent_member_query_cap_and_initialization_is_separate(
    tmp_path,
):
    path = tmp_path / "budget.json"
    state = ProfileState(path)
    owner, root = uuid4(), uuid4()
    index = active_resources.set(ResourceRun(None, owner, "resource", uuid4()))
    try:
        key = state.reserve(
            "AET-W06", "embedding", [HumanMessage(content="index")], cost=7
        )
        state.complete(key)
    finally:
        active_resources.reset(index)
    token = active_resources.set(ResourceRun(None, owner, "work", root))
    try:
        for role, amount in (
            ("lead", 15),
            ("reviewer", 15),
            ("reviewer", 15),
            ("embedding", 7),
            ("reviewer", 15),
            ("lead", 15933),
        ):
            state = ProfileState(
                path
            )  # Reconstructing state never creates a fresh root allowance.
            key = state.reserve(
                "AET-W06", role, [HumanMessage(content="fixed")], cost=amount
            )
            state.complete(key)
        before = path.read_bytes()
        with pytest.raises(RuntimeError, match="budget"):
            ProfileState(path).reserve("AET-W06", "lead", [], cost=15)
        assert path.read_bytes() == before
        saved = json.loads(before)
        assert sorted(row["value"]["tokens"] for row in saved["roots"]) == [7, 16000]
    finally:
        active_resources.reset(token)


def test_unknown_child_reservation_survives_rebuild_and_denies_parent(tmp_path):
    path = tmp_path / "unknown.json"
    token = active_resources.set(ResourceRun(None, uuid4(), "work", uuid4()))
    try:
        state = ProfileState(path)
        key = state.reserve("AET-W05", "reviewer", [], cost=15)
        state.mark_unknown(key)
        before = path.read_bytes()
        with pytest.raises(RuntimeError, match="budget"):
            ProfileState(path).reserve("AET-W05", "lead", [], cost=15)
        assert path.read_bytes() == before
        assert next(iter(ProfileState(path).roots.values()))["tokens"] == 15
    finally:
        active_resources.reset(token)


async def test_real_delegation_control_propagates_timeout_as_unknown_not_success():
    finished = []
    member, call_id, model = {"name": "reviewer"}, uuid4(), "sealed-reviewer"

    class Team:
        slots = asyncio.Semaphore(1)
        members: ClassVar = {"reviewer": member}
        configs: ClassVar = {
            "reviewer": SimpleNamespace(id=model, revision=3, route_selection_id=None)
        }
        reserves: ClassVar = {"reviewer": 4096}

        async def claim(self, selected, tool_id, arguments):
            return {"id": call_id}

        async def finish(self, identifier, status, result, code):
            finished.append((identifier, status, code))

    async def handler(request):
        assert active_member.get()["model_id"] == model
        assert active_member.get()["revision"] == 3
        raise TimeoutError("controlled no retry")

    control = DelegationControl(Team())
    with pytest.raises(APIError) as caught:
        await control.awrap_tool_call(
            SimpleNamespace(
                tool_call={
                    "name": "task",
                    "id": "delegate",
                    "args": {"subagent_type": "reviewer"},
                }
            ),
            handler,
        )
    assert caught.value.code == "TEAM_RESULT_UNKNOWN"
    assert finished == [(call_id, "UNKNOWN", "TEAM_RESULT_UNKNOWN")]
    assert active_member.get() is None


def database(row):
    class Database:
        async def fetch(self, query, parameters):
            self.query, self.parameters = query, parameters
            return [] if row is None else [row]

    return Database()


def run():
    return {"id": uuid4(), "owner_id": uuid4(), "lease_token": uuid4()}


def budget(**changes):
    return {
        "token_limit": 16000,
        "used_tokens": 16000,
        "known_tokens": 16000,
        "usage_unknown": False,
        "unsettled": 0,
        **changes,
    }


async def test_exact_budget_old_native_sync_allows_equal_but_new_real_tool_boundary_rejects_before_sdk():
    db, row = database(budget()), run()
    # Establish the precise existing equality boundary using the real production callback.
    await UsageRecorder(
        db, row["owner_id"], row["id"], "lead", "work", lease_token=row["lease_token"]
    ).sync_work()
    calls = []

    async def command(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(exit_code=0, error=None, text='{"error":null}')

    async def check():
        await guard.require_budget(db, row)

    backend = WorkBackend(
        SimpleNamespace(id="sdk-substitute", commands=SimpleNamespace(run=command)),
        check,
    )
    with pytest.raises(APIError) as caught:
        await backend.awrite("/budget-after.txt", "MUST_NOT_EXIST")
    assert caught.value.code == "WORK_BUDGET_EXCEEDED"
    assert calls == []
    assert db.parameters == (row["id"], row["owner_id"], row["lease_token"])


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"usage_unknown": True}, "WORK_USAGE_UNAVAILABLE"),
        ({"unsettled": 1}, "WORK_USAGE_UNAVAILABLE"),
        ({"used_tokens": 1, "known_tokens": 16000}, "WORK_BUDGET_EXCEEDED"),
    ],
)
async def test_live_guard_unknown_pending_and_stale_cache_never_refund(
    changes, expected
):
    with pytest.raises(APIError) as caught:
        await guard.require_budget(database(budget(**changes)), run())
    assert caught.value.code == expected


async def test_live_guard_missing_lease_and_then_revocation_prevent_later_call():
    db, row = database(None), run()
    with pytest.raises(APIError) as caught:
        await guard.require_budget(db, row)
    assert caught.value.code == "WORK_LEASE_LOST"
    assert "lease_until>now()" in db.query and "NOT r.cancel_requested" in db.query


async def test_pre_limit_real_tool_still_reaches_only_sdk_substitute():
    db, row, calls = database(budget(used_tokens=15999, known_tokens=15999)), run(), []

    async def command(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(exit_code=0, error=None, text='{"error":null}')

    async def check():
        await guard.require_budget(db, row)

    backend = WorkBackend(
        SimpleNamespace(id="substitute", commands=SimpleNamespace(run=command)), check
    )
    result = await backend.awrite("/permitted.txt", "known")
    assert result.error is None and len(calls) == 1


def fixture():
    owner, root, index, member = [str(uuid4()) for _ in range(4)]
    models = {
        role: {"id": model, "revision": 3}
        for role, model in (
            ("lead", "lead"),
            ("reviewer", "child"),
            ("embedding", "embed"),
        )
    }
    rows = []
    for role, kind, incoming, outgoing in (
        ("lead", "chat", 10, 5),
        ("reviewer", "chat", 10, 5),
        ("reviewer", "chat", 10, 5),
        ("reviewer", "embedding", 7, 0),
        ("reviewer", "chat", 10, 5),
        ("lead", "chat", 15000, 933),
    ):
        rows.append(
            {
                "call_id": str(uuid4()),
                "owner_id": owner,
                "run_id": root,
                "model_id": models["embedding" if kind == "embedding" else role]["id"],
                "model_revision": 3,
                "role_name": role,
                "team_call_id": None if role == "lead" else member,
                "call_kind": kind,
                "status": "KNOWN",
                "input_tokens": incoming,
                "output_tokens": outgoing,
            }
        )
    return {
        "work_rows": rows,
        "index_rows": [
            {
                "call_id": str(uuid4()),
                "run_id": index,
                "owner_id": owner,
                "model_id": "embed",
                "model_revision": 3,
                "mode": "resource",
                "call_kind": "embedding",
                "status": "KNOWN",
                "input_tokens": 7,
                "output_tokens": 0,
            }
        ],
        "owner": owner,
        "root": root,
        "resource_root": index,
        "models": models,
        "member": member,
        "native_work": {
            "calls": 6,
            "reported_calls": 6,
            "unknown_calls": 0,
            "pending_calls": 0,
            "input_tokens": 15047,
            "output_tokens": 953,
        },
        "native_index": {
            "calls": 1,
            "reported_calls": 1,
            "unknown_calls": 0,
            "pending_calls": 0,
            "input_tokens": 7,
            "output_tokens": 0,
        },
        "rejected_code": "WORK_BUDGET_EXCEEDED",
        "before_effects": {
            "provider_calls": 7,
            "tool_effects": 2,
            "operations": 0,
            "canary_files": 0,
        },
        "after_effects": {
            "provider_calls": 7,
            "tool_effects": 2,
            "operations": 0,
            "canary_files": 0,
        },
    }


def test_native_ledger_separates_runtime_query_and_initialization_exactly():
    assert protocol.exhaustion(**fixture())["case_tokens"] == 16007


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_root",
        "duplicate",
        "same_index",
        "wrong_model",
        "wrong_revision",
        "lost_member",
        "missing_query",
        "token_bool",
        "index_token_bool",
        "new_effect",
        "unknown",
        "missing_denial",
        "shift_query_cost",
        "reused_index_call",
    ],
)
def test_native_ledger_rejects_missing_forged_or_unsettled_evidence(fault):
    value = deepcopy(fixture())
    if fault == "foreign_root":
        value["work_rows"][1]["run_id"] = str(uuid4())
    if fault == "duplicate":
        value["work_rows"][1]["call_id"] = value["work_rows"][0]["call_id"]
    if fault == "same_index":
        value["resource_root"] = value["root"]
    if fault == "wrong_model":
        value["work_rows"][1]["model_id"] = "unsealed-same-name"
    if fault == "wrong_revision":
        value["work_rows"][1]["model_revision"] = 4
    if fault == "lost_member":
        value["work_rows"][3]["team_call_id"] = None
    if fault == "missing_query":
        value["work_rows"].pop(3)
    if fault == "token_bool":
        value["native_work"]["pending_calls"] = False
    if fault == "index_token_bool":
        value["index_rows"][0]["output_tokens"] = False
    if fault == "new_effect":
        value["after_effects"]["tool_effects"] += 1
    if fault == "unknown":
        value["work_rows"][3].update(
            status="UNKNOWN", input_tokens=None, output_tokens=None
        )
    if fault == "shift_query_cost":
        value["work_rows"][3]["input_tokens"] = 8
        value["work_rows"][5]["input_tokens"] = 14999
    if fault == "reused_index_call":
        value["index_rows"][0]["call_id"] = value["work_rows"][3]["call_id"]
    if fault == "missing_denial":
        value["rejected_code"] = "MODEL_BUDGET"
    with pytest.raises(ValueError):
        protocol.exhaustion(**value)
