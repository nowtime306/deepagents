"""Atomically enqueue automatic work into the existing durable workers."""

from dataclasses import asdict
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from app.errors import fail
from app.harness import ASK_POLICY
from app.model_catalog import supports_chat
from app.permissions import require_account
from app.web_search import ensure_available
from app.work_network import domain, policy


async def target(state, owner: UUID, body) -> dict:
    await require_account(state.db, owner, "automation.execute")
    await require_account(state.db, owner, "chat.run" if body.mode == "ask" else "work.execute")
    rows = await state.db.fetch(
        "SELECT v.snapshot,v.id,v.version FROM agent_versions v JOIN agents a ON a.id=v.agent_id "
        "WHERE v.id=%s AND v.owner_id=%s AND a.owner_id=%s AND v.active AND a.enabled",
        (body.agent_version_id, owner, owner),
    )
    if not rows:
        fail(403, "AGENT_VERSION_UNAVAILABLE", "请选择本人可用的已发布智能体版本")
    row = rows[0]
    snapshot = {**row["snapshot"], "published_version_id": str(row["id"]), "published_version": row["version"]}
    snapshot["allow_local_browser"] = False
    config = await state.models.get(snapshot["model_id"])
    if not supports_chat(config.kind, config.protocol) or config.revision != snapshot["model_revision"]:
        fail(409, "AGENT_MODEL_CHANGED", "发布版本的模型已变化或不支持聊天，请重新发布")
    if snapshot.get("workflow_version_ids"):
        fail(422, "AUTOMATION_WORKFLOW_UNSUPPORTED", "自动/批量任务暂不能绑定工作流工具，请使用独立工作流执行入口")
    if snapshot.get("web_enabled") and snapshot.get("web_mode") != "openai_native":
        await ensure_available(state.db)
    if body.mode == "work":
        if not state.settings.work_enabled:
            fail(503, "WORK_DISABLED", "任务执行尚未开放")
        try:
            body.network_domains = list(dict.fromkeys(domain(value) for value in body.network_domains))
            await policy(body.network_domains)
        except (ValueError, OSError):
            fail(422, "NETWORK_DOMAIN_INVALID", "授权域名无效或指向非公网地址")
    return await state.capabilities.freeze(owner, snapshot)


async def capacity(connection, owner: UUID) -> bool:
    # All automatic dispatchers serialize admission for an account; interactive runs bypass this quota.
    await connection.execute("SELECT pg_advisory_xact_lock(710024,hashtext(%s))", (str(owner),))
    row = await (
        await connection.execute(
            "SELECT (SELECT count(*) FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
            "WHERE c.owner_id=%s AND r.origin<>'interactive' AND r.status IN ('PENDING','RUNNING'))+"
            "(SELECT count(*) FROM work_queue_runs WHERE owner_id=%s AND origin<>'interactive' "
            "AND status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED')) AS count",
            (owner, owner),
        )
    ).fetchone()
    return row["count"] < 2


async def enqueue(
    connection, source: dict, goal: str, *, occurrence: UUID | None = None, item: UUID | None = None
) -> tuple:
    run, destination = uuid4(), uuid4()
    snapshot, owner = {**source["snapshot"], "allow_local_browser": False}, source["owner_id"]
    origin = "schedule" if occurrence else "batch"
    budget = source.get("budget", source.get("item_budget"))
    if source["mode"] == "ask":
        await connection.execute(
            "INSERT INTO conversations(id,owner_id,title,agent_id,agent_version_id) VALUES(%s,%s,%s,%s,%s)",
            (destination, owner, source["name"], snapshot.get("id"), snapshot["published_version_id"]),
        )
        harness = {**asdict(ASK_POLICY), **budget}
        await connection.execute(
            "INSERT INTO task_runs(id,conversation_id,model_id,model_revision,status,agent_snapshot,"
            "attachment_snapshot,harness_policy,origin,automation_occurrence_id,batch_item_id) "
            "VALUES(%s,%s,%s,%s,'PENDING',%s,'[]',%s,%s,%s,%s)",
            (
                run,
                destination,
                snapshot["model_id"],
                snapshot["model_revision"],
                Jsonb(snapshot),
                Jsonb(harness),
                origin,
                occurrence,
                item,
            ),
        )
        await connection.execute(
            "INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) VALUES(%s,%s,%s,'user',%s)",
            (uuid4(), destination, run, Jsonb({"text": goal, "attachments": []})),
        )
    else:
        await connection.execute(
            "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,%s,%s)",
            (destination, owner, source["name"], goal),
        )
        await connection.execute(
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,model_revision,"
            "environment,step_limit,token_limit,agent_snapshot,instructions,network_domains,input_files,"
            "origin,automation_occurrence_id,batch_item_id) "
            "VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,'[]',%s,%s,%s)",
            (
                run,
                destination,
                owner,
                source["name"],
                goal,
                snapshot["model_id"],
                snapshot["model_revision"],
                source["environment"],
                budget["model_calls"],
                budget["token_limit"],
                Jsonb(snapshot),
                snapshot.get("instructions", ""),
                Jsonb(source["network_domains"]),
                origin,
                occurrence,
                item,
            ),
        )
    return run, destination


async def channel_pins(database, owner: UUID, identifiers: list) -> list:
    rows = await database.fetch(
        "SELECT id,revision FROM notification_channels WHERE owner_id=%s AND enabled AND id=ANY(%s::uuid[])",
        (owner, identifiers),
    )
    if len(rows) != len(set(map(str, identifiers))):
        fail(422, "NOTIFICATION_CHANNEL_UNAVAILABLE", "通知通道不存在、停用或不属于本人")
    return jsonable_encoder(rows)
