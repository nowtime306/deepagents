"""Bounded MCP Streamable HTTP discovery and calls using the official Python SDK."""

import asyncio
import hashlib
import ipaddress
import json
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2
from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from app.errors import fail
from app.operation_journal import OperationJournal


async def validate_target(url: str, allow_private: bool) -> None:
    """Restrict configured targets and refuse metadata/link-local management endpoints."""
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        fail(422, "INVALID_MCP_URL", "MCP 地址或端口格式无效")
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
    ):
        fail(422, "INVALID_MCP_URL", "请填写不含账号或查询参数的 HTTP(S) MCP 地址")
    try:
        records = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, port)
    except socket.gaierror:
        fail(422, "INVALID_MCP_HOST", "无法解析 MCP 地址，请检查主机名")
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            fail(422, "MCP_ADDRESS_BLOCKED", "禁止访问链路本地、元数据或多播地址")
        if not address.is_global and not allow_private:
            fail(422, "MCP_ADDRESS_BLOCKED", "本地或内网服务需明确勾选“允许访问内网”")
    if port in {3100, 8100, 55432, 2375, 2376}:
        fail(422, "MCP_ADDRESS_BLOCKED", "禁止访问平台、数据库或容器管理端口")


def server_pin(server: dict) -> str:
    payload = {key: server[key] for key in ("url", "allow_private", "token_ciphertext", "tools")}
    if server.get("transport", "streamable_http") != "streamable_http":
        payload["transport"] = server["transport"]
    if server.get("transport") in {"stdio", "acp"}:
        payload["command_json"] = server.get("command_json", [])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MCPService:
    """Keep credentials server-side and recheck allowed tools before every invocation."""

    def __init__(self, database, vault) -> None:
        self.database, self.vault = database, vault
        self.http_client_factory = lambda headers: httpx2.AsyncClient(
            headers=headers, timeout=20, follow_redirects=False, trust_env=False
        )

    @asynccontextmanager
    async def session(self, server: dict, *, owner_id=None):
        await validate_target(server["url"], server["allow_private"])
        if server.get("transport") in {"stdio", "acp"}:
            fail(422, "MCP_STDIO_REQUIRES_WORK", "stdio工具须在已批准任务环境中执行")
        if server.get("oauth_required") and not owner_id:
            fail(403, "MCP_OAUTH_REQUIRED", "请先为当前账号授权此MCP连接")
        token = self.vault.decrypt(server["token_ciphertext"]).get_secret_value()
        if owner_id:
            from app.mcp_oauth import access_token

            authorized = await access_token(self.database, self.vault, owner_id, server)
            token = authorized if authorized is not None else token
        headers = {"Authorization": f"Bearer {token}"} if token else None
        if server.get("transport") == "sse":
            def factory(**kwargs):
                return self.http_client_factory(kwargs.get("headers"))

            async with sse_client(server["url"], headers=headers, timeout=20, sse_read_timeout=20,
                                  httpx_client_factory=factory) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=20) as session:
                    await session.initialize()
                    yield session
            return
        async with (
            self.http_client_factory(headers) as client,
            streamable_http_client(server["url"], http_client=client) as streams,
        ):
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=20) as session:
                await session.initialize()
                yield session

    async def discover(self, server: dict, *, owner_id=None) -> list[dict]:
        """Return schemas and conservative read-only flags; new tools start disabled."""
        async with asyncio.timeout(25), self.session(server, **({"owner_id": owner_id} if owner_id else {})) as session:
            result = await session.list_tools()
        previous = {item["name"]: item for item in server["tools"]}
        return [
            {
                "name": tool.name,
                "description": (tool.description or "")[:2000],
                "schema": tool.input_schema,
                "read_only": bool(
                    tool.annotations and tool.annotations.read_only_hint and not tool.annotations.destructive_hint
                ),
                "enabled": bool(previous.get(tool.name, {}).get("enabled", False)),
            }
            for tool in result.tools[:100]
        ]

    async def invoke(
        self, server_id: str, name: str, arguments: dict, *, before_write=None, expected_pin: str | None = None,
        owner_id=None, resolved_server=None, expected_tool=None, before_call=None,
    ) -> str:
        """Recheck enabled schemas; writes require a fresh single-use Work authorization callback."""
        rows = await self.database.fetch("SELECT * FROM mcp_servers WHERE id=%s AND enabled", (server_id,))
        if not rows:
            fail(403, "MCP_DISABLED", "MCP 服务已停用或删除")
        live = rows[0]
        from app.resource_runtime import current_scope
        from app.resource_tools import connection_identity, digest, frozen_server, used

        server = await frozen_server(live, self.vault)
        if bool(live.get("oauth_required")) != bool(server.get("oauth_required")):
            fail(403, "MCP_AUTHORIZATION_CHANGED", "MCP授权方式已变化，请核对当前授权")
        if server.get("allow_private") and not live.get("allow_private"):
            fail(403, "MCP_PRIVATE_ACCESS_REVOKED", "MCP内网访问授权已撤销")
        live_tool = next((item for item in live["tools"] if item["name"] == name and item["enabled"]), None)
        allowed = next((item for item in server["tools"] if item["name"] == name and item["enabled"]), None)
        if not live_tool:
            fail(403, "MCP_TOOL_DENIED", "此工具已停用或移除")
        if not allowed or (not allowed["read_only"] and before_write is None):
            fail(403, "MCP_TOOL_DENIED", "此工具未启用或需要自主任务审批")
        if live_tool["read_only"] != allowed["read_only"] or live_tool["schema"] != allowed["schema"]:
            fail(409, "MCP_TOOL_INTERFACE_CHANGED", "此工具参数结构或读写性质已变化，请核对接口与本次审批")
        if expected_tool and digest({key: expected_tool[key] for key in ("name", "schema", "read_only")}) != digest(
            {key: allowed[key] for key in ("name", "schema", "read_only")}
        ):
            fail(409, "MCP_TOOL_INTERFACE_CHANGED", "工具实例与本次固定接口不符，原审批不能执行其他接口")
        if (resolved_server and (not current_scope() or not allowed["read_only"])
                and connection_identity(resolved_server) != connection_identity(server)):
            fail(409, "MCP_TARGET_CHANGED", "工具目标与本次固定配置不符，原审批不能执行新的目标")
        async with asyncio.timeout(25), self.session(server, **({"owner_id": owner_id} if owner_id else {})) as session:
            current = await session.list_tools()
            tool = next((item for item in current.tools if item.name == name), None)
            readonly = bool(
                tool and tool.annotations and tool.annotations.read_only_hint and not tool.annotations.destructive_hint
            )
            if (
                not tool
                or readonly != allowed["read_only"]
                or tool.input_schema != allowed["schema"]
            ):
                fail(403, "MCP_TOOL_DENIED", "工具权限声明或参数结构已变化，需重新发现并审核")
            if not allowed["read_only"]:
                await before_write()
            await used("mcp", server_id)
            if before_call:
                await before_call()
            elif owner_id:
                from app.permissions import require_account

                await require_account(self.database, owner_id, "tools.use")
            fresh = await self.database.fetch("SELECT * FROM mcp_servers WHERE id=%s AND enabled", (server_id,))
            if not fresh:
                fail(403, "MCP_DISABLED", "MCP服务在调用前已停用或删除")
            declaration = next((item for item in fresh[0]["tools"]
                                if item["name"] == name and item["enabled"]), None)
            if not declaration:
                fail(403, "MCP_TOOL_DENIED", "此工具在调用前已停用或移除")
            if declaration["schema"] != allowed["schema"] or declaration["read_only"] != allowed["read_only"]:
                fail(409, "MCP_TOOL_INTERFACE_CHANGED", "工具参数结构或读写性质在调用前已变化")
            if (bool(fresh[0].get("oauth_required")) != bool(server.get("oauth_required"))
                    or server.get("allow_private") and not fresh[0].get("allow_private")):
                fail(403, "MCP_AUTHORIZATION_CHANGED", "MCP访问授权在调用前已变化")
            response = await session.call_tool(name, arguments=arguments)
        if response.is_error:
            fail(502, "MCP_TOOL_FAILED", "MCP 工具执行失败，请检查服务日志")
        return json.dumps(response.model_dump(mode="json"), ensure_ascii=False)[:24000]

    def tool(
        self,
        server: dict,
        metadata: dict,
        *,
        owner_id,
        agent_id=None,
        work_run: dict | None = None,
        expected_pin: str | None = None,
    ) -> StructuredTool:
        """Adapt a discovered JSON-schema tool into a LangChain tool."""

        async def binding() -> None:
            if agent_id:
                current = await self.database.fetch(
                    "SELECT mcp_ids FROM agents WHERE id=%s AND owner_id=%s AND enabled", (agent_id, owner_id)
                )
            else:
                current = await self.database.fetch(
                    "SELECT mcp_ids FROM capability_bindings WHERE owner_id=%s", (owner_id,)
                )
            if not current or str(server["id"]) not in current[0]["mcp_ids"]:
                fail(403, "MCP_BINDING_REVOKED", "此 MCP 绑定已撤销")

        from app.resource_tools import operation_identity

        write = not metadata["read_only"]
        name = (
            "mcpw_"
            + str(server["id"]).replace("-", "")
            + "_"
            + operation_identity(server, metadata)[:16]
            if write
            else f"mcp_{str(server['id']).replace('-', '')[:10]}_{metadata['name']}"
        )

        async def access():
            from app.permissions import require_account

            if owner_id:
                await require_account(self.database, owner_id, "tools.use")
            await binding()

        async def invoke(**arguments) -> str:
            from app.permissions import require_account

            if owner_id:
                await require_account(self.database, owner_id, "tools.use")
            await binding()
            if not write:
                options = {"resolved_server": server, "expected_tool": metadata, "before_call": access}
                if server.get("oauth_required"):
                    options["owner_id"] = owner_id
                return await self.invoke(str(server["id"]), metadata["name"], arguments, **options)
            if not work_run:
                fail(403, "MCP_WRITE_REQUIRES_WORK", "写工具仅可用于固定配置并逐次审批的自主任务")
            journal = OperationJournal(self.database, work_run)
            operation = None

            async def authorize():
                nonlocal operation
                await require_account(self.database, owner_id, "tools.use")
                await binding()
                administrators = await self.database.fetch("SELECT is_admin FROM users WHERE id=%s", (owner_id,))
                if not administrators or not administrators[0]["is_admin"]:
                    fail(403, "MCP_WRITE_ADMIN_REQUIRED", "MCP 写工具仅开放给平台管理员的自主任务")
                current = await self.database.fetch(
                    "SELECT * FROM mcp_servers WHERE id=%s AND enabled", (server["id"],)
                )
                if not current:
                    fail(403, "MCP_DISABLED", "MCP 服务已停用或删除")
                allowed = next((item for item in current[0]["tools"]
                                if item["name"] == metadata["name"] and item["enabled"]), None)
                if not allowed:
                    fail(403, "MCP_TOOL_DENIED", "此工具已停用或移除")
                operation = await journal.start(name, arguments)

            try:
                result = await self.invoke(
                    str(server["id"]), metadata["name"], arguments, before_write=authorize,
                    resolved_server=server, expected_tool=metadata, before_call=access,
                    **({"owner_id": owner_id} if server.get("oauth_required") else {}),
                )
                if operation:
                    await journal.finish(operation, 0, result)
                return result
            except BaseException:
                if operation:
                    await journal.unknown(operation)
                raise

        tool = StructuredTool.from_function(
            name=name,
            description=metadata["description"] or metadata["name"],
            coroutine=invoke,
            args_schema=metadata["schema"],
        )
        if write:
            tool.metadata = {"requires_approval": True, "target": f"MCP 写操作：{server['name']} / {metadata['name']}"}
        return tool
