"""Owner-scoped resource evidence, separate from current configuration and business versions."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder

from app.configuration_history import safe_origin
from app.errors import fail
from app.permissions import require_account
from app.security import identity

router = APIRouter(prefix="/api/v1/resource-runs", tags=["运行配置"])
Mode = Literal["ask", "work", "workflow", "resource"]


async def owned(database, owner, mode: Mode, run: UUID):
    """Resolve ownership through the original resource, never through trace rows alone."""
    if mode == "ask":
        rows = await database.fetch(
            "SELECT r.status,r.error_code,r.agent_snapshot AS business FROM task_runs r "
            "JOIN conversations c ON c.id=r.conversation_id WHERE r.id=%s AND c.owner_id=%s", (run, owner),
        )
        permission = "chat.read"
    elif mode == "work":
        rows = await database.fetch(
            "SELECT status,error_code,agent_snapshot AS business FROM work_queue_runs WHERE id=%s AND owner_id=%s",
            (run, owner),
        )
        permission = "work.read"
    elif mode == "workflow":
        rows = await database.fetch(
            "SELECT r.status,r.snapshot AS business FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id "
            "WHERE r.id=%s AND w.owner_id=%s", (run, owner),
        )
        permission = "workflows.read"
    else:
        rows = await database.fetch(
            "SELECT status,error_code,resource_kind FROM resource_operations WHERE id=%s AND owner_id=%s",
            (run, owner),
        )
        if not rows:
            rows = await database.fetch(
                "SELECT status,error_code,'media' AS resource_kind FROM knowledge_video_jobs "
                "WHERE id=%s AND owner_id=%s", (run, owner),
            )
        kind = rows[0]["resource_kind"] if rows else "knowledge"
        permission = {"memory": "memory.read", "media": "media.read"}.get(kind, "knowledge.read")
    if not rows:
        fail(404, "NOT_FOUND", "运行不存在或无权查看")
    await require_account(database, owner, permission)
    return rows[0]


def business_version(value: dict) -> dict:
    """Publish only version identifiers, never instructions, files or submitted inputs."""
    result = {key: value[key] for key in (
        "id", "revision", "agent_id", "agent_version_id", "version_id", "version", "published_version",
        "published_version_id",
        "workflow_id", "workflow_version_id", "workflow_version_ids",
    ) if key in value}
    if value.get("team_pins"):
        result["members"] = [{"role": member.get("name"),
                              "agent_id": (member.get("snapshot") or {}).get("id"),
                              "version_id": member.get("version_id"),
                              "version": (member.get("snapshot") or {}).get("published_version")}
                             for member in value["team_pins"]]
    return result


async def current_configuration(database, owner, kind: str, identifier: str):
    """Use an explicit safe projection; historical execution_config is never returned."""
    if kind == "model":
        rows = await database.fetch(
            "SELECT m.id,m.revision,m.model,m.kind,m.provider,m.protocol,m.max_output_tokens,m.temperature,"
            "m.supports_images,c.supports_tools,m.base_url,m.enabled FROM model_configs m "
            "LEFT JOIN model_capability_declarations c ON c.model_id=m.id WHERE m.id=%s", (identifier,),
        )
        if rows:
            row = rows[0]
            import hashlib

            target = row.pop("base_url")
            row["service_target"] = safe_origin(target)
            row["service_target_digest"] = hashlib.sha256(target.encode()).hexdigest()
            return row
    elif kind == "skill":
        rows = await database.fetch(
            "SELECT name,version,files,enabled FROM skills WHERE id::text=%s AND owner_id=%s", (identifier, owner),
        )
        if rows:
            from app.resource_tools import digest

            row = rows[0]
            files = row.pop("files")
            return {**row, "content_digest": digest(files), "file_count": len(files)}
    elif kind == "mcp":
        rows = await database.fetch("SELECT * FROM mcp_servers WHERE id::text=%s", (identifier,))
        if rows:
            from app.resource_tools import connection_identity

            row = rows[0]
            return {"name": row["name"], "transport": row["transport"], "enabled": row["enabled"],
                    "target": safe_origin(row["url"]), "target_digest": connection_identity(row)}
    elif kind == "route":
        rows = await database.fetch(
            "SELECT revision AS route_revision,candidates,failures,cooldown,enabled FROM model_routes "
            "WHERE id::text=%s",
            (identifier,),
        )
        return rows[0] if rows else None
    elif kind == "knowledge":
        rows = await database.fetch(
            "SELECT embedding_model_id,rerank_model_id,keyword_min_coverage,chunk_size,chunk_overlap,"
            "processing_revision "
            "FROM knowledge_bases WHERE id::text=%s AND owner_id=%s", (identifier, owner),
        )
        if rows:
            indexes = await database.fetch(
                "SELECT DISTINCT vector_fingerprint,vector_dimensions FROM documents WHERE knowledge_id::text=%s "
                "AND vector_status='READY' ORDER BY vector_fingerprint,vector_dimensions LIMIT 64", (identifier,),
            )
            return {**rows[0], "index_compatibility": indexes}
    elif kind == "memory":
        rows = await database.fetch(
            "SELECT embedding_model_id,enabled FROM memory_settings WHERE owner_id=%s AND owner_id::text=%s",
            (owner, identifier),
        )
        return rows[0] if rows else None
    elif kind == "agent":
        rows = await database.fetch(
            "SELECT id,revision,enabled FROM agents WHERE id::text=%s AND owner_id=%s", (identifier, owner),
        )
        return rows[0] if rows else None
    elif kind == "agent_version":
        rows = await database.fetch(
            "SELECT a.id,v.id AS published_version_id,v.version AS published_version,v.revision,"
            "(a.enabled AND v.active) AS enabled FROM agent_versions v JOIN agents a ON a.id=v.agent_id "
            "WHERE v.id::text=%s AND v.owner_id=%s AND a.owner_id=%s", (identifier, owner, owner),
        )
        return rows[0] if rows else None
    return None


@router.get("/{mode}/{identifier}/configurations")
async def configurations(mode: Mode, identifier: UUID, request: Request):
    user = await identity(request)
    db = request.app.state.db
    run = await owned(db, user["id"], mode, identifier)
    scope_mode, scope_id = mode, identifier
    if mode == "workflow":
        parent = (run.get("business") or {}).get("parent_ask_run")
        if parent:
            scope_mode, scope_id = "ask", UUID(parent)
            await owned(db, user["id"], scope_mode, scope_id)
        else:
            work = await db.fetch(
                "SELECT r.id FROM work_run_workflows w JOIN work_queue_runs r ON r.id=w.run_id "
                "WHERE w.workflow_run_id=%s AND r.owner_id=%s", (identifier, user["id"]),
            )
            if not work:
                work = await db.fetch(
                    "SELECT r.id FROM external_workflow_jobs j JOIN api_credentials k ON k.id=j.credential_id "
                    "JOIN work_queue_runs r ON r.id=k.parent_work_run WHERE j.run_id=%s "
                    "AND k.owner_id=%s AND r.owner_id=%s", (identifier, user["id"], user["id"]),
                )
            if work:
                scope_mode, scope_id = "work", work[0]["id"]
                await owned(db, user["id"], scope_mode, scope_id)
    stages = await db.fetch(
        "SELECT phase,created_at,confirmed_by,reason,safe_changes FROM run_resource_stages "
        "WHERE owner_id=%s AND mode=%s AND run_id=%s ORDER BY phase", (user["id"], scope_mode, scope_id),
    )
    items = await db.fetch(
        "SELECT snapshot_id,phase,resource_kind,resource_id,revision,resolved_at,used_at,snapshot,credential_changed "
        "FROM run_resource_snapshots WHERE owner_id=%s AND mode=%s AND run_id=%s "
        "ORDER BY phase,resolved_at,snapshot_id", (user["id"], scope_mode, scope_id),
    )
    for item in items:
        item["state"] = "used" if item["used_at"] else "resolved"
        # Only keys present in the recorded safe projection are eligible for comparison.
        current = await current_configuration(db, user["id"], item["resource_kind"], item["resource_id"])
        current = jsonable_encoder(current)
        item["available"] = bool(current and current.get("enabled", True))
        item["current"] = ({key: value for key, value in current.items()
                            if key in item["snapshot"] or key == "enabled"} if current else None)
        item["differences"] = [key for key in item["snapshot"] if current and key in current
                               and current[key] != item["snapshot"][key]]
        item["calls"] = await db.fetch(
            "SELECT call_id,run_id,mode,model_id,model_revision,status,failure_code,role_name,"
            "effective_parameters,input_tokens,output_tokens FROM model_usage "
            "WHERE owner_id=%s AND resource_snapshot_id=%s ORDER BY created_at,call_id",
            (user["id"], item["snapshot_id"]),
        )
    return {"run_id": identifier, "mode": mode, "status": run["status"], "error_code": run.get("error_code"),
            "configuration_scope": {"mode": scope_mode, "run_id": scope_id},
            "can_recover": bool(user["is_admin"] or "work.execute" in user["permissions"]),
            "can_view_global_history": user["is_admin"],
            "stages": stages, "items": items,
            "business_version": business_version(run.get("business") or {}),
            "note": "已解析与已发起调用分别记录；发起调用不代表成功。历史未记录时不会用当前配置补造。"}
