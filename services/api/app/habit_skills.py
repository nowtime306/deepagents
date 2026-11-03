"""Owner-controlled evidence and instruction-only Skills; no model-written instructions."""

import hashlib
import json
import re
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.errors import APIError, fail
from app.permissions import require_account
from app.skill_library import parse_skill

OPTIONS = {
    "language": {"zh-CN": "中文", "en": "英文"},
    "output_format": {"markdown": "Markdown", "plain": "纯文本", "json": "JSON"},
    "detail_level": {"concise": "简洁", "detailed": "详细"},
    "docs_first": {"on": "先文档后开发"},
    "review_before_test": {"on": "代码审查后测试"},
    "management_layout": {"list-create": "列表与新增入口"},
}
PATTERNS = {
    "language": {"zh-CN": r"中文|汉语|chinese", "en": r"英文|英语|english"},
    "output_format": {"markdown": r"markdown", "plain": r"纯文本|纯文字|plain text",
                      "json": r"(?<![A-Za-z])json(?![A-Za-z])"},
    "detail_level": {"concise": r"简洁|简短|简明|精简|concise|brief", "detailed": r"详细|详尽|细致|detailed"},
    "docs_first": {"on": r"文档先行|先.{0,25}文档.{0,20}(?:再|然后).{0,12}(?:开发|编码|实现)"},
    "review_before_test": {"on": r"代码审查.{0,16}(?:再|后|才).{0,16}(?:测试|验收)"},
    "management_layout": {"list-create": r"列表.{0,20}(?:新增|创建)|(?:新增|创建).{0,20}列表"},
}
LONG_TERM = re.compile(
    r"^(?:以后|今后|往后|一直|默认|每次|长期|我(?:习惯|偏好|喜欢|希望)|always|from now on|by default|i prefer)", re.I
)
EXCLUDED = re.compile(
    r"这次|本次|这轮|本轮|当前|项目|仅|只对|文档说|引用|示例|例子|有人|不要|不能|不想|取消|停止|关闭|忘记|"
    r"授权|权限|审批|绕过|忽略|password|secret|token|api.?key|密码|密钥|令牌|bearer|sk-[\w-]+|https?://|[\"“”‘’`<>]|"
    r"authoriz|permission|approval|bypass|ignore|skip|this time|project|this task|"
    r"for now|today|only|do not|never|don.t",
    re.I
)


def validate_value(kind: str, value: str) -> None:
    if kind not in OPTIONS or value not in OPTIONS[kind]:
        fail(422, "HABIT_VALUE_INVALID", "请选择受支持的习惯类型和值")


def supported_preference(text: str, kind: str, value: str) -> bool:
    """Only brief, explicit global preferences qualify; ambiguity is never guessed."""
    text = text.strip()
    if len(text) > 300 or "\n" in text or not LONG_TERM.search(text) or EXCLUDED.search(text):
        return False
    matches = [key for key, pattern in PATTERNS[kind].items() if re.search(pattern, text, re.I)]
    if matches != [value]:
        return False
    if kind in {"language", "output_format", "detail_level"}:
        return communication_preference(text, kind, value)
    return True


def communication_preference(text: str, kind: str, value: str) -> bool:
    """Require a local relation to communication, not an unrelated subject or product interest."""
    word = "(?:" + PATTERNS[kind][value] + ")"
    chinese = {
        "language": r"(?:回复|回答|输出|沟通|交流|写作|语言)",
        "output_format": r"(?:输出|回复|回答|答复|文档|格式|结果)",
        "detail_level": r"(?:回复|回答|答复|说明|输出|内容|表达)",
    }[kind]
    english = {
        "language": r"\b(?:reply|replies|response|responses|respond|answer|answers|writing|language)\b",
        "output_format": r"\b(?:output|reply|replies|response|responses|answer|answers|documents?|format|results?)\b",
        "detail_level": r"\b(?:reply|replies|response|responses|answer|answers|explanation|output|content)\b",
    }[kind]
    connector = r"(?:请|都|默认|优先|尽量|需要|内容|风格|要|使用|用|采用|以|为|是|的|合法|有效|\s){0,8}"
    join = r"(?:\s+(?:in|using|use|as|the|default|preferred|should|be|please|of|with|a|an))*\s+"
    patterns = (chinese + connector + word, word + r"(?:来|进行|作为|的|\s){0,4}" + chinese,
                english + join + word + r"\b", word + r"\s+" + english)
    return any(re.search(pattern, text, re.I) for pattern in patterns)


async def lock(connection, owner: UUID) -> None:
    await connection.execute("SELECT pg_advisory_xact_lock(710079,hashtext(%s))", (str(owner),))


async def evidence_count(connection, preference: dict) -> int:
    row = await (await connection.execute(
        "SELECT count(*) AS count FROM habit_evidence e JOIN conversation_messages m ON m.id=e.message_id "
        "JOIN conversations c ON c.id=m.conversation_id JOIN task_runs r ON r.id=m.run_id "
        "WHERE e.preference_id=%s AND e.owner_id=%s AND c.owner_id=%s AND c.internal_purpose IS NULL "
        "AND m.role='user' AND NOT COALESCE((m.content->>'context_import')::boolean,false) "
        "AND r.origin='interactive' AND NOT(r.agent_snapshot ? 'internal_purpose')",
        (preference["id"], preference["owner_id"], preference["owner_id"]),
    )).fetchone()
    return row["count"]


async def permitted(connection, preference: dict) -> bool:
    return preference["status"] == "active" and (
        preference["manual_confirmed"] or await evidence_count(connection, preference) >= 3
    )


async def revoke(connection, preference: dict, *, erase: bool = False) -> None:
    if erase:
        await connection.execute(
            "UPDATE skills SET enabled=false,version=version+1,files='{}'::jsonb,description='已遗忘的习惯技能' "
            "WHERE owner_id=%s AND habit_preference_id=%s",
            (preference["owner_id"], preference["id"]),
        )
    elif preference["skill_id"]:
        await connection.execute(
            "UPDATE skills SET enabled=false,version=version+1 WHERE id=%s AND owner_id=%s AND habit_preference_id=%s",
            (preference["skill_id"], preference["owner_id"], preference["id"]),
        )


async def generate(connection, preference: dict) -> None:
    """Use a collision-resistant reserved name and never upsert another person's/manual Skill."""
    identifier = uuid4()
    name = "habit-" + preference["kind"].replace("_", "-") + "-" + identifier.hex[:16]
    label = OPTIONS[preference["kind"]][preference["value"]]
    triggers = {
        "language": "当前任务未指定回复语言时",
        "output_format": "当前任务允许选择答复格式时",
        "detail_level": "当前任务未指定答复详略时",
        "docs_first": "需要进行需求设计或代码开发时",
        "review_before_test": "代码改动即将交付独立测试时",
        "management_layout": "设计或调整管理数据页面时",
    }
    steps = {
        "language": "默认使用" + label + "回复；保留代码、标识符和引文需要的原文。",
        "output_format": {"markdown": "用Markdown组织必要段落、列表和代码；不额外添加整篇代码围栏。",
                          "plain": "使用纯文本回复，不添加Markdown装饰。",
                          "json": "输出语法有效的JSON，不添加代码围栏或JSON之外的说明。"}.get(preference["value"], ""),
        "detail_level": "默认采用" + label + "表达；仍回答当前问题所需事实和必要限制。",
        "docs_first": "编码前先整理必要的需求和开发说明；已有合适文档则复用，不重复创建无关文档。",
        "review_before_test": "先完成代码审查，再交付独立测试；保留开发者必要的自验，不声称未完成的检查已通过。",
        "management_layout": "用列表展示管理数据，提供明确的新增入口；按需打开编辑详情，不把所有表单平铺在列表前。",
    }
    description = triggers[preference["kind"]] + "采用本人已确认的" + label + "偏好"
    text = (f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n"
            f"# 本人任务习惯\n\n## 适用场景\n{triggers[preference['kind']]}。\n\n"
            f"## 操作约定\n{steps[preference['kind']]}\n\n"
            "## 边界\n当前请求优先；不新增任务或权限，不跳过审批，不触发未授权外部操作。\n")
    parsed_name, description, files = parse_skill("SKILL.md", text.encode())
    await connection.execute(
        "INSERT INTO skills(id,owner_id,name,description,files,habit_preference_id) VALUES(%s,%s,%s,%s,%s,%s)",
        (identifier, preference["owner_id"], parsed_name, description, Jsonb(files), preference["id"]),
    )
    await connection.execute(
        "UPDATE habit_preferences SET skill_id=%s,skill_version=1,status='active',revision=revision+1,updated_at=now() "
        "WHERE id=%s", (identifier, preference["id"]),
    )


async def guard_selected(database, owner: UUID, identifiers: list) -> None:
    """Ordinary Skills are unaffected; derived files retain source/forget revocation even if manually bound."""
    if not identifiers:
        return
    async with database.pool.connection() as connection:
        rows = await (await connection.execute(
            "SELECT p.*,s.id AS selected_id,s.version AS selected_version FROM skills s "
            "JOIN habit_preferences p ON p.id=s.habit_preference_id WHERE s.id=ANY(%s::uuid[]) AND s.owner_id=%s",
            (identifiers, owner),
        )).fetchall()
        for row in rows:
            if (row["skill_id"] != row["selected_id"] or row["skill_version"] != row["selected_version"]
                    or not await permitted(connection, row)):
                fail(403, "HABIT_SKILL_REVOKED", "习惯技能或来源已失效，请检查习惯设置")


class HabitLibrary:
    def __init__(self, database, owner: UUID, selection: dict, context: dict, parent_guard) -> None:
        self.database, self.owner, self.selection = database, owner, selection
        self.context, self.parent_guard = context, parent_guard

    async def authorize(self, *, writing=False) -> dict:
        await require_account(self.database, self.owner, "skills.read")
        await require_account(self.database, self.owner,
                              "chat.run" if self.context.get("mode") == "ask" else "work.execute")
        if writing:
            await require_account(self.database, self.owner, "skills.manage")
            await require_account(self.database, self.owner, "chat.read")
            await require_account(self.database, self.owner, "chat.run")
        await self.parent_guard()
        if self.selection.get("published_version_id"):
            active = await self.database.fetch(
                "SELECT 1 FROM agent_versions WHERE id=%s AND agent_id=%s AND owner_id=%s AND active",
                (self.selection["published_version_id"], self.selection.get("id"), self.owner),
            )
            if not active:
                fail(403, "HABIT_AGENT_VERSION_REVOKED", "智能体发布版本已下线")
        table = "task_runs" if self.context.get("mode") == "ask" else "work_queue_runs"
        live = await self.database.fetch(
            f"SELECT 1 FROM {table} WHERE id=%s AND lease_token IS NOT DISTINCT FROM %s "
            "AND (%s::uuid IS NULL OR lease_until>now()) "
            "AND status='RUNNING' AND NOT cancel_requested",
            (self.context.get("id"), self.context.get("lease_token"), self.context.get("lease_token")),
        )
        if not live:
            fail(409, "HABIT_RUN_EXPIRED", "当前运行已结束或授权已失效")
        if self.context.get("guard"):
            await self.context["guard"]()
        rows = await self.database.fetch("SELECT * FROM habit_settings WHERE owner_id=%s", (self.owner,))
        settings = rows[0] if rows else None
        if (not settings or not settings["enabled"] or str(settings["meta_skill_id"]) not in
                [str(value) for value in self.selection["skill_ids"]]):
            fail(403, "HABIT_LEARNING_DISABLED", "习惯学习已关闭或元Skill未获授权")
        return settings

    async def record(self, kind: str, value: str) -> dict:
        validate_value(kind, value)
        await self.authorize(writing=True)
        if self.context.get("mode") != "ask":
            fail(403, "HABIT_SOURCE_INVALID", "只能从当前真实智能对话用户消息学习")
        async with self.database.pool.connection() as connection:
            await lock(connection, self.owner)
            settings = await (await connection.execute(
                "SELECT * FROM habit_settings WHERE owner_id=%s AND enabled FOR UPDATE", (self.owner,),
            )).fetchone()
            if (not settings or str(settings["meta_skill_id"]) not in
                    [str(item) for item in self.selection["skill_ids"]]):
                fail(403, "HABIT_LEARNING_DISABLED", "习惯学习已关闭")
            source = await (await connection.execute(
                "SELECT m.id,m.content->>'text' AS text,m.created_at FROM task_runs r "
                "JOIN conversations c ON c.id=r.conversation_id JOIN conversation_messages m ON m.run_id=r.id "
                "AND m.conversation_id=c.id WHERE r.id=%s AND r.lease_token IS NOT DISTINCT FROM %s "
                "AND (%s::uuid IS NULL OR r.lease_until>now()) "
                "AND r.status='RUNNING' AND NOT r.cancel_requested AND r.origin='interactive' "
                "AND NOT(r.agent_snapshot ? 'internal_purpose') AND c.owner_id=%s AND c.internal_purpose IS NULL "
                "AND m.role='user' AND NOT COALESCE((m.content->>'context_import')::boolean,false) "
                "AND m.created_at>%s ORDER BY m.created_at,m.id LIMIT 2",
                (self.context["id"], self.context["lease_token"], self.context["lease_token"],
                 self.owner, settings["learning_after"]),
            )).fetchall()
            if len(source) != 1 or not supported_preference(source[0]["text"] or "", kind, value):
                fail(422, "HABIT_EVIDENCE_REJECTED", "当前消息没有可确认的全局长期偏好；未记录")
            source = source[0]
            await connection.execute(
                "INSERT INTO habit_preferences(id,owner_id,kind,value) VALUES(%s,%s,%s,%s) "
                "ON CONFLICT(owner_id,kind) DO NOTHING", (uuid4(), self.owner, kind, value),
            )
            preference = await (await connection.execute(
                "SELECT * FROM habit_preferences WHERE owner_id=%s AND kind=%s FOR UPDATE", (self.owner, kind),
            )).fetchone()
            if (preference["status"] in {"forgotten", "disabled"}
                    or source["created_at"] <= preference["learning_after"]):
                fail(409, "HABIT_SUPPRESSED", "此习惯已停用或遗忘，请先明确重新学习")
            if preference["status"] == "active" and not preference["skill_id"]:
                fail(409, "HABIT_SKILL_MISSING", "派生技能已删除，请明确修正或重新学习")
            if preference["manual_confirmed"]:
                return {"status": "manual", "preference_id": str(preference["id"])}
            if preference["value"] != value:
                await revoke(connection, preference)
                await connection.execute("DELETE FROM habit_evidence WHERE preference_id=%s", (preference["id"],))
                await connection.execute(
                    "UPDATE habit_preferences SET value=%s,status='learning',skill_id=NULL,skill_version=NULL,"
                    "revision=revision+1,updated_at=now() WHERE id=%s", (value, preference["id"]),
                )
            inserted = await (await connection.execute(
                "INSERT INTO habit_evidence(owner_id,preference_id,message_id,source_digest) VALUES(%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING message_id",
                (self.owner, preference["id"], source["id"], hashlib.sha256(source["text"].encode()).hexdigest()),
            )).fetchone()
            count = await evidence_count(connection, preference)
            latest = await (await connection.execute(
                "SELECT * FROM habit_preferences WHERE id=%s", (preference["id"],),
            )).fetchone()
            if count >= 3 and latest["status"] != "active":
                await generate(connection, latest)
            elif inserted:
                await connection.execute(
                    "UPDATE habit_preferences SET revision=revision+1,updated_at=now() WHERE id=%s",
                    (preference["id"],),
                )
            return {"status": "duplicate" if not inserted else "generated" if count >= 3 else "learning",
                    "preference_id": str(preference["id"]), "evidence_count": count}

    async def listing(self) -> dict:
        await self.authorize()
        async with self.database.pool.connection() as connection:
            rows = await (await connection.execute(
                "SELECT p.*,s.name,s.description,s.version FROM habit_preferences p JOIN skills s ON s.id=p.skill_id "
                "WHERE p.owner_id=%s AND s.enabled AND s.version=p.skill_version ORDER BY p.kind", (self.owner,),
            )).fetchall()
            return {"items": [{"skill_id": str(row["skill_id"]), "name": row["name"], "description": row["description"],
                               "version": row["version"]} for row in rows if await permitted(connection, row)]}

    async def read(self, skill_id: UUID, version: int) -> dict:
        await self.authorize()
        async with self.database.pool.connection() as connection:
            row = await (await connection.execute(
                "SELECT p.*,s.version,s.files,s.enabled FROM habit_preferences p JOIN skills s ON s.id=p.skill_id "
                "WHERE p.owner_id=%s AND s.id=%s", (self.owner, skill_id),
            )).fetchone()
            if not row or not row["enabled"] or not await permitted(connection, row):
                fail(403, "HABIT_SKILL_REVOKED", "习惯技能不存在、来源失效或已撤销")
            if row["version"] != version or row["skill_version"] != version:
                fail(409, "HABIT_SKILL_VERSION_CHANGED", "习惯技能版本已变化，请不要静默替换旧版本")
            return {"skill_id": str(skill_id), "version": version, "content": row["files"]["SKILL.md"]}


async def tools(database, owner, selection, context, parent_guard):
    from langchain_core.tools import StructuredTool

    rows = await database.fetch("SELECT meta_skill_id FROM habit_settings WHERE owner_id=%s AND enabled", (owner,))
    if (not context or context.get("mode") not in {"ask", "work"}
            or (context.get("mode") == "work" and not context.get("lease_token"))
            or not rows or str(rows[0]["meta_skill_id"]) not in [str(value) for value in selection["skill_ids"]]):
        return []
    library = HabitLibrary(database, owner, selection, context or {}, parent_guard)
    async def invoke(operation, *args, status):
        try:
            return await operation(*args)
        except APIError as error:
            if not 400 <= error.status < 500 or error.code == "HABIT_RUN_EXPIRED":
                raise
            return {"status": status, "code": error.code, "message": error.message}

    async def record_habit_preference(kind: str, value: str) -> dict:
        return await invoke(library.record, kind, value, status="rejected")

    async def list_learned_skills() -> dict:
        return await invoke(library.listing, status="unavailable")

    async def read_learned_skill(skill_id: UUID, version: int) -> dict:
        return await invoke(library.read, skill_id, version, status="unavailable")

    result = [StructuredTool.from_function(coroutine=list_learned_skills, name="list_learned_skills",
              description="列出本人当前获准习惯子Skill及显式版本，不自动注入或执行。"),
              StructuredTool.from_function(coroutine=read_learned_skill, name="read_learned_skill",
              description="按明确skill_id和version读取本人习惯Skill；撤权或过期拒绝。")]
    if context and context.get("mode") == "ask":
        result.append(StructuredTool.from_function(coroutine=record_habit_preference, name="record_habit_preference",
                      description="从当前真实用户原始消息核验有限全局长期偏好，三条独立消息才生成私有Skill。"))
    return result
