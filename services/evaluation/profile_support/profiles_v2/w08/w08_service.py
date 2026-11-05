"""Private target fixture provisioning and independent native checkpoint/SQL observations."""
import asyncio
import base64
import hashlib
import json
import os
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import UUID

from app.resource_runtime import current_scope
from langchain_core.messages import AIMessage, ToolMessage
from opensandbox.models.execd import RunCommandOpts

from evaluation_runner.profiles_v2.w08.contract import actions, check_anchor
from profiles_v2.w08.w08_checkpoint import read_database as read_checkpoint


class Service:
    def __init__(self,app,proof):
        self.app,self.db,self.profiles = app,app.state.db,app.state.evaluation_profiles
        self.owner,self.environment,self.namespace = (str(proof[key]) for key in ('owner_id','environment_id','sandbox_namespace'))
        self.directory = app.state.settings.data_dir/'w08-v2'
        self.directory.mkdir(exist_ok=True)
        self.path = self.directory/'intent.json'
        self.reconstructed = self.path.exists()
        self.record = json.loads(self.path.read_text()) if self.path.exists() else None
        self.original_reserve = self.profiles.reserve
        self.original_effect_guard = getattr(self.db,'evaluation_effect_guard',None)
        self.reservation_lock,self.setup_lock = Lock(),asyncio.Lock()
        self.sandbox_lock = asyncio.Lock()
        self.sandboxes = app.state.evaluation_bounded_sandboxes
        self.original_sandbox_reserve = self.sandboxes.reserve

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(self.record,stream,sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,self.path)

    def alive(self):
        if self.record is None or datetime.now(UTC)>=datetime.fromisoformat(self.record['deadline']):
            raise ValueError('Original W08 whole-case deadline expired')

    def initialize(self,model,started_at,deadline):
        if self.record is not None:
            raise ValueError('Original fixture initialization intent already exists')
        check_anchor(started_at,deadline)
        self.record = {'phase':'READY','model_id':model,'started_at':started_at,'deadline':deadline}
        self.save()
        return {'sealed':True}

    def anchor(self):
        if self.record is None:
            raise ValueError('Original confirmed initialization required')
        return {key:self.record[key] for key in ('model_id','started_at','deadline')}

    async def effect(self,boundary):
        if self.original_effect_guard is not None:
            await self.original_effect_guard(boundary)
        scope=current_scope()
        if (boundary not in {'tool','sandbox'} or scope is None or scope.database is not self.db
                or scope.mode!='work' or str(scope.owner)!=self.owner or self.record is None
                or self.record['phase']!='BOUND' or str(scope.run)!=self.record['root_id']):
            raise ValueError('Only original owned W08 native tool/sandbox effect permitted')
        # Native WorkBackend guard / DurableSandboxes.create call this immediately
        # before SDK submission. Recheck after the original asynchronous parent guard.
        self.alive()

    def bind(self,root,model):
        self.alive()
        if (self.reconstructed or self.record['phase'] not in {'READY','BOUND'} or self.record['model_id']!=model
                or self.record.get('root_id') not in {None,root}):
            raise ValueError('Original W08 model/root binding required')
        if self.record['phase']=='READY':
            self.record.update(phase='BOUND',root_id=str(UUID(root)))
            self.save()

    def reserve(self,case_id,role,messages,*,cost=None):
        if case_id!='AET-W08':
            return self.original_reserve(case_id,role,messages,cost=cost)
        with self.reservation_lock:
            self.alive()
            scope = current_scope()
            if (self.reconstructed or scope is None or str(scope.owner)!=self.owner or scope.mode!='work'
                    or self.record['phase']!='BOUND' or str(scope.run)!=self.record['root_id'] or role!='lead'
                    or cost not in {None,15}):
                raise ValueError('Original W08 generation scope required')
            summary = self.profiles.case_summary(self.owner,'AET-W08')
            if (not summary['complete'] or summary['calls']>=6 or summary['tokens']+15>90
                    or any(row['run_id']!=self.record['root_id'] for row in summary['roots'])):
                raise ValueError('Persistent W08 one-root six-call 90-token budget unavailable')
            return self.original_reserve(case_id,role,messages,cost=15)

    async def prepare(self,run,backend,guard):
        async with self.setup_lock:
            self.alive()
            if self.record['phase']!='BOUND' or str(run['id'])!=self.record['root_id'] or str(run['owner_id'])!=self.owner:
                raise ValueError('Original owned W08 root required')
            if 'setup' in self.record:
                raise ValueError('Fixture setup intent exists; no automatic replay/adoption')
            await guard()
            self.alive()
            self.record['setup']={'phase':'SUBMITTING','sandbox_id':backend.sandbox.id}
            self.save()
            script = Path(__file__).with_name('fixture_program.py').read_bytes()
            encoded = base64.b64encode(script).decode()
            launcher = 'import base64;exec(compile(base64.b64decode('+repr(encoded)+'),"w08-fixed-fixture","exec"))'
            command = shlex.join(['python3','-c',launcher,self.record['root_id']])
            self.alive()
            result = await backend.sandbox.commands.run(command,opts=RunCommandOpts(timeout=timedelta(seconds=10)))
            if result.exit_code!=0 or result.error or len(result.text)>4096:
                raise ValueError('Actual physical W08 fixture setup failed or result unknown')
            fixture = json.loads(result.text)
            if fixture['run_id']!=self.record['root_id'] or fixture['platform']!='linux' or fixture['symlink'] is not True:
                raise ValueError('Real Linux symlink fixture not proven')
            self.record['setup'].update(phase='CONFIRMED',fixture=fixture,program_sha256=hashlib.sha256(script).hexdigest())
            self.save()

    async def reserve_sandbox(self,run_id,token,kind):
        async with self.sandbox_lock:
            self.alive()
            if (self.reconstructed or kind!='coding' or self.record['phase']!='BOUND' or str(run_id)!=self.record['root_id']
                    or 'sandbox_reservation' in self.record):
                raise ValueError('W08 permits only one original Coding sandbox reservation')
            rows = await self.db.fetch('SELECT owner_id,model_id FROM work_queue_runs WHERE id=%s AND lease_token=%s '
                                       "AND status='RUNNING' AND lease_until>now()",(run_id,token))
            if len(rows)!=1 or str(rows[0]['owner_id'])!=self.owner or rows[0]['model_id']!=self.record['model_id']:
                raise ValueError('Owned original running W08 sandbox root required')
            self.alive()
            self.record['sandbox_reservation']={'phase':'SUBMITTING'}
            self.save()
            self.alive()
            result = await self.original_sandbox_reserve(run_id,token,kind)
            # An unused capacity rejection is known, but remains consumed in this fixed batch.
            self.record['sandbox_reservation'].update(phase='CONFIRMED' if result is not None else 'CAPACITY_REJECTED')
            self.save()
            try:
                self.alive()
            except ValueError:
                if result is not None:
                    # The native reserve has returned but its caller has not reached
                    # Sandbox.create. Only this known unused local reservation is released.
                    released=await self.db.fetch("UPDATE work_sandbox_sessions SET state='RELEASED',updated_at=now() "
                        "WHERE id=%s AND run_id=%s AND owner_id=%s AND state='CREATING' AND sandbox_id IS NULL RETURNING id",
                        (result['id'],run_id,UUID(self.owner)))
                    if len(released)!=1 or str(released[0]['id'])!=str(result['id']):
                        raise ValueError('Expired pre-SDK reservation cleanup is not confirmed') from None
                    self.record['sandbox_reservation'].update(phase='EXPIRED_BEFORE_SDK_RELEASED',session_id=str(result['id']))
                    self.save()
                raise
            return result

    async def observe(self):
        if self.record is None or self.record.get('setup',{}).get('phase')!='CONFIRMED':
            raise ValueError('Confirmed original physical fixture required')
        root = self.record['root_id']
        rows = await self.db.fetch('SELECT id,owner_id,model_id,status,model_calls,used_tokens,usage_unknown,lease_token,lease_until,'
                                   'agent_snapshot,instructions,environment,restore_snapshot_id,network_domains '
                                   'FROM work_queue_runs WHERE id=%s AND owner_id=%s',(UUID(root),UUID(self.owner)))
        if len(rows)!=1:
            raise ValueError('Original real Work root missing')
        run = rows[0]
        sessions = await self.db.fetch('SELECT state,retention_required,sandbox_id FROM work_sandbox_sessions WHERE run_id=%s',(UUID(root),))
        usages = await self.db.fetch("SELECT status,input_tokens,output_tokens FROM model_usage WHERE owner_id=%s AND run_id=%s AND mode='work'",(UUID(self.owner),UUID(root)))
        namespaces = await self.db.fetch('SELECT id FROM work_sandbox_namespace')
        operations = await self.db.fetch("SELECT status FROM operation_executions WHERE run_id=%s AND status IN ('STARTED','UNKNOWN')",(UUID(root),))
        approvals = await self.db.fetch('SELECT id FROM work_approvals WHERE run_id=%s',(UUID(root),))
        provider = self.profiles.root_summary(self.owner,'work',root)
        if (run['status']!='COMPLETED' or run['model_id']!=self.record['model_id'] or run['model_calls']!=6 or run['used_tokens']!=90
                or run['usage_unknown'] or run['lease_token'] is not None or run['lease_until'] is not None
                or len(sessions)!=1 or sessions[0]['state']!='RELEASED' or sessions[0]['retention_required']
                or sessions[0]['sandbox_id']!=self.record['setup']['sandbox_id'] or operations or approvals
                or [str(row['id']) for row in namespaces]!=[self.namespace]
                or provider!={'recorded':True,'complete':True,'case_id':'AET-W08','provider_calls':6,'tokens':90}
                or len(usages)!=6 or any(row['status']!='KNOWN' or int(row['input_tokens'])!=10 or int(row['output_tokens'])!=5 for row in usages)):
            raise ValueError('Original Work usage/release/scope facts not complete')
        messages,checkpoint = await read_checkpoint(self.app.state.settings.database_url,self.db,run)
        results = collect(messages,root)
        return {'scope':{'environment_id':self.environment,'owner_id':self.owner,'root_id':root,'namespace':self.namespace},
                'initialization_anchor':self.anchor(),
                'fixture':self.record['setup']['fixture'],'tool_results':results,'checkpoint_id':checkpoint['checkpoint_id'],
                'checkpoint_reconstruction':checkpoint,
                'usage':{'calls':6,'known_calls':6,'input_tokens':60,'output_tokens':30,'unknown_calls':0,'pending_calls':0},
                'released':True,'root_status':run['status']}


def collect(messages,root):
    proposals = [call for row in messages if isinstance(row,AIMessage) for call in row.tool_calls]
    results = [row for row in messages if isinstance(row,ToolMessage)]
    sequence = actions(root)
    if len(proposals)!=5 or len(results)!=5 or len({row.tool_call_id for row in results})!=5:
        raise ValueError('Actual checkpoint must contain exactly five distinct probes')
    observed = []
    for proposal,result,(name,args) in zip(proposals,results,sequence,strict=True):
        if (proposal['name'],proposal['args'])!=(name,args) or result.tool_call_id!=proposal['id'] or result.name!=name:
            raise ValueError('Actual probe arguments/results differ from fixed definition')
        content = str(result.content)
        observed.append({'name':name,'arguments':args,'error':result.status=='error',
                         'content_contains_safe':'SAFE_INPUT' in content,
                         'canary_seen':'SYNTHETIC_W08_OUTSIDE_CANARY' in content})
    return observed
