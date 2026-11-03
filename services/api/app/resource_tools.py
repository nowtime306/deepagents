"""Freeze tool and Skill content while checking live authority separately."""

import hashlib
import json

from fastapi.encoders import jsonable_encoder

from app.errors import fail
from app.resource_runtime import current_scope, freeze_resource


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def connection_identity(server: dict) -> str:
    """Exclude credentials and labels; retain the actual execution destination."""
    return digest({key: server.get(key)
                   for key in ("url", "transport", "allow_private", "command_json", "oauth_required")})


def operation_identity(server: dict, tool: dict) -> str:
    return digest({"connection": connection_identity(server), "name": tool["name"],
                   "schema": tool["schema"], "read_only": tool["read_only"]})


async def frozen_server(server: dict, vault) -> dict:
    """Never persist a token; a cold recovery may rotate it only for the same receiver."""
    state = current_scope()
    if state is None:
        return server
    fields = {key: server.get(key) for key in (
        "id", "name", "url", "allow_private", "transport", "command_json", "tools", "oauth_required",
        "configuration_revision",
    )}
    if server.get("transport") in {"stdio", "acp"}:
        from app.tool_transports import BRIDGE

        fields["bridge_digest"] = digest(BRIDGE)
    fields["credential_marker"] = digest(server.get("token_ciphertext", ""))
    fields = jsonable_encoder(fields)
    from app.configuration_history import safe_origin

    summary = {"configuration_revision": server.get("configuration_revision"), "name": server["name"],
               "transport": server.get("transport", "streamable_http"),
               "target": safe_origin(server.get("url") or "") if server.get("url") else "隔离任务环境",
               "target_digest": connection_identity(server), "tools": [
                   {"name": tool["name"], "read_only": tool["read_only"], "schema_digest": digest(tool["schema"]),
                    "parameter_schema_digest": digest(tool["schema"].get("properties", {}))}
                   for tool in server["tools"]]}
    frozen = await freeze_resource("mcp", server["id"], server.get("configuration_revision", 1), fields,
                                   vault=vault, snapshot=summary, credential_scope=connection_identity(server))
    if frozen.get("bridge_digest") != fields.get("bridge_digest"):
        fail(409, "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED", "隔离协议执行脚本已变化，请核对并确认新的执行阶段")
    key = ("mcp", str(server["id"]))
    if key not in state.cached_credentials:
        if connection_identity(frozen) != connection_identity(server):
            fail(409, "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED", "恢复所需的MCP目标已变化，请确认新的执行阶段")
        state.cached_credentials[key] = server.get("token_ciphertext", "")
        if frozen.get("credential_marker") != fields["credential_marker"]:
            await state.database.execute(
                "UPDATE run_resource_snapshots SET credential_changed=true WHERE owner_id=%s AND mode=%s "
                "AND run_id=%s AND phase=%s AND resource_kind='mcp' AND resource_id=%s",
                (state.owner, state.mode, state.run, state.phase, str(server["id"])))
    return {**frozen, "token_ciphertext": state.cached_credentials[key]}


async def frozen_skill(database, vault, owner, identifier) -> dict:
    rows = await database.fetch(
        "SELECT id,name,version,files FROM skills WHERE id=%s AND owner_id=%s AND enabled", (identifier, owner),
    )
    if not rows:
        fail(403, "SKILL_UNAVAILABLE", "技能不存在、已停用或无权访问")
    row = rows[0]
    config = {"name": row["name"], "version": row["version"], "files": row["files"]}
    return await freeze_resource("skill", identifier, row["version"], config, vault=vault,
                                 snapshot={"name": row["name"], "version": row["version"],
                                           "content_digest": digest(row["files"]), "file_count": len(row["files"])})


async def used(kind: str, identifier) -> None:
    state = current_scope()
    if state:
        await state.database.execute(
            "UPDATE run_resource_snapshots SET used_at=coalesce(used_at,now()) WHERE owner_id=%s AND mode=%s "
            "AND run_id=%s AND phase=%s AND resource_kind=%s AND resource_id=%s",
            (state.owner, state.mode, state.run, state.phase, kind, str(identifier)),
        )


async def skill_binding(database, owner, selection, identifier):
    """Keep resource grants live independently of frozen Skill content."""
    if selection.get("id"):
        rows = await database.fetch(
            "SELECT skill_ids FROM agents WHERE id=%s AND owner_id=%s AND enabled", (selection["id"], owner))
    else:
        rows = await database.fetch("SELECT skill_ids FROM capability_bindings WHERE owner_id=%s", (owner,))
    if not rows or str(identifier) not in [str(value) for value in rows[0]["skill_ids"]]:
        fail(403, "SKILL_REVOKED", "技能绑定已撤销或智能体已停用")
