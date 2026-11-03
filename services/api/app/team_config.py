"""Published member snapshots and explicit parent resource limits."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.model_catalog import supports_chat


class TeamMember(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff-]*$")
    version_id: UUID
    goal: str = Field(min_length=1, max_length=1500)


def subset(parent: dict, child: dict) -> None:
    for key in ("knowledge_ids", "skill_ids", "mcp_ids"):
        if not set(child.get(key, [])) <= set(parent.get(key, [])):
            fail(422, "TEAM_RESOURCE_SCOPE", "团队成员资源必须包含在主智能体的明确授权中")
    if child.get("memory_enabled") and not parent.get("memory_enabled"):
        fail(422, "TEAM_RESOURCE_SCOPE", "请先为主智能体授权长期记忆")
    if child.get("web_enabled") and (not parent.get("web_enabled") or child.get("web_mode") != parent.get("web_mode")):
        fail(422, "TEAM_RESOURCE_SCOPE", "成员联网方式必须与主智能体的授权一致")
    if child.get("workflow_version_ids"):
        fail(422, "TEAM_WORKFLOW_UNSUPPORTED", "首版只读团队成员暂不支持工作流工具，请选择不绑定工作流的版本")


async def freeze_team(capabilities, owner: UUID, selection: dict) -> dict:
    members = selection.get("team") or []
    if not members:
        return selection
    if selection.get("workflow_version_ids"):
        fail(422, "TEAM_WORKFLOW_UNSUPPORTED", "首版团队暂不混用工作流工具，请分别配置智能体")
    names = [member["name"] for member in members]
    if len(members) > 6 or len(set(names)) != len(names):
        fail(422, "TEAM_MEMBERS_INVALID", "团队最多六个成员，角色名不能重复")
    frozen = []
    for member in members:
        rows = await capabilities.database.fetch(
            "SELECT v.id,v.version,v.snapshot FROM agent_versions v JOIN agents a ON a.id=v.agent_id "
            "WHERE v.id=%s AND v.owner_id=%s AND a.owner_id=%s AND v.active AND a.enabled",
            (member["version_id"], owner, owner),
        )
        if not rows:
            fail(403, "TEAM_MEMBER_UNAVAILABLE", "成员版本已下线、停用或无权访问")
        row, child = rows[0], rows[0]["snapshot"]
        if child.get("team") or child.get("id") == str(selection.get("id")):
            fail(422, "TEAM_NESTING_FORBIDDEN", "只允许一层团队，不能包含自身或其他团队")
        subset(selection, child)
        models = await capabilities.database.fetch(
            "SELECT revision,kind,protocol FROM model_configs WHERE id=%s AND enabled", (child["model_id"],)
        )
        if not models:
            fail(409, "TEAM_MODEL_UNAVAILABLE", "成员模型不存在或已停用")
        if not supports_chat(models[0]["kind"], models[0]["protocol"]):
            fail(422, "TEAM_MODEL_UNSUPPORTED", "团队成员需要支持当前协议的聊天模型")
        child = await capabilities.freeze(owner, child)
        frozen.append(
            {
                **member,
                "snapshot": {**child, "published_version_id": str(row["id"]), "published_version": row["version"]},
            }
        )
    return {**selection, "team_pins": frozen}
