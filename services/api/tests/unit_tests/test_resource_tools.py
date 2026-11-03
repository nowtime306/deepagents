"""Approvals bind execution semantics while maintenance and unrelated tools remain independent."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from mcp.types import ListToolsResult, Tool, ToolAnnotations

from app.errors import APIError
from app.mcp_service import MCPService
from app.operation_journal import action_digest
from app.tool_transports import task_tool


def test_browser_receipt_binds_trajectory_not_just_arguments():
    args = {'skill_id': 's', 'version': 1, 'parameters': {'customer': 'controlled'}}
    old = action_digest('run_browser_skill', args, semantic_digest='published-path')
    assert old != action_digest('run_browser_skill', args, semantic_digest='replaced-path')
    assert action_digest('ordinary', args) == action_digest('ordinary', deepcopy(args))


async def test_readonly_http_schema_change_refuses_before_actual_tool_call():
    server = {'id': str(uuid4()), 'tools': [{'name': 'lookup', 'enabled': True, 'read_only': True,
               'schema': {'type': 'object'}}]}
    calls = []

    class Database:
        async def fetch(self, *args):
            return [server]

    class Session:
        async def list_tools(self):
            return ListToolsResult(tools=[Tool(name='lookup', input_schema={'type': 'object', 'required': ['new']},
                annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))])

        async def call_tool(self, *args, **kwargs):
            calls.append(args)

    @asynccontextmanager
    async def session(*args):
        yield Session()

    service = MCPService(Database(), None)
    service.session = session
    with pytest.raises(APIError, match='参数结构已变化'):
        await service.invoke(server['id'], 'lookup', {}, resolved_server=server)
    assert calls == []


async def test_stdio_keeps_frozen_command_binds_bridge_and_hides_command(monkeypatch):
    server = {'id': str(uuid4()), 'name': 'isolated', 'transport': 'stdio', 'tools': [],
              'command_json': ['/usr/local/bin/python', '-c', 'secret-command-literal']}
    calls = []

    class Database:
        async def fetch(self, query, params=()):
            if 'mcp_servers' in query:
                return [server | {'command_json': []}]
            return [{'mcp_ids': [server['id']]}]

    class Backend:
        async def _execute(self, command, *args):
            calls.append(command)
            return SimpleNamespace(exit_code=0, output='controlled')

    async def permission(*args):
        return None

    monkeypatch.setattr('app.tool_transports.require_account', permission)
    monkeypatch.setattr('app.tool_transports.protocol_command', lambda body: body)
    tool = task_tool(Database(), Backend(), server, uuid4(), {'mcp_ids': [server['id']]})
    assert 'secret-command-literal' not in str(tool.metadata)
    assert await tool.ainvoke({'operation': 'discover'}) == 'controlled'
    assert calls[0]['command'] == server['command_json']
    monkeypatch.setattr('app.tool_transports.BRIDGE', 'replacement-script')
    with pytest.raises(APIError, match='原审批不能执行新脚本'):
        await tool.ainvoke({'operation': 'discover'})
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["disabled", "tool_disabled", "schema", "readonly", "permission", "binding"])
async def test_list_tools_await_cannot_bypass_live_revocation(change):
    server = {"id": str(uuid4()), "enabled": True, "allow_private": False, "oauth_required": False,
              "tools": [{"name": "lookup", "enabled": True, "read_only": True, "schema": {"type": "object"}}]}
    calls = []
    revoked = False

    class Database:
        async def fetch(self, *args):
            return [deepcopy(server)] if server["enabled"] else []

    class Session:
        async def list_tools(self):
            nonlocal revoked
            if change == "disabled":
                server["enabled"] = False
            elif change == "tool_disabled":
                server["tools"][0]["enabled"] = False
            elif change == "schema":
                server["tools"][0]["schema"] = {"type": "object", "required": ["new"]}
            elif change == "readonly":
                server["tools"][0]["read_only"] = False
            else:
                revoked = True
            return ListToolsResult(tools=[Tool(name="lookup", input_schema={"type": "object"},
                annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))])

        async def call_tool(self, *args, **kwargs):
            calls.append(args)

    @asynccontextmanager
    async def session(*args):
        yield Session()

    async def authority():
        if revoked:
            raise APIError(403, "AUTHORITY_REVOKED", "permission or binding revoked during discovery")

    service = MCPService(Database(), None)
    service.session = session
    with pytest.raises(APIError):
        await service.invoke(server["id"], "lookup", {}, before_call=authority)
    assert calls == []
