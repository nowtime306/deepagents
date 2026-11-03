"""Live source authorization, including hidden model calls and paused batches."""

from uuid import UUID

from app.errors import fail
from app.permissions import require_account


async def authorize(database, owner: UUID, run: UUID, mode: str) -> dict | None:
    """Do not let a frozen snapshot grant authority after account/source revocation."""
    if mode == "resource":
        rows = await database.fetch(
            "SELECT resource_kind,action FROM resource_operations WHERE id=%s AND owner_id=%s "
            "AND status='RUNNING' AND lease_until>now() AND NOT cancel_requested",
            (run, owner),
        )
        if not rows:
            fail(409, "RESOURCE_LEASE_LOST", "资源操作已停止或租约失效")
        permission = (
            "memory.manage"
            if rows[0]["resource_kind"] == "memory"
            else ("knowledge.manage" if rows[0]["action"] == "index" else "knowledge.search")
        )
        await require_account(database, owner, permission)
        return None
    await require_account(database, owner, {"ask": "chat.run", "work": "work.execute"}.get(mode, "workflows.execute"))
    if mode not in {"ask", "work"}:
        return None
    table = "task_runs" if mode == "ask" else "work_queue_runs"
    rows = await database.fetch(
        f"SELECT origin,automation_occurrence_id,batch_item_id,agent_snapshot FROM {table} WHERE id=%s", (run,)
    )
    if rows and (rows[0]["agent_snapshot"] or {}).get("internal_purpose") == "ocr":
        await require_account(database, owner, "knowledge.manage")
        await require_account(database, owner, "media.use")
        jobs = await database.fetch(
            "SELECT j.id FROM knowledge_ocr_jobs j JOIN knowledge_bases k ON k.id=j.knowledge_id "
            "WHERE j.run_id=%s AND j.owner_id=%s AND k.owner_id=%s AND j.status='PENDING'", (run, owner, owner),
        )
        if not jobs:
            fail(403, "OCR_REVOKED", "OCR 已取消或来源知识库已撤销")
    if rows and (rows[0]["agent_snapshot"] or {}).get("internal_purpose") == "memory":
        await require_account(database, owner, "memory.manage")
        await require_account(database, owner, "chat.read")
        jobs = await database.fetch(
            "SELECT j.id FROM memory_jobs j JOIN conversations c ON c.id=j.source_conversation_id "
            "WHERE j.run_id=%s AND j.owner_id=%s AND c.owner_id=%s AND j.status='PENDING'",
            (run, owner, owner),
        )
        if not jobs:
            fail(403, "MEMORY_SOURCE_REVOKED", "记忆提取的来源或授权已撤销")
    if not rows or rows[0]["origin"] == "interactive":
        return None
    await require_account(database, owner, "automation.execute")
    root = rows[0]
    if root["origin"] == "schedule":
        allowed = await database.fetch(
            "SELECT a.id FROM automation_occurrences o JOIN automations a ON a.id=o.automation_id "
            "WHERE o.id=%s AND a.owner_id=%s AND a.paused_at IS NULL AND a.deleted_at IS NULL",
            (root["automation_occurrence_id"], owner),
        )
    else:
        allowed = await database.fetch(
            "SELECT b.id FROM batch_items i JOIN agent_batches b ON b.id=i.batch_id "
            "WHERE i.id=%s AND b.owner_id=%s AND b.status IN ('ACTIVE','PAUSED') AND b.deleted_at IS NULL",
            (root["batch_item_id"], owner),
        )
    if not allowed:
        fail(403, "AUTOMATION_REVOKED", "主动任务来源已暂停、取消或撤销")
    return root
