"""Approved isolated protocol execution using the existing Work backend and lease."""

import base64
import json
import shlex
import zlib
from pathlib import Path

from langchain_core.tools import StructuredTool

from app.errors import fail
from app.permissions import require_account
from app.resource_tools import connection_identity, digest, used

BRIDGE = Path(__file__).with_name("tool_protocol_bridge.py").read_text(encoding="utf-8")
EXECUTABLES = {"/usr/local/bin/python", "/usr/bin/python3", "/usr/bin/node", "/usr/local/bin/node"}
MAX_TEXT = 2000


def protocol_command(body):
    encoded = base64.b64encode(zlib.compress(BRIDGE.encode())).decode()
    program = (
        "import base64,io,sys,zlib;"
        "sys.stdin=io.StringIO(base64.b64decode(sys.stdin.buffer.read()).decode());"
        "exec(compile(zlib.decompress(base64.b64decode('" + encoded + "')),'<protocol>','exec'))"
    )
    payload = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
    command = "printf %s " + shlex.quote(payload) + " | python -c " + shlex.quote(program)
    if len(command) > 16000:
        fail(413, "ISOLATED_INPUT_TOO_LARGE", "命令配置与输入合计超过任务协议预算，请缩短配置或输入后重新审批")
    return command


def validate_command(command):
    if (
        not isinstance(command, list)
        or not 1 <= len(command) <= 32
        or any(not isinstance(part, str) or not part or len(part) > 4096 or "\x00" in part for part in command)
        or command[0] not in EXECUTABLES
        or sum(map(len, command)) > 16384
    ):
        fail(422, "ISOLATED_COMMAND_INVALID", "请选择隔离镜像内Python或Node绝对路径，最多32个有界参数")


def task_tool(database, backend, server, owner_id, selection, agent_id=None):
    bridge = server.get("bridge_digest") or digest(BRIDGE)
    semantic = digest({"target": connection_identity(server), "bridge": bridge})
    operation_name = "isolated_" + str(server["id"]).replace("-", "") + "_" + semantic[:16]

    async def isolated_protocol(**payload):
        """在已审批任务环境中发现或调用隔离MCP/ACP；不执行宿主命令。"""
        if digest(BRIDGE) != bridge:
            fail(409, "ISOLATED_BRIDGE_CHANGED", "隔离执行脚本已变化，原审批不能执行新脚本")
        text, operation = payload.get("text", ""), payload.get("operation", "discover")
        tool_name, arguments = payload.get("tool_name", ""), payload.get("arguments", {})
        await require_account(database, owner_id, "tools.use")
        current = await database.fetch("SELECT * FROM mcp_servers WHERE id=%s AND enabled", (server["id"],))
        if not current:
            fail(403, "MCP_DISABLED", "隔离工具已停用或删除")
        table, clause = (
            ("agents", "id=%s AND owner_id=%s AND enabled") if agent_id else ("capability_bindings", "owner_id=%s")
        )
        params = (agent_id, owner_id) if agent_id else (owner_id,)
        bound = await database.fetch("SELECT mcp_ids FROM " + table + " WHERE " + clause, params)
        if not bound or str(server["id"]) not in bound[0]["mcp_ids"]:
            fail(403, "MCP_BINDING_REVOKED", "此隔离工具授权已撤销")
        validate_command(server["command_json"])
        if (
            not isinstance(text, str)
            or not isinstance(tool_name, str)
            or not isinstance(arguments, dict)
            or operation not in {"discover", "call"}
            or len(text) > MAX_TEXT
            or len(tool_name) > 200
        ):
            fail(422, "ISOLATED_INPUT_INVALID", "隔离协议输入无效或超过限制")
        if operation == "call" and server["transport"] == "stdio":
            if not isinstance(payload.get("tool_schema"), dict) or not isinstance(payload.get("read_only"), bool):
                fail(422, "ISOLATED_TOOL_SCHEMA_REQUIRED", "请将发现的工具参数结构与只读声明一并提交本次审批")
        body = {
            "transport": server["transport"],
            "command": server["command_json"],
            "text": text,
            "operation": operation,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "expected_schema": payload.get("tool_schema"),
            "expected_read_only": payload.get("read_only"),
        }
        await used("mcp", server["id"])
        result = await backend._execute(
            protocol_command(body), 50, operation_name, payload
        )
        if result.exit_code != 0:
            fail(502, "ISOLATED_PROTOCOL_FAILED", "隔离协议执行失败，未自动重发，请检查任务运行证据")
        return result.output

    tool = StructuredTool.from_function(
        coroutine=isolated_protocol,
        name=operation_name,
        description=(server["name"] + "；" + server["transport"]
                     + "；逐次审批隔离执行，文本最多2000字符，配置与输入合计受编码预算限制"),
        args_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": MAX_TEXT},
                "operation": {"type": "string", "enum": ["discover", "call"]},
                "tool_name": {"type": "string", "maxLength": 200},
                "arguments": {"type": "object", "description": "与容器命令合计受16000字符编码预算限制"},
                "tool_schema": {"type": "object", "description": "call时提供发现的准确参数结构，随本次操作审批"},
                "read_only": {"type": "boolean", "description": "call时提供发现的只读声明"},
            },
            "additionalProperties": False,
        },
    )
    tool.metadata = {
        "requires_approval": True,
        "target": "隔离程序；目标摘要 " + semantic[:16],
    }
    return tool
