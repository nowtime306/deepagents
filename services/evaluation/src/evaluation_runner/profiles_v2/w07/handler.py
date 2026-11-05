"""Fixed W07 lifecycle via formal HTTP; no replay of unknown write or run submission."""
import re
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from evaluation_runner.fixed_evidence import binding, load, store
from evaluation_runner.fixed_fixtures import FixedFixtures
from evaluation_runner.journal import UnknownSubmission, digest

BASE = '/api/v1/agent-evaluations/profiles/w07-v2'
PROMPT = '经审批向受控工具写入一条record。'


def check(specimen):
    if (specimen.case_id != 'AET-W07' or specimen.mode != 'work' or specimen.input != PROMPT
            or specimen.fixture_profile.id != 'unknown-tool-write' or specimen.fixture_profile.version != 2
            or specimen.approval_policy != 'approve' or specimen.repeats != 1 or specimen.metrics
            or specimen.fixture_parameters != {'protocol':'terminal-unknown-write-v2'}
            or specimen.budgets.model_dump() != {'timeout_seconds':300,'max_model_calls':5,'max_tokens':1000}
            or [v.model_dump() for v in specimen.validators] != [{'registered_id':'unknown_no_replay','version':1,'required':True,'parameters':{}}]):
        raise ValueError('Original separate W07 v2 contract required')


def admission(platform):
    proof = platform.attest()
    capability = platform.get(BASE+'/capabilities')
    if capability != {'case_id':'AET-W07','definition_version':2,'observer':'terminal-unknown-write-v2',
                       'max_roots':1,'max_model_calls':1,'max_tokens':15,'max_sandboxes':2,
                       'recovery':'same-root-observation-only','unknown_preserved':True,'healthy':True}:
        raise ValueError('Original bounded W07 controlled target required')
    platform.expected_sandbox_namespace = str(UUID(proof['sandbox_namespace']))
    platform.attest()
    return proof


def initialize(platform,journal,attempt,specimen):
    check(specimen)
    admission(platform)
    if 'w07_deadline' not in attempt:
        attempt['w07_deadline'] = (datetime.now(UTC)+timedelta(seconds=300)).isoformat()
        journal.save()
    alive(attempt)
    fixtures = FixedFixtures(platform,journal,attempt)
    model = fixtures.create('w07-model','/api/v1/admin/models',{
        'name':'AE W07 v2 '+fixtures.prefix,'model':'eval::AET-W07::v2','base_url':platform.target.api_base_url+'/controlled-model',
        'kind':'chat','provider':'custom','protocol':'openai','enabled':True,'supports_tools':True})
    fixtures.mutate('w07-seal','POST',BASE+'/model-binding',body={'model_id':model})
    receiver = fixtures.mutate('w07-receiver','POST',BASE+'/receiver',body={'model_id':model})
    descriptor = platform.get(BASE+'/receiver')
    if (descriptor['id'] != receiver['id'] or descriptor['model_id'] != model or descriptor['phase'] != 'READY'
            or re.fullmatch('mcpw_'+UUID(receiver['id']).hex+'_[0-9a-f]{16}',descriptor['tool_name']) is None):
        raise ValueError('Original receiver/model tool binding was not confirmed')
    agent = fixtures.published_agent('AET-W07',model,mcp=[receiver['id']])
    task = fixtures.work_task(PROMPT,{})
    attempt['fixture'] = {**task,**agent,'model_id':model,'server_id':receiver['id'],'tool_name':descriptor['tool_name']}
    journal.save()
    return attempt['fixture']


def alive(attempt):
    if datetime.now(UTC) >= datetime.fromisoformat(attempt['w07_deadline']):
        raise ValueError('Original full-case deadline expired; no new effects')


def save_evidence(journal,attempt,key,value):
    reference = store(journal.directory,'w07-v2',{'schema_version':1,'kind':'w07-v2','binding':binding(journal.data,attempt),'stage':key,'body':value})
    attempt.setdefault('evidence_refs',[]).append(reference)
    attempt[key] = reference
    journal.save()


def approve(platform,journal,attempt):
    fixture = attempt['fixture']
    path = '/api/v1/work-runs/'+attempt['root_id']
    rows = platform.get(path+'/approvals')['items']
    if len(rows) != 1:
        raise ValueError('Exactly one original Work approval required')
    row = rows[0]
    actions = row['payload']['action_requests']
    if (row['decision'] is not None or row.get('consumed_at') is not None or len(actions) != 1
            or actions[0]['name'] != fixture['tool_name'] or actions[0]['args'] != {'record':'one'}
            or row['digest'] != digest(row['payload'])):
        raise ValueError('Approval must match the actual fixed write proposal')
    if platform.get(path+'/operations')['items']:
        raise ValueError('No write receipt may execute before approval')
    if 'w07_approval_before' not in attempt:
        save_evidence(journal,attempt,'w07_approval_before',row)
    alive(attempt)
    return journal.post(platform,attempt,'/api/v1/work-approvals/'+row['id']+'/decisions',
                        {'decision':'approve','digest':row['digest']},'w07-approve-original')


def execute(platform,journal,attempt,specimen,*,cancelled=lambda:False):
    check(specimen)
    admission(platform)
    fixture = attempt['fixture']
    if 'root_id' not in attempt:
        alive(attempt)
        result = journal.post(platform,attempt,'/api/v1/work-tasks/'+fixture['id']+'/runs',{
            'revision':fixture['revision'],'model_id':fixture['model_id'],'agent_id':fixture['agent_id'],
            'agent_version_id':fixture['agent_version_id'],'environment':'coding','step_limit':5,'token_limit':1000},'w07-submit-original')
        attempt['root_id'] = str(UUID(result['run_id']))
        journal.save()
    path = '/api/v1/work-runs/'+attempt['root_id']
    while True:
        if cancelled(): raise ValueError('Cancelled: no subsequent write/resume/rebuild permitted')
        alive(attempt)
        platform.attest()
        native = platform.get(path)
        if native['status'] == 'WAITING_APPROVAL':
            approve(platform,journal,attempt)
        elif native['status'] == 'NEEDS_REVIEW':
            break
        elif native['status'] in {'FAILED','CANCELLED','COMPLETED'}:
            raise ValueError('Expected original unknown write NEEDS_REVIEW, not a successful task')
        time.sleep(.1)
    before = platform.get(BASE+'/observations')
    validate(before,attempt)
    if 'w07_before' not in attempt:
        save_evidence(journal,attempt,'w07_before',before)
    alive(attempt)
    journal.post(platform,attempt,BASE+'/rebuild',{},'w07-rebuild-observer')
    after = platform.get(BASE+'/observations')
    validate(after,attempt)
    if after['facts'] != before['facts'] or after['recovery'] is None or after['recovery']['phase'] != 'OBSERVATION_RECOVERED':
        raise ValueError('Recovery changed unknown original facts or lacks real consumer rebuild proof')
    save_evidence(journal,attempt,'w07_after',after)
    attempt.update(task_outcome='PASS',business_root_status='NEEDS_REVIEW',unknown_write_preserved=True,
                   usage={'calls':1,'input_tokens':10,'output_tokens':5},usage_completeness='KNOWN')
    journal.save()
    return after


def validate(value,attempt):
    facts = value['facts']
    receiver = facts['receiver']
    if (facts['root_id'] != attempt['root_id'] or facts['root_status'] != 'NEEDS_REVIEW'
            or facts['operation_status'] != 'UNKNOWN' or receiver['write_attempts'] != 1 or receiver['write_count'] != 1
            or receiver['effects'] != [[1,'one']] or receiver['scope']['root_id'] != attempt['root_id']):
        raise ValueError('No replay/foreign-root/unknown rewrite can pass W07')


def cleanup(platform,journal,attempt,specimen):
    check(specimen)
    if 'w07_after' not in attempt:
        return 'NEEDS_REVIEW'
    retained = load(journal.directory,attempt['w07_after'],'w07-v2')
    if retained['binding'] != binding(journal.data,attempt):
        raise ValueError('Foreign original evidence')
    before = platform.get(BASE+'/observations')
    validate(before,attempt)
    if before != retained['body']:
        raise ValueError('Original unknown facts drifted; no cleanup action authorized')
    alive(attempt)
    journal.post(platform,attempt,BASE+'/close',{},'w07-close-receiver')
    after = platform.get(BASE+'/observations')
    validate(after,attempt)
    if not after['closed'] or after['facts'] != before['facts'] or after['recovery'] != before['recovery']:
        raise UnknownSubmission('Receiver close or original unknown facts not confirmed')
    save_evidence(journal,attempt,'w07_closed',after)
    return 'CLEAN_RECEIVER_UNKNOWN_OPERATION_RETAINED'
