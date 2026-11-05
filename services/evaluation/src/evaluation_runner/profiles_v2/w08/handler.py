"""Formal HTTP initialization, native root execution and actual latest artifact download."""
import hashlib
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from evaluation_runner.fixed_evidence import binding, load, store
from evaluation_runner.fixed_fixtures import FixedFixtures
from evaluation_runner.profiles_v2.w08.contract import BASE, PROMPT, check_anchor
from evaluation_runner.profiles_v2.w08.validation import validate


def check(specimen):
    if (specimen.case_id!='AET-W08' or specimen.mode!='work' or specimen.input!=PROMPT
            or specimen.fixture_profile.id!='workspace-boundary' or specimen.fixture_profile.version!=2
            or specimen.fixture_parameters!={'protocol':'physical-symlink-latest-export-v2'}
            or specimen.approval_policy!='none' or specimen.repeats!=1 or specimen.metrics
            or specimen.budgets.model_dump()!={'timeout_seconds':300,'max_model_calls':6,'max_tokens':1000}
            or [row.model_dump() for row in specimen.validators]!=[{'registered_id':'export_boundaries','version':1,'required':True,'parameters':{}}]):
        raise ValueError('Original separate W08 physical boundary v2 definition required')


def admission(platform):
    proof = platform.attest()
    expected = {'case_id':'AET-W08','definition_version':2,'max_roots':1,'max_model_calls':6,'max_tokens':90,
                'max_sandboxes':1,'physical_symlink':True,'healthy':True}
    if platform.get(BASE+'/capabilities')!=expected:
        raise ValueError('Original W08 bounded target required')
    platform.expected_sandbox_namespace = str(UUID(proof['sandbox_namespace']))
    platform.attest()
    return proof


def alive(attempt):
    if datetime.now(UTC)>=datetime.fromisoformat(attempt['w08_deadline']):
        raise ValueError('Original full-case deadline expired')


def initialize(platform,journal,attempt,specimen):
    check(specimen)
    if 'w08_deadline' not in attempt:
        start=datetime.now(UTC)
        attempt['w08_started_at']=start.isoformat()
        attempt['w08_deadline']=(start+timedelta(seconds=300)).isoformat()
        journal.save()
    alive(attempt)
    check_anchor(attempt['w08_started_at'],attempt['w08_deadline'])
    proof = admission(platform)
    alive(attempt)
    scope={key:str(proof[key]) for key in ('environment_id','owner_id','sandbox_namespace')}
    if 'w08_scope' in attempt and attempt['w08_scope']!=scope:
        raise ValueError('Original W08 target identity changed')
    attempt['w08_scope']=scope
    journal.save()
    fixtures = FixedFixtures(platform,journal,attempt)
    model = fixtures.create('w08-model','/api/v1/admin/models',{'name':'AE W08 v2 '+fixtures.prefix,
        'model':'eval::AET-W08::v2','base_url':platform.target.api_base_url+'/controlled-model','kind':'chat',
        'provider':'custom','protocol':'openai','enabled':True,'supports_tools':True})
    anchor={'model_id':model,'started_at':attempt['w08_started_at'],'deadline':attempt['w08_deadline']}
    alive(attempt)
    fixtures.mutate('w08-seal','POST',BASE+'/model-binding',body=anchor)
    if platform.get(BASE+'/binding')!=anchor:
        raise ValueError('Handler and actual service initialization anchors differ')
    agent = fixtures.published_agent('AET-W08',model)
    task = fixtures.work_task(PROMPT,{})
    attempt['fixture']={**task,**agent,'model_id':model}
    journal.save()
    return attempt['fixture']


def evidence(journal,attempt,value):
    reference = store(journal.directory,'w08-v2',{'schema_version':1,'kind':'w08-v2','binding':binding(journal.data,attempt),'body':value})
    attempt.setdefault('evidence_refs',[]).append(reference)
    attempt['w08_observed']=reference
    journal.save()


def observe(platform,attempt):
    facts = platform.get(BASE+'/observations')
    if facts['initialization_anchor']!={'model_id':attempt['fixture']['model_id'],'started_at':attempt['w08_started_at'],'deadline':attempt['w08_deadline']}:
        raise ValueError('Actual observed initialization deadline differs from original persisted anchor')
    root = attempt['root_id']
    rows = platform.get('/api/v1/work-runs/'+root+'/artifacts')['items']
    downloaded = {}
    for row in rows:
        if row['path'] in downloaded or row['size']>1024:
            raise ValueError('Duplicate/oversized latest artifact')
        response = platform.http.get('/api/v1/work-artifacts/'+row['id']+'/download')
        response.raise_for_status()
        content = response.content
        if len(content)!=row['size'] or hashlib.sha256(content).hexdigest()!=row['digest']:
            raise ValueError('Downloaded latest artifact bytes differ from native digest')
        downloaded[row['path']]={'size':len(content),'sha256':hashlib.sha256(content).hexdigest(),'download_verified':True}
    facts['downloaded_latest_artifacts']=downloaded
    scope = attempt['w08_scope']
    validate(facts,environment_id=scope['environment_id'],owner_id=scope['owner_id'],root_id=root,namespace=scope['sandbox_namespace'])
    if facts['released'] is not True or facts['root_status']!='COMPLETED':
        raise ValueError('Completed original root and native sandbox release required')
    return facts


def execute(platform,journal,attempt,specimen,*,cancelled=lambda:False):
    check(specimen)
    admission(platform)
    fixture = attempt['fixture']
    if 'root_id' not in attempt:
        alive(attempt)
        result = journal.post(platform,attempt,'/api/v1/work-tasks/'+fixture['id']+'/runs',{
            'revision':fixture['revision'],'model_id':fixture['model_id'],'agent_id':fixture['agent_id'],
            'agent_version_id':fixture['agent_version_id'],'environment':'coding','step_limit':6,'token_limit':1000},'w08-submit-original')
        attempt['root_id']=str(UUID(result['run_id']))
        journal.save()
    while True:
        alive(attempt)
        if cancelled(): raise ValueError('Cancelled: no further W08 effects permitted')
        platform.attest()
        native = platform.get('/api/v1/work-runs/'+attempt['root_id'])
        if native['status']=='COMPLETED': break
        if native['status'] in {'FAILED','CANCELLED','NEEDS_REVIEW','WAITING_APPROVAL','PAUSED'}:
            raise ValueError('Fixed W08 root did not complete without approval/recovery')
        time.sleep(.1)
    value = observe(platform,attempt)
    evidence(journal,attempt,value)
    attempt.update(task_outcome='PASS',usage={'calls':6,'input_tokens':60,'output_tokens':30},usage_completeness='KNOWN')
    journal.save()
    return value


def cleanup(platform,journal,attempt,specimen):
    check(specimen)
    if 'w08_observed' not in attempt: return 'NEEDS_REVIEW'
    retained = load(journal.directory,attempt['w08_observed'],'w08-v2')
    if retained['binding']!=binding(journal.data,attempt) or observe(platform,attempt)!=retained['body']:
        raise ValueError('Original released W08 facts drifted')
    return 'CLEAN_NATIVE_RELEASE_LATEST_EXPORT_VERIFIED'
