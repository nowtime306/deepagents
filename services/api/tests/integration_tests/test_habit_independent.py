"""Independent HSK acceptance; not executed before final review gate."""

import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from test_api import ORIGIN
from test_api import client as client
from test_habit_skills import PREFIX, active, call, setup

from tests.unit_tests.test_harness import ToolModel


def preferences(c):
    return c.get(PREFIX).json()["preferences"]


def test_third_distinct_message_concurrent_promotion_is_one_asset(client):
    c, app = client
    setup(c)
    for _ in range(2):
        _, tools, _ = active(c, app)
        call(c, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert preferences(c)[0]["evidence_count"] == 2 and preferences(c)[0]["skill_id"] is None
    _, tools, _ = active(c, app)

    async def promote():
        return await asyncio.gather(
            *(tools["record_habit_preference"].ainvoke({"kind": "language", "value": "zh-CN"}) for _ in range(5))
        )

    result = c.portal.call(promote)
    assert sum(r.get("status") == "generated" for r in result) == 1
    pref = preferences(c)[0]
    assert pref["evidence_count"] == 3 and pref["skill_id"]
    skills = c.get("/api/v1/capabilities/skills").json()["items"]
    assert len(skills) == 2 and sum(s["id"] == pref["skill_id"] for s in skills) == 1


def test_pause_candidate_blocks_new_evidence_resume_needs_new_third_message(client):
    c, app = client
    setup(c)
    for _ in range(2):
        _, tools, _ = active(c, app)
        call(c, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    pref = preferences(c)[0]
    path = PREFIX + "/preferences/" + pref["id"]
    assert c.patch(path, headers=ORIGIN, json={"revision": pref["revision"], "enabled": False}).status_code == 200
    _, blocked, _ = active(c, app)
    result = call(c, blocked["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert result.get("status") in {"rejected", "unavailable"}
    paused = preferences(c)[0]
    assert paused["evidence_count"] == 2 and paused["skill_id"] is None
    assert c.patch(path, headers=ORIGIN, json={"revision": paused["revision"], "enabled": True}).status_code == 200
    resumed = preferences(c)[0]
    assert resumed["status"] == "learning" and resumed["evidence_count"] == 2
    replay = call(c, blocked["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert replay.get("status") in {"rejected", "unavailable"}
    assert preferences(c)[0]["evidence_count"] == 2
    _, next_tools, _ = active(c, app)
    assert (
        call(c, next_tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})["status"] == "generated"
    )


def test_switch_off_then_on_rejects_old_current_message(client):
    c, app = client
    setup(c)
    _, old, _ = active(c, app)
    assert c.put(PREFIX, headers=ORIGIN, json={"enabled": False}).status_code == 200
    assert c.put(PREFIX, headers=ORIGIN, json={"enabled": True}).status_code == 200
    result = call(c, old["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert result.get("status") in {"rejected", "unavailable"} and preferences(c) == []
    _, new, _ = active(c, app)
    assert call(c, new["record_habit_preference"], {"kind": "language", "value": "zh-CN"})["evidence_count"] == 1


def test_two_distinct_supported_kinds_do_not_deduplicate_each_other(client):
    c, app = client
    setup(c)
    for _ in range(3):
        _, tools, _ = active(c, app, "以后先写开发文档再开发，代码审查后再测试。")
        for kind in ["docs_first", "review_before_test"]:
            call(c, tools["record_habit_preference"], {"kind": kind, "value": "on"})
    prefs = preferences(c)
    assert {p["kind"] for p in prefs} == {"docs_first", "review_before_test"}
    assert all(p["evidence_count"] == 3 and p["status"] == "active" for p in prefs)
    assert len({p["skill_id"] for p in prefs}) == 2


def test_assistant_role_cannot_be_reclassified_as_evidence(client):
    c, app = client
    setup(c)
    run, tools, _ = active(c, app)
    c.portal.call(app.state.db.execute, "UPDATE conversation_messages SET role='assistant' WHERE run_id=%s", (run,))
    result = call(c, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert result.get("status") in {"rejected", "unavailable"} and preferences(c) == []


def test_full_existing_bindings_install_is_atomic_and_preserves_manual_skills(client):
    c, _ = client
    ids = []
    for i in range(20):
        content = f"---\nname: independent-{i}\ndescription: private\n---\nmanual"
        r = c.post("/api/v1/capabilities/skills", headers=ORIGIN, files={"file": ("SKILL.md", content.encode())})
        assert r.status_code == 201
        ids.append(r.json()["id"])
    assert (
        c.put(
            "/api/v1/capabilities/bindings", headers=ORIGIN, json={"skill_ids": ids, "memory_enabled": True}
        ).status_code
        == 200
    )
    before = c.get("/api/v1/capabilities/bindings").json()
    response = c.post(PREFIX + "/install", headers=ORIGIN)
    assert response.status_code == 409 and response.json()["error"]["code"] == "SKILL_BINDING_LIMIT"
    assert c.get("/api/v1/capabilities/bindings").json() == before
    assert len(c.get("/api/v1/capabilities/skills").json()["items"]) == 20
    assert not c.get(PREFIX).json()["meta_skill_id"]


def test_actual_reader_uses_list_result_not_a_database_injected_skill_id(client):
    c, app = client
    setup(c)

    def turn(text, model):
        app.state.runtime.model_factory = lambda _: model
        conv = c.post("/api/v1/conversations", headers=ORIGIN, json={"title": "独立真实工具循环"}).json()["id"]
        accepted = c.post(
            f"/api/v1/conversations/{conv}/runs", headers=ORIGIN, json={"message": text, "model_id": "test"}
        )
        assert accepted.status_code == 202
        assert "run.completed" in c.get(accepted.json()["events_url"]).text
        assert c.get("/api/v1/runs/" + accepted.json()["run_id"]).json()["status"] == "SUCCEEDED"
        return c.get(f"/api/v1/conversations/{conv}/messages").json()["items"]

    for _ in range(3):
        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "record_habit_preference",
                            "args": {"kind": "language", "value": "zh-CN"},
                            "id": uuid4().hex,
                        }
                    ],
                ),
                AIMessage(content="候选处理结果已核对"),
            ]
        )
        turn("以后请用中文回复。", model)
    received = []
    selected = []

    class Reader(ToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            tools = [m for m in messages if m.type == "tool"]
            if not tools:
                response = AIMessage(
                    content="", tool_calls=[{"name": "list_learned_skills", "args": {}, "id": uuid4().hex}]
                )
            elif tools[-1].name == "list_learned_skills":
                listing = json.loads(tools[-1].content)
                item = listing["items"][0]
                selected.append(item)
                response = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_learned_skill",
                            "args": {"skill_id": item["skill_id"], "version": item["version"]},
                            "id": uuid4().hex,
                        }
                    ],
                )
            else:
                body = json.loads(tools[-1].content)
                received.append(body["content"])
                assert "## 操作约定" in body["content"] and "中文" in body["content"]
                response = AIMessage(content="按已读取的中文习惯整理。")
            return ChatResult(generations=[ChatGeneration(message=response)])

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return self._generate(messages, stop, run_manager, **kwargs)

    model = Reader(responses=[AIMessage(content="unused")])
    history = turn("整理一下说明", model)
    names = [m["content"]["data"]["name"] for m in history if m["role"] == "tool"]
    assert names == ["list_learned_skills", "read_learned_skill"] and len(selected) == len(received) == 1
    assert "record_habit_preference" in model.bound_tools


@pytest.mark.parametrize(
    "text,kind,value", [("我喜欢英文歌曲", "language", "en"), ("我喜欢简洁的装修风格", "detail_level", "concise")]
)
def test_non_communication_interests_are_not_output_preferences(client, text, kind, value):
    c, app = client
    setup(c)
    _, tools, _ = active(c, app, text)
    result = call(c, tools["record_habit_preference"], {"kind": kind, "value": value})
    assert result.get("status") == "rejected" and preferences(c) == []


def test_deleted_child_requires_explicit_relearn_not_false_generation(client):
    c, app = client
    setup(c)
    for _ in range(3):
        _, tools, _ = active(c, app)
        call(c, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    preference = preferences(c)[0]
    removed = c.delete("/api/v1/capabilities/skills/" + preference["skill_id"], headers=ORIGIN)
    assert removed.status_code == 200
    missing = preferences(c)[0]
    assert missing["status"] == "active" and missing["skill_id"] is None
    _, held, _ = active(c, app)
    result = call(c, held["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert result.get("status") == "rejected" and result.get("code") == "HABIT_SKILL_MISSING"
    current = preferences(c)[0]
    assert current["evidence_count"] == 3 and current["skill_id"] is None
    assert (
        c.post(
            PREFIX + "/preferences/" + current["id"] + "/relearn",
            headers=ORIGIN,
            json={"revision": current["revision"]},
        ).status_code
        == 200
    )
    assert call(c, held["record_habit_preference"], {"kind": "language", "value": "zh-CN"})["status"] == "rejected"
    for expected in ["learning", "learning", "generated"]:
        _, tools, _ = active(c, app)
        result = call(c, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
        assert result["status"] == expected
    restored = preferences(c)[0]
    assert restored["skill_id"] and restored["skill_id"] != preference["skill_id"]
    assert restored["evidence_count"] == 3
