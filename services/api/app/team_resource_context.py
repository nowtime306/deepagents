"""Carry a verified team-call identity across internal Work knowledge HTTP only."""

from contextlib import asynccontextmanager

from app.errors import fail
from app.team_context import active_member

CONTRACT = "work-member-resource-v1"


@asynccontextmanager
async def member_context(
    database, owner, run, lease, selection, identifier, knowledge_id
):
    if identifier is None:
        yield
        return

    async def authority():
        rows = await database.fetch(
            "SELECT t.id,t.role_name,t.version_id,t.model_id,p.team AS current_team,"
            "a.knowledge_ids AS child_knowledge_ids FROM team_calls t "
            "JOIN work_queue_runs r ON r.id=t.work_run_id AND r.owner_id=t.owner_id "
            "JOIN agent_versions v ON v.id=t.version_id AND v.owner_id=t.owner_id AND v.active "
            "JOIN agents a ON a.id=v.agent_id AND a.owner_id=t.owner_id AND a.enabled "
            "JOIN agents p ON p.id=%s AND p.owner_id=t.owner_id AND p.enabled "
            "JOIN model_configs m ON m.id=t.model_id AND m.enabled "
            "WHERE t.id=%s AND t.work_run_id=%s AND t.owner_id=%s AND t.lease_token=%s "
            "AND t.status='RUNNING' AND NOT t.cancel_requested AND r.status='RUNNING' "
            "AND r.lease_token=%s AND r.lease_until>now() AND NOT r.cancel_requested",
            (selection.get("id"), identifier, run, owner, lease, lease),
        )
        if not rows:
            fail(403, "TEAM_MEMBER_REVOKED", "成员调用已停止、撤销或无权访问")
        row = rows[0]
        frozen = [
            pin
            for pin in selection.get("team_pins", [])
            if pin["name"] == row["role_name"]
            and str(pin["version_id"]) == str(row["version_id"])
            and (
                pin["snapshot"].get("model_routing")
                or pin["snapshot"]["model_id"] == row["model_id"]
            )
        ]
        live = [
            pin
            for pin in row["current_team"]
            if pin["name"] == row["role_name"]
            and str(pin["version_id"]) == str(row["version_id"])
        ]
        if len(frozen) != 1 or len(live) != 1:
            fail(403, "TEAM_MEMBER_REVOKED", "成员不在本轮冻结配置及当前授权中")
        knowledge = str(knowledge_id)
        if (
            knowledge not in frozen[0]["snapshot"].get("knowledge_ids", [])
            or knowledge not in row["child_knowledge_ids"]
        ):
            fail(403, "KNOWLEDGE_REVOKED", "成员知识库绑定已撤销或不在冻结授权中")
        return row

    row = await authority()
    # The endpoint performs embedding/retrieval only. Do not accept/forward caller model IDs or revisions.
    token = active_member.set({"id": row["id"], "role_name": row["role_name"]})
    try:
        yield authority
    finally:
        active_member.reset(token)
