"""Knowledge resource authority and accounting without a second model execution loop."""

from app.errors import APIError, fail
from app.permissions import require_account
from app.resource_usage import EmbeddingMeter, finish_operation, operation, parent_authority


async def authority(database, owner, knowledge, context, model=None, kind="embedding"):
    await parent_authority(database, owner, context)
    permission = "knowledge.manage" if context.get("action") == "index" else "knowledge.search"
    await require_account(database, owner, permission)
    if context.get("require_manage"):
        await require_account(database, owner, "knowledge.manage")
    rows = await database.fetch("SELECT * FROM knowledge_bases WHERE id=%s AND owner_id=%s", (knowledge, owner))
    if not rows:
        fail(403, "KNOWLEDGE_REVOKED", "知识库已删除或授权已撤销")
    if context.get("expected_pins"):
        from app.knowledge_pins import guard

        await guard(database, rows[0], context["expected_pins"])
    if model:
        column = "rerank_model_id" if kind == "rerank" else "embedding_model_id"
        if rows[0][column] != model.id:
            fail(403, "KNOWLEDGE_REVOKED", "知识库模型配置已变化，未继续请求旧模型")
    if context["mode"] not in {"ask", "work"}:
        return
    table = "task_runs" if context["mode"] == "ask" else "work_queue_runs"
    run = (await database.fetch(f"SELECT agent_snapshot FROM {table} WHERE id=%s", (context["id"],)))[0]
    snapshot = run["agent_snapshot"] or {}
    if snapshot.get("id"):
        current = await database.fetch(
            "SELECT knowledge_ids FROM agents WHERE id=%s AND owner_id=%s AND enabled", (snapshot["id"], owner)
        )
    else:
        current = await database.fetch("SELECT knowledge_ids FROM capability_bindings WHERE owner_id=%s", (owner,))
    allowed = current[0]["knowledge_ids"] if current else []
    if str(knowledge) not in allowed or (snapshot and str(knowledge) not in snapshot.get("knowledge_ids", [])):
        fail(403, "KNOWLEDGE_REVOKED", "知识库绑定已撤销")


def must_stop(error):
    return isinstance(error, APIError) and (
        error.status == 403
        or error.code.endswith("BUDGET_EXCEEDED")
        or "LEASE" in error.code
        or error.code
        in {
            "RESOURCE_PARENT_STOPPED",
            "WORK_USAGE_UNAVAILABLE",
            "EVALUATION_SOURCE_CHANGED",
            "EVALUATION_USAGE_UNKNOWN",
        }
    )


async def context_for(service, knowledge, model, context=None, *, action="query", source=None, revision=None):
    if context is not None:
        return context, False
    context = await operation(
        service.database,
        knowledge["owner_id"],
        model,
        "knowledge",
        action,
        resource=knowledge["id"],
        source=source,
        revision=revision,
    )
    context["action"] = action
    return context, True


def meter(service, knowledge, config, context, *, kind="embedding"):
    return EmbeddingMeter(
        service.database, knowledge["owner_id"], config, context, call_kind=kind, knowledge=knowledge["id"]
    )


async def finish(service, context, own, error=None):
    if not own or not context:
        return
    unknown = await service.database.fetch(
        "SELECT 1 FROM model_usage WHERE run_id=%s AND status IN ('PENDING','UNKNOWN') LIMIT 1", (context["id"],)
    )
    status = "UNKNOWN" if error and unknown else "FAILED" if error else "SUCCEEDED"
    code = error.code if isinstance(error, APIError) else "KNOWLEDGE_RESULT_UNKNOWN" if error else None
    if not await finish_operation(service.database, context, status, code):
        fail(409, "RESOURCE_LEASE_LOST", "资源操作已失效，不返回迟到结果")
