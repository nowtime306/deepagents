"""One owner-bound terminal Work consumer rebuild; never resume/replay the graph."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from app.operation_journal import action_digest


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Recovery:
    def __init__(self, consumer, profiles, receiver, path: Path, identity: dict, tool_name: str):
        if set(identity) != {'environment_id','owner_id','root_id','namespace'} or any(str(UUID(value)) != value for value in identity.values()):
            raise ValueError('Canonical four-field ownership required')
        if not tool_name.startswith('mcpw_') or not path.parent.is_dir() or any(p.is_symlink() or p.is_junction() for p in (path,*path.parents)):
            raise ValueError('Owned write tool and private unaliased storage required')
        if path.exists() and path.stat().st_size > 65536:
            raise ValueError('Oversized recovery receipt')
        self.consumer, self.db, self.profiles = consumer, consumer.db, profiles
        self.receiver, self.path, self.identity, self.tool_name = receiver, path, dict(identity), tool_name
        self.lock = asyncio.Lock()
        self.record = json.loads(path.read_text()) if path.exists() else None
        self.storage_failed = False

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(self.record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            self.storage_failed = True
            raise

    async def observe(self, owner, root):
        owner, root = str(UUID(str(owner))), str(UUID(str(root)))
        if str(owner) != self.identity['owner_id'] or root != self.identity['root_id']:
            raise PermissionError('Foreign recovery scope')
        rows = await self.db.fetch(
            "SELECT id,owner_id,model_id,status,lease_token,lease_until,cancel_requested FROM work_queue_runs WHERE id=%s AND owner_id=%s",
            (UUID(root), UUID(owner)))
        if len(rows) != 1:
            raise ValueError('Original real Work root required')
        run = rows[0]
        provider = self.profiles.root_summary(owner, 'work', root)
        operations = await self.db.fetch(
            'SELECT id,run_id,approval_id,name,args,digest,status,exit_code,output,started_at,finished_at FROM operation_executions WHERE run_id=%s ORDER BY created_at,action_index',
            (UUID(root),))
        if len(operations) != 1:
            raise ValueError('Exactly one original approved operation required')
        operation = operations[0]
        approvals = await self.db.fetch(
            'SELECT id,decision,consumed_at,payload FROM work_approvals WHERE id=%s AND run_id=%s',
            (operation['approval_id'], UUID(root)))
        usage = await self.db.fetch(
            "SELECT status,input_tokens,output_tokens FROM model_usage WHERE owner_id=%s AND mode='work' AND run_id=%s",
            (UUID(owner), UUID(root)))
        namespace = await self.db.fetch('SELECT id FROM work_sandbox_namespace')
        active = await self.db.fetch(
            "SELECT id FROM work_queue_runs WHERE status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED')")
        sessions = await self.db.fetch('SELECT state,retention_required FROM work_sandbox_sessions WHERE run_id=%s', (UUID(root),))
        receiver = json.loads(json.dumps(self.receiver.observe()))
        with self.profiles.lock:
            binding = self.profiles.models.get(run['model_id'])
        if (run['status'] != 'NEEDS_REVIEW' or run['lease_token'] is not None or run['lease_until'] is not None
                or run['cancel_requested'] or active
                or binding != {'case_id':'AET-W07','role':'lead','owner_id':str(owner)}
                or operation['status'] != 'UNKNOWN' or operation['finished_at'] is not None
                or operation['name'] != self.tool_name or operation['args'] != {'record':'one'}
                or operation['digest'] != action_digest(self.tool_name,{'record':'one'})
                or len(approvals) != 1 or approvals[0]['decision'] != 'approve' or approvals[0]['consumed_at'] is None
                or [{key:item.get(key) for key in ('name','args')} for item in approvals[0]['payload'].get('action_requests',[])] != [{'name':self.tool_name,'args':{'record':'one'}}]
                or provider != {'recorded':True,'complete':True,'case_id':'AET-W07','provider_calls':1,'tokens':15}
                or len(usage) != 1 or usage[0]['status'] != 'KNOWN'
                or int(usage[0]['input_tokens']) != 10 or int(usage[0]['output_tokens']) != 5
                or len(namespace) != 1 or str(namespace[0]['id']) != self.identity['namespace']
                or not sessions or any(row['state'] != 'RELEASED' or row['retention_required'] for row in sessions)
                or receiver['scope'] != self.identity
                or receiver['write_attempts'] != 1 or receiver['write_count'] != 1):
            raise ValueError('Original unknown write is not in a verified quiescent observation state')
        checkpoint = await self.consumer.checkpoint(root)
        return {'root_id':root, 'operation_id':str(operation['id']), 'operation_digest':digest(operation),
                'checkpoint_id':checkpoint,'receiver':receiver,'provider':provider,
                'root_status':'NEEDS_REVIEW','operation_status':'UNKNOWN'}

    async def rebuild(self, owner, root):
        async with self.lock:
            if self.record is not None or self.storage_failed:
                raise ValueError('Existing/unknown recovery intent must not be replayed')
            async with asyncio.timeout(5):
                while self.consumer.worker.active_run_id == root:
                    await asyncio.sleep(.02)
            before = await self.observe(owner, root)
            if self.consumer.worker.active_run_id is not None:
                raise ValueError('Other consumer operation in flight')
            previous = await self.consumer.registered()
            self.record = {'schema_version':1,'case_id':'AET-W07','scope':dict(self.identity),
                           'phase':'REBUILDING','before_worker_ids':previous,'before':before}
            self.save()
            self.consumer.stop.set()
            await asyncio.wait_for(asyncio.shield(self.consumer.task),5)
            if await self.db.fetch('SELECT id FROM work_worker_instances WHERE id=%s',(UUID(previous[0]),)):
                raise ValueError('Old consumer did not unregister')
            self.consumer.start()
            following = await self.consumer.registered()
            after = await self.observe(owner, root)
            if before != after or previous == following:
                raise ValueError('Recovery changed original facts or failed to replace consumer')
            self.record.update(phase='OBSERVATION_RECOVERED', after_worker_ids=following, after=after)
            self.save()
            return self.record

    async def read(self, owner, root):
        if self.storage_failed or self.record is None or self.record['phase'] != 'OBSERVATION_RECOVERED':
            raise ValueError('Recovery unknown or never requested')
        value = await self.observe(owner,root)
        if value != self.record['after']:
            raise ValueError('Facts changed after original recovery receipt')
        return self.record
