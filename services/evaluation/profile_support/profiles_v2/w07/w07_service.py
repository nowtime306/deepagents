"""Test-target-only fixed receiver lifecycle and observation recovery APIs."""
import asyncio
import json
import os
import secrets
import socket
from threading import Lock
from uuid import UUID, uuid4

import uvicorn
from app.resource_runtime import current_scope
from psycopg.types.json import Jsonb

from profiles_v2.w07.w07_receiver import Ledger, application
from profiles_v2.w07.w07_recovery import Recovery

BASE = '/api/v1/agent-evaluations/profiles/w07-v2'


class Service:
    def __init__(self, app, proof):
        self.app, self.db = app, app.state.db
        self.original_client_factory = app.state.mcp.http_client_factory
        self.original_reserve = app.state.evaluation_profiles.reserve
        self.reservation_lock = Lock()
        self.owner, self.environment, self.namespace = (str(proof[key]) for key in ('owner_id','environment_id','sandbox_namespace'))
        self.directory = app.state.settings.data_dir/'w07-v2'
        self.directory.mkdir(exist_ok=True)
        self.path = self.directory/'intent.json'
        # Reconstructed targets keep old evidence and may only be inspected by operators.
        self.record = json.loads(self.path.read_text()) if self.path.exists() else None
        self.ledger = self.server = self.listener_task = self.sock = self.recovery = None
        self.lock = asyncio.Lock()

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(self.record,stream,sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,self.path)

    @property
    def scope(self):
        return {'environment_id':self.environment,'owner_id':self.owner,'namespace':self.namespace,
                'root_id':self.record.get('root_id',self.record['server_id'])}

    @property
    def model_id(self):
        return self.record['model_id']

    @property
    def tool_name(self):
        return self.record['tool_name']

    def bind(self, root):
        root = str(UUID(root))
        if self.server is None or self.record['phase'] not in {'READY','BOUND'}:
            raise ValueError('Original live receiver required')
        if self.record.get('root_id') not in {None,root}:
            raise ValueError('W07 cannot adopt a different root')
        if self.ledger is None:
            self.record.update(root_id=root,phase='BOUND')
            self.save()
            self.ledger = Ledger(self.directory/'effects.sqlite',self.scope)

    def write(self):
        if self.ledger is None:
            raise ValueError('No original real Work root bound')
        self.ledger.write()

    def observe(self):
        if self.ledger is not None:
            return self.ledger.observe()
        return {'scope':self.scope,'write_attempts':0,'write_count':0,'effects':[],'digest':'unbound'}

    def client_factory(self, headers):
        active = current_scope()
        identity = self.scope if active is None else {
            'environment_id':self.environment,'namespace':self.namespace,
            'owner_id':str(active.owner),'root_id':str(active.run)}
        headers = dict(headers or {})
        headers = {key:value for key,value in headers.items() if not key.lower().startswith('x-w07-')}
        headers.update({'x-w07-'+key.replace('_','-'):value for key,value in identity.items()})
        return self.original_client_factory(headers)

    def descriptor(self):
        if self.record is None or self.record['phase'] not in {'READY','BOUND','CLOSED'}:
            raise ValueError('Confirmed original receiver required')
        return {'id':self.record['server_id'],'model_id':self.model_id,
                'tool_name':self.tool_name,'phase':self.record['phase']}

    def reserve(self, case_id, role, messages, *, cost=None):
        if case_id != 'AET-W07':
            return self.original_reserve(case_id,role,messages,cost=cost)
        with self.reservation_lock:
            scope = current_scope()
            if (scope is None or scope.mode != 'work' or str(scope.owner) != self.owner
                    or self.record is None or self.record['phase'] != 'BOUND'
                    or str(scope.run) != self.record['root_id'] or role != 'lead'
                    or cost not in {None,15}):
                raise ValueError('Original bound W07 generation scope required')
            state = self.app.state.evaluation_profiles
            summary = state.case_summary(self.owner,'AET-W07')
            if not summary['complete'] or summary['roots'] or summary['calls'] or summary['tokens']:
                raise ValueError('Durable W07 single-root one-call budget exhausted or unknown')
            # Original reserve persists the 1/15 reservation before releasing this gate.
            return self.original_reserve(case_id,role,messages,cost=15)

    async def initialize(self, model_id):
        async with self.lock:
            if self.record is not None:
                raise ValueError('Original init intent exists; no automatic receiver reconstruction')
            state = self.app.state.evaluation_profiles
            with state.lock:
                if state.models.get(model_id) != {'case_id':'AET-W07','role':'lead','owner_id':self.owner}:
                    raise PermissionError('Original sealed W07 model required')
            config = await self.app.state.models.get(model_id)
            if config.model != 'eval::AET-W07::v2':
                raise ValueError('Explicit W07 v2 definition required')
            server_id = str(uuid4())
            self.record = {'phase':'INITIALIZING','model_id':model_id,'server_id':server_id}
            self.save()
            secret = secrets.token_urlsafe(32)
            self.sock = socket.socket()
            self.sock.bind(('127.0.0.1',0))
            self.sock.listen(128)
            self.sock.setblocking(False)
            port = self.sock.getsockname()[1]
            self.server = uvicorn.Server(uvicorn.Config(application(self,secret),access_log=False,log_level='critical'))
            self.listener_task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
            async with asyncio.timeout(5):
                while not self.server.started:
                    if self.listener_task.done(): self.listener_task.result()
                    await asyncio.sleep(.01)
            vault = self.app.state.models.vault
            await self.db.execute(
                "INSERT INTO mcp_servers(id,name,url,token_ciphertext,allow_private) VALUES(%s,%s,%s,%s,true)",
                (UUID(server_id),'W07-v2-'+server_id,f'http://127.0.0.1:{port}/mcp',vault.encrypt(secret)))
            config = (await self.db.fetch('SELECT * FROM mcp_servers WHERE id=%s',(UUID(server_id),)))[0]
            tools = await self.app.state.mcp.discover(config)
            if len(tools) != 1 or tools[0]['name'] != 'ae_write_record' or tools[0]['read_only']:
                raise ValueError('Exact one real write tool expected')
            tools[0]['enabled'] = True
            await self.db.execute('UPDATE mcp_servers SET tools=%s WHERE id=%s',(Jsonb(tools),UUID(server_id)))
            config['tools'] = tools
            tool = self.app.state.mcp.tool(config,tools[0],owner_id=self.owner)
            self.record.update(phase='READY',tool_name=tool.name)
            self.save()
            return {'id':server_id,'tool_name':tool.name}

    def recovery_service(self):
        if self.ledger is None or self.record['phase'] not in {'BOUND','CLOSED'}:
            raise ValueError('Original unknown write source required')
        if self.recovery is None:
            self.recovery = Recovery(self.app.state.evaluation_work_consumer,self.app.state.evaluation_profiles,
                                     self,self.directory/'recovery.json',self.scope,self.tool_name)
        return self.recovery

    async def observations(self):
        recovery = self.recovery_service()
        facts = await recovery.observe(self.owner,self.scope['root_id'])
        return {'facts':facts,'recovery':await recovery.read(self.owner,self.scope['root_id']) if recovery.record else None,
                'closed':self.record['phase'] == 'CLOSED'}

    async def close(self):
        if self.record['phase'] != 'BOUND':
            raise ValueError('Only original observed receiver can be closed')
        await self.recovery_service().read(self.owner,self.scope['root_id'])
        self.record['phase'] = 'CLOSING'
        self.save()
        await self.shutdown()
        await self.db.execute("UPDATE mcp_servers SET enabled=false,token_ciphertext='' WHERE id=%s",(UUID(self.record['server_id']),))
        self.record['phase'] = 'CLOSED'
        self.save()
        return {'closed':True,'receiver':self.observe()}

    async def shutdown(self):
        if self.server is not None:
            self.server.should_exit = True
            await asyncio.wait_for(asyncio.shield(self.listener_task),5)
            self.sock.close()
