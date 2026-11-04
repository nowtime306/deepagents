"""Explicitly bound read-only workflow tools share the parent Ask authority and ledger."""

import asyncio
import json
from uuid import UUID, uuid4

from langchain_core.tools import StructuredTool
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.graphs import resolve_model
from app.permissions import require_account
from app.resource_usage import parent_authority
from app.team_context import active_member
from app.workflow_definition import Definition
from app.workflow_runtime import WorkflowRuntime, digest
from app.workflow_schema import validate_text


class WorkflowArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID
    text: str = Field(min_length=1, max_length=16000)


async def check_scope(service, owner, selection, pins):
    rows = await service.database.fetch(
        "SELECT knowledge_ids,skill_ids,mcp_ids,workflow_version_ids,web_enabled,web_mode,memory_enabled "
        "FROM agents WHERE id=%s AND owner_id=%s AND enabled",
        (selection["id"], owner),
    )
    if not rows:
        fail(403, "WORKFLOW_REVOKED", "父智能体已停用或删除")
    current = rows[0]
    for key, pin in [("knowledge_ids", "knowledge"), ("skill_ids", "skills"), ("mcp_ids", "mcp")]:
        allowed = set(selection.get(key, [])) & set(current[key])
        if not set(pins[pin]) <= allowed:
            fail(403, "WORKFLOW_SCOPE_EXCEEDED", "子流程使用的资源超出父智能体当前授权，请先调整绑定")
    for identifier in pins["agents"]:
        agent = await service.database.fetch(
            "SELECT web_enabled,web_mode,memory_enabled FROM agents WHERE id=%s AND owner_id=%s AND enabled",
            (identifier, owner),
        )
        if (
            not agent
            or (
                agent[0]["web_enabled"]
                and not (
                    current["web_enabled"]
                    and selection.get("web_enabled")
                    and agent[0]["web_mode"] == current["web_mode"] == selection.get("web_mode")
                )
            )
            or (agent[0]["memory_enabled"] and not (current["memory_enabled"] and selection.get("memory_enabled")))
        ):
            fail(403, "WORKFLOW_SCOPE_EXCEEDED", "子智能体的联网或记忆能力超出父智能体授权")
    for child in pins.get("workflows", {}).values():
        await check_scope(service, owner, selection, child["pins"])
    return current


def workflow_tool(service, owner, selection, context):
    runtime = WorkflowRuntime(service.database, service.models, service, resolve_model)

    async def run_workflow(version_id: UUID, text: str) -> str:
        """运行智能体已明确绑定的只读发布工作流。结构化输入使用 JSON 文本；同轮相同输入复用结果，不重放未知请求。"""
        from app.execution_policy import require_execution

        require_execution(service.database)
        identifier = str(version_id)
        if identifier not in selection.get("workflow_version_ids", []):
            fail(403, "WORKFLOW_UNBOUND", "当前智能体未授权此工作流版本")
        source = await runtime.published(identifier, owner)
        definition = Definition.model_validate(source["definition"])
        validate_text(text, definition.input_schema)
        pins = await runtime.pins(definition, owner, source["model_id"])

        async def guard():
            await require_account(service.database, owner, "workflows.execute")
            await parent_authority(service.database, owner, context)
            current = await check_scope(service, owner, selection, pins)
            if identifier not in current["workflow_version_ids"]:
                fail(403, "WORKFLOW_REVOKED", "父智能体的工作流授权已撤销")
            await runtime.published(identifier, owner)

        await guard()
        fingerprint = digest([identifier, text])
        run_id = uuid4()
        async with service.database.pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(710033,hashtext(%s))", (str(context["id"]),))
            previous = await (
                await connection.execute(
                    "SELECT id,status,output FROM workflow_runs WHERE snapshot->>'parent_ask_run'=%s "
                    "AND snapshot->>'request_digest'=%s ORDER BY created_at LIMIT 1",
                    (str(context["id"]), fingerprint),
                )
            ).fetchone()
            if previous:
                if previous["status"] != "SUCCEEDED":
                    fail(409, "WORKFLOW_RESULT_UNAVAILABLE", "相同子流程尚未完成或结果未知，本轮不会再次执行")
                return json.dumps(
                    {"run_id": str(previous["id"]), "output": previous["output"], "reused": True}, ensure_ascii=False
                )
            snapshot = {
                "definition": definition.model_dump(),
                "model_id": source["model_id"],
                "instructions": source["instructions"],
                "resource_pins": pins,
                "name": source["name"],
                "published_version": source["version"],
                "parent_ask_run": str(context["id"]),
                "request_digest": fingerprint,
            }
            await connection.execute(
                "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) VALUES(%s,%s,'RUNNING',%s,%s)",
                (run_id, source["workflow_id"], text, Jsonb(snapshot)),
            )
        member = active_member.get() or {}
        token = active_member.set({key: value for key, value in member.items() if key in {"id", "role_name"}})
        try:
            async with asyncio.timeout(45):
                output = await runtime.run(
                    definition,
                    owner,
                    source["model_id"],
                    source["instructions"],
                    text,
                    run_id,
                    pins,
                    guard=guard,
                    usage_context=context,
                )
            await guard()
            await service.database.execute(
                "UPDATE workflow_runs SET status='SUCCEEDED',output=%s,finished_at=now() "
                "WHERE id=%s AND status='RUNNING'",
                (output, run_id),
            )
            return json.dumps({"run_id": str(run_id), "output": output, "reused": False}, ensure_ascii=False)
        except BaseException:
            await service.database.execute(
                "UPDATE workflow_runs SET status='NEEDS_REVIEW',finished_at=now() WHERE id=%s AND status='RUNNING'",
                (run_id,),
            )
            raise
        finally:
            active_member.reset(token)

    return StructuredTool.from_function(coroutine=run_workflow, args_schema=WorkflowArguments)
