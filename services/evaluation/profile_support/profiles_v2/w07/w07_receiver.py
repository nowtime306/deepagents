"""W07 synthetic-only write receiver, with a committed effect and lost response."""
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import UUID

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

FIELDS = ('environment_id', 'owner_id', 'root_id', 'namespace')


class Ledger:
    def __init__(self, path: Path, scope: dict):
        if set(scope) != set(FIELDS) or any(type(v) is not str or str(UUID(v)) != v for v in scope.values()):
            raise ValueError('Exact canonical owned scope required')
        if path.suffix != '.sqlite' or not path.parent.is_dir():
            raise ValueError('Private existing SQLite directory required')
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise ValueError('Aliased receiver storage rejected')
        self.path, self.scope = path, dict(scope)
        encoded = json.dumps(scope, sort_keys=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS scope(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, accepted INTEGER NOT NULL CHECK(accepted IN (0,1)))')
            db.execute("CREATE TABLE IF NOT EXISTS effects(id INTEGER PRIMARY KEY CHECK(id=1), record TEXT NOT NULL CHECK(record='one'))")
            db.execute('INSERT OR IGNORE INTO scope VALUES(1,?)', (encoded,))
            if db.execute('SELECT value FROM scope').fetchall() != [(encoded,)]:
                raise ValueError('Foreign persisted receiver scope')

    def write(self):
        with sqlite3.connect(self.path, timeout=2) as db:
            db.execute('BEGIN IMMEDIATE')
            permitted = db.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
            db.execute('INSERT INTO attempts(accepted) VALUES(?)', (int(permitted),))
            if permitted:
                db.execute("INSERT INTO effects VALUES(1,'one')")
        if not permitted:
            raise ValueError('Write replay attempted; original effect retained')

    def observe(self):
        with sqlite3.connect(f'{self.path.as_uri()}?mode=ro', uri=True) as db:
            attempts = db.execute('SELECT id,accepted FROM attempts ORDER BY id').fetchall()
            effects = db.execute('SELECT id,record FROM effects ORDER BY id').fetchall()
        return {'scope': dict(self.scope), 'write_attempts': len(attempts), 'write_count': len(effects),
                'effects': effects, 'digest': hashlib.sha256(json.dumps([attempts,effects]).encode()).hexdigest()}


def application(ledger: Ledger, bearer: str):
    if type(bearer) is not str or len(bearer) < 32 or not bearer.isascii():
        raise ValueError('Private ASCII bearer required')
    server = MCPServer('W07-synthetic-write', log_level='ERROR')

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    def ae_write_record(record: Literal['one']) -> str:
        """Commit one fixed synthetic record; real callers require Work approval."""
        ledger.write()
        return 'one'

    mcp = server.streamable_http_app(stateless_http=True, json_response=True, max_request_body_size=4096)
    expected = ('Bearer ' + bearer).encode()

    async def app(scope, receive, send):
        if scope['type'] != 'http':
            return await mcp(scope, receive, send)
        headers = dict(scope.get('headers', []))
        if not hmac.compare_digest(headers.get(b'authorization', b''), expected):
            return await response(send,401,b'')
        scoped_headers = {('x-w07-' + key.replace('_','-')).encode():value.encode() for key,value in ledger.scope.items()}
        if any(not hmac.compare_digest(headers.get(key,b''), value) for key,value in scoped_headers.items()):
            return await response(send,403,b'')
        if scope['path'] == '/w07/evidence' and scope['method'] == 'GET':
            return await response(send,200,json.dumps(ledger.observe()).encode())
        before = ledger.observe()['write_count']

        async def lose_response(message):
            # The first tool call has committed before MCP emits response start.
            if ledger.observe()['write_count'] > before:
                if message['type'] == 'http.response.start':
                    message = {**message,'headers': [(b'content-type',b'application/json'),(b'content-length',b'999999')]}
                if message['type'] == 'http.response.body':
                    message = {**message,'body':b'', 'more_body':False}
            await send(message)

        await mcp(scope, receive, lose_response)

    return app


async def response(send, status, body):
    await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'cache-control',b'no-store')]})
    await send({'type':'http.response.body','body':body})
