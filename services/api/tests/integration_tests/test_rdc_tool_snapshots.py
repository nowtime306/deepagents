"""Resource maintenance keeps frozen tool semantics; authority and approvals remain live."""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client
from test_browser_skill_api import PREFIX, seed

from app.browser_skill_work import browser_skill_tool
from app.errors import APIError
from app.operation_journal import OperationJournal, receipts
from app.resource_runtime import release_credentials, resource_run
from app.resource_tools import connection_identity, frozen_skill, operation_identity
from app.work_queue import WorkQueue
from tests.unit_tests.test_browser_skill_work import Runtime, nothing


def test_http_mcp_warm_target_and_key_cold_rotation_and_target_confirmation(client):
    c, app = client
    owner = UUID(c.get('/api/v1/auth/me').json()['id'])
    c.portal.call(app.state.db.execute, 'UPDATE users SET is_admin=true WHERE id=%s', (owner,))
    identifier = c.post('/api/v1/capabilities/mcp', headers=ORIGIN,
        json={'name': uuid4().hex, 'url': 'http://127.0.0.1:8791/mcp', 'allow_private': True}).json()['id']
    tool = {'name': 'lookup', 'description': 'original', 'schema': {'type': 'object'},
            'enabled': True, 'read_only': True}
    c.portal.call(app.state.db.execute, 'UPDATE mcp_servers SET tools=%s,token_ciphertext=%s WHERE id=%s',
                  (Jsonb([tool]), app.state.models.vault.encrypt('original-token'), identifier))
    assert c.put("/api/v1/capabilities/bindings", headers=ORIGIN,
                 json={"mcp_ids": [identifier]}).status_code == 200
    original_revision = c.portal.call(app.state.db.fetch,
        "SELECT configuration_revision FROM mcp_servers WHERE id=%s", (identifier,))[0]["configuration_revision"]
    calls, effects = [], []
    behavior = {"revoke": None}

    class Session:
        async def list_tools(self):
            if behavior["revoke"] == "permission":
                await app.state.db.execute("UPDATE users SET is_admin=false,role_id='viewer' WHERE id=%s", (owner,))
            elif behavior["revoke"] == "binding":
                await app.state.db.execute(
                    "UPDATE capability_bindings SET mcp_ids=%s WHERE owner_id=%s", (Jsonb([]), owner))
            return ListToolsResult(tools=[Tool(name='lookup', input_schema=tool['schema'],
                annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))])

        async def call_tool(self, name, arguments):
            effects.append(name)
            return CallToolResult(content=[TextContent(type='text', text='controlled reply')])

    @asynccontextmanager
    async def session(server):
        calls.append((server['url'], app.state.models.vault.decrypt(server['token_ciphertext']).get_secret_value()))
        yield Session()

    app.state.mcp.session = session
    run_id = uuid4()

    async def scenario():
        async with resource_run(app.state.db, owner, 'work', run_id):
            await app.state.mcp.invoke(identifier, 'lookup', {})
            await app.state.db.execute(
                "UPDATE mcp_servers SET name=name||'-new',url='http://127.0.0.1:8792/mcp',token_ciphertext=%s "
                "WHERE id=%s", (app.state.models.vault.encrypt('rotated-token'), identifier))
            current = (await app.state.db.fetch("SELECT * FROM mcp_servers WHERE id=%s", (identifier,)))[0]
            # Workflow nodes build a tool from the current raw row on every node invocation.
            raw_tool = app.state.mcp.tool(current, current["tools"][0], owner_id=owner)
            await raw_tool.ainvoke({})
            await app.state.db.execute("UPDATE mcp_servers SET tools=%s WHERE id=%s",
                (Jsonb([tool | {"read_only": False}]), identifier))
            with pytest.raises(APIError, match="读写性质"):
                await app.state.mcp.invoke(identifier, "lookup", {})
            await app.state.db.execute("UPDATE mcp_servers SET tools=%s WHERE id=%s",
                (Jsonb([tool | {"schema": {"type": "object", "required": ["changed"]}}]), identifier))
            with pytest.raises(APIError, match="参数结构"):
                await app.state.mcp.invoke(identifier, "lookup", {})
            await app.state.db.execute("UPDATE mcp_servers SET tools=%s WHERE id=%s", (Jsonb([tool]), identifier))
            await app.state.db.execute('UPDATE mcp_servers SET enabled=false WHERE id=%s', (identifier,))
            with pytest.raises(APIError, match='停用'):
                await app.state.mcp.invoke(identifier, 'lookup', {})
            await app.state.db.execute('UPDATE mcp_servers SET enabled=true WHERE id=%s', (identifier,))
        release_credentials(owner, 'work', run_id)
        async with resource_run(app.state.db, owner, 'work', run_id):
            with pytest.raises(APIError, match='目标已变化'):
                await app.state.mcp.invoke(identifier, 'lookup', {})
        await app.state.db.execute(
            "UPDATE mcp_servers SET url='http://127.0.0.1:8791/mcp' WHERE id=%s", (identifier,))
        async with resource_run(app.state.db, owner, 'work', run_id):
            await app.state.mcp.invoke(identifier, 'lookup', {})
            for change in ("permission", "binding"):
                behavior["revoke"] = change
                row = (await app.state.db.fetch("SELECT * FROM mcp_servers WHERE id=%s", (identifier,)))[0]
                raw_tool = app.state.mcp.tool(row, row["tools"][0], owner_id=owner)
                with pytest.raises(APIError):
                    await raw_tool.ainvoke({})
                await app.state.db.execute("UPDATE users SET is_admin=true,role_id='member' WHERE id=%s", (owner,))
                await app.state.db.execute("UPDATE capability_bindings SET mcp_ids=%s WHERE owner_id=%s",
                                           (Jsonb([identifier]), owner))
                behavior["revoke"] = None
        release_credentials(owner, 'work', run_id)

    c.portal.call(scenario)
    assert calls[:2] == [('http://127.0.0.1:8791/mcp', 'original-token')]*2
    assert calls[2:] == [('http://127.0.0.1:8791/mcp', 'rotated-token')]*3
    assert effects == ['lookup']*3
    snapshots = c.portal.call(app.state.db.fetch,
        "SELECT snapshot,credential_changed,revision FROM run_resource_snapshots WHERE run_id=%s", (run_id,))
    assert snapshots[0]['credential_changed']
    assert snapshots[0]['revision'] == str(original_revision)
    assert snapshots[0]['snapshot']['configuration_revision'] == original_revision
    assert 'original-token' not in str(snapshots) and '/mcp' not in str(snapshots[0]['snapshot'])


def test_semantic_tool_identity_excludes_maintenance_but_binds_specific_schema_and_target():
    server = {'url': 'https://first.test/mcp', 'transport': 'streamable_http', 'allow_private': False,
              'tools': [], 'name': 'original', 'token_ciphertext': 'old'}
    tool = {'name': 'write', 'schema': {'type': 'object'}, 'read_only': False}
    original = operation_identity(server, tool)
    changed = server | {'name': 'changed', 'token_ciphertext': 'new', 'tools': [{'description': 'unrelated'}]}
    assert operation_identity(changed, tool) == original
    assert operation_identity(server | {'url': 'https://other.test/mcp'}, tool) != original
    assert operation_identity(server, tool | {'schema': {'type': 'object', 'required': ['x']}}) != original
    assert connection_identity(server) == connection_identity(changed)


def test_browser_approval_reads_and_executes_frozen_skill_after_latest_update(client, monkeypatch):
    monkeypatch.setattr('app.work_network.public_addresses', lambda host: ['93.184.216.34'])
    c, app = client
    owner = UUID(c.get('/api/v1/auth/me').json()['id'])
    c.portal.call(app.state.db.execute, 'UPDATE users SET is_admin=true WHERE id=%s', (owner,))
    recording = seed(c, app)
    skill_id = c.post(f'{PREFIX}/recordings/{recording}/publish', headers=ORIGIN,
                      json={'revision': 1}).json()['skill_id']
    c.put('/api/v1/capabilities/bindings', headers=ORIGIN, json={'skill_ids': [skill_id]})
    task = c.post('/api/v1/work-tasks', headers=ORIGIN,
                  json={'title': 'frozen browser', 'goal': 'controlled'}).json()['id']
    app.state.settings.work_enabled = True
    c.portal.call(app.state.db.execute, 'INSERT INTO work_worker_instances(id) VALUES(%s)', (uuid4(),))
    result = c.post(f'/api/v1/work-tasks/{task}/runs', headers=ORIGIN,
        json={'revision': 1, 'model_id': 'test', 'environment': 'browser', 'allow_local_browser': True,
              'network_domains': ['fixture.test']})
    assert result.status_code == 202, result.text
    run = c.portal.call(WorkQueue(app.state.db).claim)
    approval = uuid4()
    class BrowserRuntime(Runtime):
        async def command(self, *args, **kwargs):
            reply = await super().command(*args, **kwargs)
            if args[:2] == ("cooperate", "sync"):
                value = json.loads(reply)
                value["observation"]["text"] = '@vom 1\n @e99 textbox "Name"'
                return json.dumps(value)
            return reply

    runtime = BrowserRuntime()
    monkeypatch.setattr('app.browser_skill_work.guard', nothing)

    async def freeze_and_approve():
        async with resource_run(app.state.db, owner, 'work', run['id']):
            fixed = await frozen_skill(app.state.db, app.state.models.vault, owner, skill_id)
            args = {'skill_id': skill_id, 'version': 1, 'parameters': {'input_2': 'controlled'},
                    'execution_boundary': 'local_authenticated_browser', 'skill_name': fixed['name'], 'step_count': 2}
            payload = {'action_requests': [{'name': 'run_browser_skill', 'args': args}]}
            async with app.state.db.pool.connection() as connection:
                await connection.execute(
                    "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,expires_at,"
                    "decision,consumed_at) VALUES(%s,%s,%s,'controlled',%s,'digest',"
                    "now()+interval '1 hour','approve',now())",
                    (approval, run['id'], owner, Jsonb(payload)))
                await receipts(connection, {'id': approval, 'run_id': run['id']}, payload['action_requests'])
            return args

    args = c.portal.call(freeze_and_approve)
    c.portal.call(app.state.db.execute,
        "UPDATE skills SET name=name||'-latest',version=version+1,files=%s WHERE id=%s",
        (Jsonb({'SKILL.md': 'new unrelated body'}), skill_id))
    endpoint = f'{PREFIX}/approvals/{approval}/skills/{skill_id}'
    view = c.get(endpoint)
    assert view.status_code == 200 and view.json()['version'] == 1, view.text
    assert 'trajectory.json' in view.json()['files']

    async def execute():
        async with resource_run(app.state.db, owner, 'work', run['id']):
            await frozen_skill(app.state.db, app.state.models.vault, owner, skill_id)
            backend = SimpleNamespace(journal=OperationJournal(app.state.db, run))
            tool = browser_skill_tool(app.state.db, runtime, backend, run, run['agent_snapshot'])
            return await tool.ainvoke(args)

    assert '"ok": true' in c.portal.call(execute)
    trajectory = c.portal.call(app.state.db.fetch,
        'SELECT trajectory FROM browser_skill_versions WHERE skill_id=%s AND version=1', (skill_id,))[0]['trajectory']
    replaced = deepcopy(trajectory)
    replaced['steps'][0]['url'] = 'https://other.fixture.test'
    c.portal.call(app.state.db.execute,
        'UPDATE browser_skill_versions SET trajectory=%s WHERE skill_id=%s AND version=1', (Jsonb(replaced), skill_id))
    assert c.get(endpoint).status_code == 409
    with pytest.raises(APIError, match='发布路径'):
        c.portal.call(execute)
    c.portal.call(app.state.db.execute, 'UPDATE skills SET enabled=false WHERE id=%s', (skill_id,))
    assert c.get(endpoint).status_code == 403
    release_credentials(owner, 'work', run['id'])
