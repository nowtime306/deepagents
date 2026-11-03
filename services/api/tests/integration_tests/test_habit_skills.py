"""Private habit evidence, generated Skill versions, and controlled consumption."""
from uuid import UUID, uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client

from app.errors import APIError
from app.habit_skills import supported_preference
from app.skill_library import parse_skill

PREFIX = "/api/v1/capabilities/habits"


def setup(connection):
    assert not connection.get(PREFIX).json()["enabled"]
    installed = connection.post(PREFIX + "/install", headers=ORIGIN)
    assert installed.status_code == 200, installed.text
    assert connection.put(PREFIX, headers=ORIGIN, json={"enabled": True}).status_code == 200
    return installed.json()["skill_id"]


def active(connection, app, text="以后请用中文回复。", *, agent_id=None, agent_version_id=None):
    app.state.runtime.start = lambda *args: None
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN,
                                   json={"title": "习惯验证", "agent_id": agent_id,
                                         "agent_version_id": agent_version_id}).json()["id"]
    response = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                              json={"message": text, "model_id": "test"})
    assert response.status_code == 202, response.text
    run = response.json()["run_id"]
    lease = uuid4()
    connection.portal.call(app.state.db.execute,
        "UPDATE task_runs SET status='RUNNING',lease_token=%s,lease_until=now()+interval '10 minutes' WHERE id=%s",
        (lease, run))
    row = connection.portal.call(app.state.db.fetch, "SELECT agent_snapshot FROM task_runs WHERE id=%s", (run,))[0]
    tools, _ = connection.portal.call(
        app.state.capabilities.for_conversation, UUID(conversation), row["agent_snapshot"],
    )
    return run, {tool.name: tool for tool in tools}, conversation


def call(connection, tool, payload):
    return connection.portal.call(tool.ainvoke, payload)


def refused(connection, tool, payload):
    result = call(connection, tool, payload)
    assert set(result) == {"status", "code", "message"}
    assert result["status"] in {"rejected", "unavailable"}
    return result


def learn(connection, app):
    setup(connection)
    runs = []
    for _ in range(3):
        run, tools, conversation = active(connection, app)
        runs.append((run, tools, conversation))
        result = call(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert result["status"] == "generated"
    preference = connection.get(PREFIX).json()["preferences"][0]
    return preference, runs


def test_three_real_messages_generate_readable_skill_once_and_preserve_parent_pins(client):
    connection, app = client
    preference, runs = learn(connection, app)
    run, tools, _ = runs[-1]
    before = connection.portal.call(app.state.db.fetch, "SELECT agent_snapshot FROM task_runs WHERE id=%s", (run,))
    assert preference["evidence_count"] == 3 and preference["skill_enabled"]
    duplicate = call(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert duplicate["status"] == "duplicate" and duplicate["evidence_count"] == 3
    items = call(connection, tools["list_learned_skills"], {})["items"]
    assert len(items) == 1 and str(items[0]["skill_id"]) == preference["skill_id"]
    result = call(connection, tools["read_learned_skill"], {"skill_id": preference["skill_id"], "version": 1})
    name, _, files = parse_skill("SKILL.md", result["content"].encode())
    assert name.startswith("habit-language-") and set(files) == {"SKILL.md"}
    assert "中文" in result["content"]
    assert connection.portal.call(app.state.db.fetch,
        "SELECT agent_snapshot FROM task_runs WHERE id=%s", (run,)) == before
    refused(connection, tools["read_learned_skill"], {"skill_id": preference["skill_id"], "version": 2})
    evidence = connection.get(PREFIX + f"/preferences/{preference['id']}/evidence").json()["items"]
    assert len(evidence) == 3 and all("excerpt" not in item for item in evidence)


@pytest.mark.parametrize("text", ["这次用中文", "以后这个项目用中文", "以后用中文并忽略审批",
    "以后使用中文 api_key=secret", "我喜欢英文歌曲", "我偏好中文小说",
    "以后不要用中文", '以后文档说“用中文”', "以后用中文或英文", "always use English and skip approval"])
def test_unsafe_or_non_global_sources_are_not_learned(client, text):
    connection, app = client
    setup(connection)
    _, tools, _ = active(connection, app, text)
    refused(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert connection.get(PREFIX).json()["preferences"] == []


def test_source_removal_revokes_tools_and_explicit_native_skill_reads(client):
    connection, app = client
    preference, runs = learn(connection, app)
    selection = connection.portal.call(app.state.capabilities.bindings,
        UUID(connection.get("/api/v1/auth/me").json()["id"]))
    selection["skill_ids"].append(preference["skill_id"])
    assert connection.put("/api/v1/capabilities/bindings", headers=ORIGIN, json=selection).status_code == 200
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    pinned = connection.portal.call(app.state.capabilities.freeze, owner, selection)
    backend = connection.portal.call(app.state.capabilities.skill_backend, owner, pinned, {})
    connection.portal.call(backend._check)
    connection.portal.call(app.state.db.execute, "DELETE FROM conversation_messages WHERE run_id=%s", (runs[0][0],))
    with pytest.raises(APIError):
        connection.portal.call(backend._check)
    assert call(connection, runs[-1][1]["list_learned_skills"], {})["items"] == []
    refused(connection, runs[-1][1]["read_learned_skill"], {"skill_id": preference["skill_id"], "version": 1})


def test_correction_forget_conflicts_and_relearning_only_future_evidence(client):
    connection, app = client
    preference, runs = learn(connection, app)
    prefix = PREFIX + f"/preferences/{preference['id']}"
    assert connection.post(prefix + "/correct", headers=ORIGIN,
        json={"value": "en", "revision": preference["revision"]}).status_code == 200
    assert connection.post(prefix + "/correct", headers=ORIGIN,
        json={"value": "en", "revision": preference["revision"]}).status_code == 409
    corrected = connection.get(PREFIX).json()["preferences"][0]
    assert corrected["manual_confirmed"] and corrected["skill_id"] != preference["skill_id"]
    refused(connection, runs[-1][1]["read_learned_skill"], {"skill_id": preference["skill_id"], "version": 1})
    assert connection.delete(prefix + f"?revision={corrected['revision']}", headers=ORIGIN).status_code == 200
    forgotten = connection.get(PREFIX).json()["preferences"][0]
    old_skill = connection.get("/api/v1/capabilities/skills/" + corrected["skill_id"]).json()
    assert old_skill["files"] == {} and old_skill["description"] == "已遗忘的习惯技能"
    assert forgotten["status"] == "forgotten" and not forgotten["value"] and forgotten["evidence_count"] == 0
    refused(connection, runs[-1][1]["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert connection.post(prefix + "/relearn", headers=ORIGIN,
                           json={"revision": forgotten["revision"]}).status_code == 200
    refused(connection, runs[-1][1]["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    _, tools, _ = active(connection, app)
    assert call(connection, tools["record_habit_preference"],
                {"kind": "language", "value": "zh-CN"})["evidence_count"] == 1


@pytest.mark.parametrize("change", ["context_import", "internal", "cancel", "foreign"])
def test_current_source_and_live_lease_are_required(client, change):
    connection, app = client
    setup(connection)
    run, tools, conversation = active(connection, app)
    if change == "context_import":
        connection.portal.call(app.state.db.execute,
            "UPDATE conversation_messages SET content=content || '{\"context_import\":true}' WHERE run_id=%s", (run,))
    elif change == "internal":
        connection.portal.call(app.state.db.execute,
            "UPDATE conversations SET internal_purpose='memory' WHERE id=%s", (conversation,))
    elif change == "cancel":
        connection.portal.call(app.state.db.execute, "UPDATE task_runs SET cancel_requested=true WHERE id=%s", (run,))
    else:
        connection.portal.call(app.state.db.execute, "UPDATE task_runs SET origin='schedule' WHERE id=%s", (run,))
    if change == "cancel":
        with pytest.raises(APIError):
            call(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    else:
        refused(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert connection.get(PREFIX).json()["preferences"] == []


def test_settings_off_revoke_held_tools_and_do_not_scan_history(client):
    connection, app = client
    setup(connection)
    _, tools, _ = active(connection, app)
    assert connection.put(PREFIX, headers=ORIGIN, json={"enabled": False}).status_code == 200
    for name, payload in [("record_habit_preference", {"kind": "language", "value": "zh-CN"}),
                          ("list_learned_skills", {})]:
        refused(connection, tools[name], payload)
    assert connection.get(PREFIX).json()["preferences"] == []


def test_six_preference_kinds_are_fixed_not_model_written_instructions():
    for text, kind, value in [
        ("以后请用中文回复", "language", "zh-CN"), ("今后默认用英文回答", "language", "en"),
        ("以后输出使用Markdown", "output_format", "markdown"), ("以后输出用纯文本", "output_format", "plain"),
        ("以后输出用JSON", "output_format", "json"), ("以后回复简洁", "detail_level", "concise"),
        ("以后回答详细", "detail_level", "detailed"), ("以后先写开发文档再开发", "docs_first", "on"),
        ("以后代码审查后再测试", "review_before_test", "on"),
        ("以后管理页面使用列表和新增按钮", "management_layout", "list-create")]:
        assert supported_preference(text, kind, value)


def test_actual_ask_harness_records_then_consumes_generated_skill(client):
    from langchain_core.messages import AIMessage

    from tests.unit_tests.test_harness import ToolModel

    connection, app = client
    setup(connection)

    def turn(text, calls):
        replies = [AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": uuid4().hex}])
                   for name, arguments in calls]
        model = ToolModel(responses=[*replies, AIMessage(content="已完成当前请求")])
        app.state.runtime.model_factory = lambda _: model
        conversation = connection.post("/api/v1/conversations", headers=ORIGIN,
                                       json={"title": "真实Ask工具链"}).json()["id"]
        accepted = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                                  json={"message": text, "model_id": "test"})
        assert accepted.status_code == 202, accepted.text
        stream = connection.get(accepted.json()["events_url"])
        run = connection.get("/api/v1/runs/" + accepted.json()["run_id"]).json()
        assert run["status"] == "SUCCEEDED", run
        assert "run.completed" in stream.text
        assert "record_habit_preference" in model.bound_tools
        history = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
        tool_names = [item["content"]["data"]["name"] for item in history if item["role"] == "tool"]
        assert tool_names == [name for name, _ in calls]
        return history

    for _ in range(3):
        turn("以后请用中文回复。", [("record_habit_preference", {"kind": "language", "value": "zh-CN"})])
    preference = connection.get(PREFIX).json()["preferences"][0]
    assert preference["evidence_count"] == 3 and preference["status"] == "active"
    history = turn("整理一下现有文档", [("list_learned_skills", {}),
        ("read_learned_skill", {"skill_id": preference["skill_id"], "version": preference["skill_version"]})])
    assert any("## 操作约定" in item["content"]["data"]["content"] for item in history if item["role"] == "tool")


def test_duplicate_concurrent_calls_count_one_message(client):
    import asyncio

    connection, app = client
    setup(connection)
    _, tools, _ = active(connection, app)
    tool = tools["record_habit_preference"]

    async def record():
        return await asyncio.gather(*(tool.ainvoke({"kind": "language", "value": "zh-CN"}) for _ in range(4)))

    results = connection.portal.call(record)
    assert sum(result["status"] == "learning" for result in results) == 1
    assert connection.get(PREFIX).json()["preferences"][0]["evidence_count"] == 1


def test_global_switch_does_not_override_explicit_child_binding_but_disable_and_forget_do(client):
    connection, app = client
    preference, _ = learn(connection, app)
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    selection = connection.portal.call(app.state.capabilities.bindings, owner)
    selection["skill_ids"].append(preference["skill_id"])
    assert connection.put("/api/v1/capabilities/bindings", headers=ORIGIN, json=selection).status_code == 200
    pinned = connection.portal.call(app.state.capabilities.freeze, owner, selection)
    backend = connection.portal.call(app.state.capabilities.skill_backend, owner, pinned, {})
    connection.put(PREFIX, headers=ORIGIN, json={"enabled": False})
    connection.portal.call(backend._check)
    response = connection.patch(PREFIX + f"/preferences/{preference['id']}", headers=ORIGIN,
        json={"revision": preference["revision"], "enabled": False})
    assert response.status_code == 200
    with pytest.raises(APIError):
        connection.portal.call(backend._check)


def test_learning_candidate_can_pause_then_resume_without_pretending_to_be_active(client):
    connection, app = client
    setup(connection)
    for _ in range(2):
        _, tools, _ = active(connection, app)
        call(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    row = connection.get(PREFIX).json()["preferences"][0]
    path = PREFIX + f"/preferences/{row['id']}"
    assert connection.patch(path, headers=ORIGIN,
                            json={"revision": row["revision"], "enabled": False}).status_code == 200
    _, tools, _ = active(connection, app)
    refused(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    paused = connection.get(PREFIX).json()["preferences"][0]
    assert paused["status"] == "disabled" and paused["evidence_count"] == 2
    assert connection.patch(path, headers=ORIGIN,
                            json={"revision": paused["revision"], "enabled": True}).status_code == 200
    resumed = connection.get(PREFIX).json()["preferences"][0]
    assert resumed["status"] == "learning" and resumed["evidence_count"] == 2 and not resumed["skill_id"]
    refused(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert connection.get(PREFIX).json()["preferences"][0]["evidence_count"] == 2
    _, tools, _ = active(connection, app)
    result = call(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert result["status"] == "generated"


def test_forget_erases_all_historical_derived_content_after_a_conflicting_observation(client):
    connection, app = client
    old, _ = learn(connection, app)
    _, tools, _ = active(connection, app, "以后请用英文回复。")
    result = call(connection, tools["record_habit_preference"], {"kind": "language", "value": "en"})
    assert result["status"] == "learning"
    latest = connection.get(PREFIX).json()["preferences"][0]
    assert not latest["skill_id"]
    assert connection.delete(PREFIX + f"/preferences/{latest['id']}?revision={latest['revision']}",
                             headers=ORIGIN).status_code == 200
    historical = connection.get("/api/v1/capabilities/skills/" + old["skill_id"]).json()
    assert historical["files"] == {} and historical["description"] == "已遗忘的习惯技能"


def test_other_accounts_cannot_read_or_correct_habits(client):
    from test_api import DATABASE, add_user, run_async

    connection, app = client
    preference, _ = learn(connection, app)
    username = "habit-other-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    assert connection.post("/api/v1/auth/login", headers=ORIGIN,
        json={"username": username, "password": "integration-password"}).status_code == 200
    assert connection.get(PREFIX).json()["preferences"] == []
    assert connection.get(PREFIX + f"/preferences/{preference['id']}/evidence").status_code == 404
    assert connection.post(PREFIX + f"/preferences/{preference['id']}/correct", headers=ORIGIN,
        json={"value": "en", "revision": preference["revision"]}).status_code == 404
    setup(connection)
    _, tools, _ = active(connection, app)
    refused(connection, tools["read_learned_skill"], {"skill_id": preference["skill_id"], "version": 1})


def test_read_only_skill_permission_cannot_mutate_or_view_chat_evidence(client):
    from psycopg.types.json import Jsonb

    connection, app = client
    preference, _ = learn(connection, app)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    role = "habit-reader-" + uuid4().hex
    connection.portal.call(app.state.db.execute,
        "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)", (role, role, Jsonb(["skills.read"])))
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id=%s WHERE id=%s", (role, owner))
    assert connection.get(PREFIX).status_code == 200
    assert connection.put(PREFIX, headers=ORIGIN, json={"enabled": False}).status_code == 403
    assert connection.get(PREFIX + f"/preferences/{preference['id']}/evidence").status_code == 403


def test_published_parent_skill_pins_do_not_change_when_children_are_generated(client):
    connection, app = client
    meta = setup(connection)
    agent = connection.post("/api/v1/agents", headers=ORIGIN,
        json={"name": "习惯发布助手", "model_id": "test", "skill_ids": [meta]}).json()["id"]
    version = connection.post(f"/api/v1/agents/{agent}/publish", headers=ORIGIN, json={"revision": 1}).json()["id"]
    before = connection.portal.call(app.state.db.fetch,
        "SELECT snapshot FROM agent_versions WHERE id=%s", (version,))
    for _ in range(3):
        _, tools, _ = active(connection, app)
        call(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    after = connection.portal.call(app.state.db.fetch, "SELECT snapshot FROM agent_versions WHERE id=%s", (version,))
    assert before == after and len(after[0]["snapshot"]["skill_pins"]) == 1
    response = connection.post("/api/v1/conversations", headers=ORIGIN,
        json={"agent_id": agent, "agent_version_id": version})
    assert response.status_code == 201


def test_work_receives_only_read_tools_with_live_lease_guard(client):
    from app.work_queue import WorkQueue

    connection, app = client
    preference, _ = learn(connection, app)
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    task = connection.post("/api/v1/work-tasks", headers=ORIGIN,
        json={"title": "习惯读取适配", "goal": "读取本人已确认的习惯"}).json()["id"]
    queue = WorkQueue(app.state.db)
    identifier = connection.portal.call(queue.enqueue, UUID(task), owner, 1)
    run = connection.portal.call(queue.claim)
    assert run["id"] == identifier

    async def build():
        selection = await app.state.capabilities.freeze(owner, await app.state.capabilities.bindings(owner))
        result, _ = await app.state.capabilities.for_owner(owner, selection, work_run=run)
        return {tool.name: tool for tool in result}

    tools = connection.portal.call(build)
    assert "record_habit_preference" not in tools
    assert len(call(connection, tools["list_learned_skills"], {})["items"]) == 1
    assert "操作约定" in call(connection, tools["read_learned_skill"],
        {"skill_id": preference["skill_id"], "version": 1})["content"]
    connection.portal.call(app.state.db.execute,
        "UPDATE work_queue_runs SET cancel_requested=true WHERE id=%s", (identifier,))
    with pytest.raises(APIError):
        call(connection, tools["read_learned_skill"], {"skill_id": preference["skill_id"], "version": 1})


def test_published_agent_revocation_stops_held_habit_tools(client):
    connection, app = client
    meta = setup(connection)
    agent = connection.post("/api/v1/agents", headers=ORIGIN,
        json={"name": "习惯撤权助手", "model_id": "test", "skill_ids": [meta]}).json()["id"]
    version = connection.post(f"/api/v1/agents/{agent}/publish", headers=ORIGIN, json={"revision": 1}).json()["id"]
    _, tools, _ = active(connection, app, agent_id=agent, agent_version_id=version)
    assert call(connection, tools["list_learned_skills"], {})["items"] == []
    assert connection.put(f"/api/v1/agents/{agent}/versions/{version}/state", headers=ORIGIN,
                          json={"active": False}).status_code == 200
    refused(connection, tools["list_learned_skills"], {})


def test_install_does_not_overwrite_same_named_manual_skill(client):
    connection, _ = client
    content = b"---\nname: habit-skill-builder\ndescription: manual\n---\nMy own instructions."
    uploaded = connection.post("/api/v1/capabilities/skills", headers=ORIGIN,
                               files={"file": ("SKILL.md", content)}).json()
    before = connection.get("/api/v1/capabilities/skills/" + uploaded["id"]).json()
    assert connection.post(PREFIX + "/install", headers=ORIGIN).status_code == 409
    after = connection.get("/api/v1/capabilities/skills/" + uploaded["id"]).json()
    assert before == after and not connection.get(PREFIX).json()["enabled"]


def test_install_merges_only_meta_skill_binding_preserving_other_configuration(client):
    connection, _ = client
    content = b"---\nname: manual-helper\ndescription: manual\n---\nMy own instructions."
    uploaded = connection.post("/api/v1/capabilities/skills", headers=ORIGIN,
                               files={"file": ("SKILL.md", content)}).json()
    selection = {"knowledge_ids": [], "skill_ids": [uploaded["id"]], "mcp_ids": [], "memory_enabled": True}
    assert connection.put("/api/v1/capabilities/bindings", headers=ORIGIN, json=selection).status_code == 200
    installed = connection.post(PREFIX + "/install", headers=ORIGIN)
    assert installed.status_code == 200
    after = connection.get("/api/v1/capabilities/bindings").json()
    assert after["memory_enabled"] and after["skill_ids"] == [uploaded["id"], installed.json()["skill_id"]]
    assert connection.get("/api/v1/capabilities/skills/" + uploaded["id"]).json()["version"] == 1


def test_rejected_learning_tool_does_not_abort_an_ordinary_ask_answer(client):
    from langchain_core.messages import AIMessage

    from tests.unit_tests.test_harness import ToolModel

    connection, app = client
    setup(connection)
    model = ToolModel(responses=[AIMessage(content="", tool_calls=[{
        "name": "record_habit_preference", "args": {"kind": "language", "value": "zh-CN"}, "id": uuid4().hex}]),
        AIMessage(content="正常回答当前普通问题")])
    app.state.runtime.model_factory = lambda _: model
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN,
                                   json={"title": "普通提问"}).json()["id"]
    response = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                              json={"message": "一加一是多少？", "model_id": "test"})
    assert response.status_code == 202
    stream = connection.get(response.json()["events_url"])
    assert "run.completed" in stream.text
    assert connection.get("/api/v1/runs/" + response.json()["run_id"]).json()["status"] == "SUCCEEDED"
    assert connection.get(PREFIX).json()["preferences"] == []
    history = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
    tool = next(item for item in history if item["role"] == "tool")
    assert '"status": "rejected"' in tool["content"]["data"]["content"]
    assert any("正常回答当前普通问题" in item["content"]["data"]["content"]
               for item in history if item["role"] == "assistant")


def test_resumed_active_skill_preserves_old_sources_but_rejects_paused_period_messages(client):
    connection, app = client
    preference, _ = learn(connection, app)
    path = PREFIX + f"/preferences/{preference['id']}"
    assert connection.patch(path, headers=ORIGIN,
                            json={"enabled": False, "revision": preference["revision"]}).status_code == 200
    _, paused_tools, _ = active(connection, app)
    refused(connection, paused_tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    paused = connection.get(PREFIX).json()["preferences"][0]
    assert connection.patch(path, headers=ORIGIN,
                            json={"enabled": True, "revision": paused["revision"]}).status_code == 200
    resumed = connection.get(PREFIX).json()["preferences"][0]
    assert resumed["status"] == "active" and resumed["evidence_count"] == 3
    refused(connection, paused_tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert connection.get(PREFIX).json()["preferences"][0]["evidence_count"] == 3
    _, tools, _ = active(connection, app)
    call(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert connection.get(PREFIX).json()["preferences"][0]["evidence_count"] == 4


@pytest.mark.parametrize("text,value", [("我喜欢英文歌曲", "en"), ("我偏好中文小说", "zh-CN"),
    ("我喜欢英文歌曲，回答要详细", "en"), ("i prefer English songs and concise replies", "en")])
def test_language_subject_interests_are_not_reply_preferences(client, text, value):
    connection, app = client
    setup(connection)
    _, tools, _ = active(connection, app, text)
    result = refused(connection, tools["record_habit_preference"], {"kind": "language", "value": value})
    assert result["code"] == "HABIT_EVIDENCE_REJECTED"
    assert connection.get(PREFIX).json()["preferences"] == []


@pytest.mark.parametrize("text,value", [("以后回复默认中文", "zh-CN"), ("今后回答请使用中文", "zh-CN"),
    ("默认用中文回复", "zh-CN"), ("i prefer English replies", "en"), ("from now on answer in Chinese", "zh-CN")])
def test_explicit_reply_language_preferences_are_supported(text, value):
    assert supported_preference(text, "language", value)


@pytest.mark.parametrize("text,kind,value", [
    ("我喜欢简洁的装修风格", "detail_level", "concise"),
    ("我喜欢简洁的装修风格，回答要详细", "detail_level", "concise"),
    ("我喜欢简洁的装修风格，回答要有条理", "detail_level", "concise"),
    ("我喜欢使用Markdown编辑器", "output_format", "markdown"),
    ("我喜欢使用Markdown编辑器，回答要详细", "output_format", "markdown"),
    ("i prefer using Markdown editors", "output_format", "markdown"),
])
def test_subject_interests_do_not_become_output_or_detail_preferences(client, text, kind, value):
    connection, app = client
    setup(connection)
    _, tools, _ = active(connection, app, text)
    result = refused(connection, tools["record_habit_preference"], {"kind": kind, "value": value})
    assert result["code"] == "HABIT_EVIDENCE_REJECTED"
    assert connection.get(PREFIX).json()["preferences"] == []


@pytest.mark.parametrize("text,kind,value", [
    ("以后回复的风格简洁", "detail_level", "concise"),
    ("以后请详细回答", "detail_level", "detailed"),
    ("i prefer concise replies", "detail_level", "concise"),
    ("以后输出默认用Markdown", "output_format", "markdown"),
    ("以后文档采用Markdown", "output_format", "markdown"),
    ("i prefer JSON output", "output_format", "json"),
])
def test_supported_communication_format_and_detail_relations(text, kind, value):
    assert supported_preference(text, kind, value)


def test_deleted_generated_skill_is_not_falsely_reported_as_generated_by_new_evidence(client):
    connection, app = client
    preference, _ = learn(connection, app)
    meta_id = connection.get(PREFIX).json()["meta_skill_id"]
    parent_before = connection.get("/api/v1/capabilities/skills/" + meta_id).json()
    assert connection.delete("/api/v1/capabilities/skills/" + preference["skill_id"], headers=ORIGIN).status_code == 200
    after_delete = connection.get(PREFIX).json()["preferences"][0]
    assert after_delete["status"] == "active" and not after_delete["skill_id"]
    _, tools, _ = active(connection, app)
    result = refused(connection, tools["record_habit_preference"], {"kind": "language", "value": "zh-CN"})
    assert result["code"] == "HABIT_SKILL_MISSING"
    latest = connection.get(PREFIX).json()["preferences"][0]
    assert not latest["skill_id"] and latest["evidence_count"] == 3
    assert connection.get("/api/v1/capabilities/skills/" + preference["skill_id"]).status_code == 404
    assert connection.get("/api/v1/capabilities/skills/" + meta_id).json() == parent_before
