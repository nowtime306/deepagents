"""Sequential isolated-target suite coordinator; no dataset-supplied executable code."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from evaluation_sources import source_fingerprint as inventory_fingerprint
from evaluation_profiles import identity as profile_identity
from evaluation_runner.contracts import Dataset, Target
from evaluation_runner.fixed_audit import validate_ready
from evaluation_runner.journal import digest
from evaluation_runner.fixed_case_deadline import Supervisor, ENV as DEADLINE_ENV, request_owned_stop


def check_candidate(candidate, manifest=None):
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,encoding='utf-8').strip()
    if candidate!=head:
        raise ValueError('Candidate does not match the actual checkout HEAD')
    if manifest is not None and json.loads(manifest.read_text(encoding='utf-8')).get('platform_commit')!=candidate:
        raise ValueError('Child target belongs to a different candidate')


def source_fingerprint(profile_version=1):
    return inventory_fingerprint(ROOT,profile_version=profile_version)


def describe_case(dataset, case, rules, profile_version=1):
    profile_identity(profile_version,case.case_id)
    result=subprocess.run([sys.executable,'-I','-m','evaluation_runner.fixed_run','describe','--case-id',case.case_id,
                           '--profile-version',str(profile_version)],
                          check=True,capture_output=True,text=True,encoding='utf-8',cwd=ROOT)
    value=json.loads(result.stdout)
    if (value.get('catalog_digest')!=digest(dataset.model_dump(mode='json'))
            or value.get('case_definition_digest')!=digest(case.model_dump(mode='json'))
            or value.get('case_id')!=case.case_id or value.get('rules_id')!=rules or value.get('implemented') is not True):
        raise ValueError('Parent input is not the implemented fixed catalog definition')
    return value


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


class Ledger:
    """Charge authorized upper bounds before dispatch; uncertainty never refunds them."""
    def __init__(self, calls, tokens, sandboxes, seconds):
        self.maximum = {'calls': calls, 'tokens': tokens, 'sandboxes': sandboxes, 'seconds': seconds}
        if any(type(v) is not int or v < 0 for v in self.maximum.values()) or seconds == 0:
            raise ValueError('Invalid parent budget')
        self.reserved = {'calls': 0, 'tokens': 0, 'sandboxes': 0}

    def reserve(self, case, bound, elapsed):
        proposed = {'calls': case.budgets.max_model_calls, 'tokens': case.budgets.max_tokens, 'sandboxes': bound}
        if type(bound) is not int or bound < 0:
            raise ValueError('A reviewed sandbox upper bound is required')
        if elapsed >= self.maximum['seconds'] or any(self.reserved[k] + n > self.maximum[k] for k, n in proposed.items()):
            return False
        for key, amount in proposed.items():
            self.reserved[key] += amount
        return True


def child_dataset(dataset, case, ordinal):
    row = case.model_dump()
    row['repeats'] = 1
    return {'schema_version': 1, 'dataset_id': f'{dataset.dataset_id}:{case.case_id}:r{ordinal}',
            'dataset_version': dataset.dataset_version, 'cases': [row]}


def selection(dataset, case_ids=None, repeat_count=None):
    if repeat_count is not None and (type(repeat_count) is not int or not 1<=repeat_count<=3):
        raise ValueError('Independent repeats must be between one and three')
    if case_ids and (len(case_ids)!=len(set(case_ids)) or not set(case_ids).issubset({c.case_id for c in dataset.cases})):
        raise ValueError('Only distinct registered catalog case IDs may be selected')
    chosen=[c for c in dataset.cases if not case_ids or c.case_id in case_ids]
    return [(case,ordinal) for case in chosen for ordinal in range(1,(repeat_count or case.repeats)+1)]


def command(action, *, state, admin=None, manager=None, dataset=None, output=None, parent_run=None,profile_version=1,case_id=None):
    """Every executable/module/script is fixed in code, never read from a case."""
    pwsh = 'pwsh'
    profile_identity(profile_version,case_id)
    if action in {'start', 'stop', 'target-cleanup'}:
        args = [pwsh, '-NoProfile', '-File', str(ROOT/'scripts/evaluation-target.ps1'), '-Action',
                'cleanup' if action == 'target-cleanup' else action, '-StateDirectory', str(state)]
        args += ['-ProfileVersion',str(profile_version)]
        if profile_version==2: args += ['-CaseId',case_id]
        if action == 'start' and parent_run is not None:
            args += ['-ParentRunId', str(parent_run)]
        if action in {'start', 'target-cleanup'}:
            args += ['-AdminConnectionFile', str(admin)]
        if action == 'start' and manager:
            args += ['-ManagerKeyFile', str(manager)]
        return args
    if action not in {'preflight', 'run', 'cleanup'}:
        if action != 'init':
            raise ValueError('Unregistered suite action')
    args = [sys.executable, '-I', '-m', 'evaluation_runner.fixed_run', action,
            '--target-manifest', str(state/'target-manifest.json'), '--output', str(output)]
    context = json.loads((dataset.parent/'suite-context.json').read_text(encoding='utf-8'))
    if context.get('profile_version',1)!=profile_version or case_id is not None and context['case_id']!=case_id:
        raise ValueError('Child context differs from selected profile')
    args += ['--profile-version',str(profile_version)]
    args += ['--case-id', context['case_id'], '--suite-context', str(dataset.parent/'suite-context.json')]
    return args


def observe_started(attempt, report):
    """Count accepted main/initialization roots separately from the client's main-stage flag."""
    from uuid import UUID

    roots, unknown = [], False
    for row in report.get('attempts', []):
        refs = [row.get('platform_root') or {}, *row.get('prior_run_refs', {}).values()]
        refs += row.get('versioned_roots',[])
        unknown |= row.get('versioned_submission_unknown') is True
        if any(item.get('state') in {'SUBMITTING','UNKNOWN'} for item in row.get('versioned_roots',[])):
            unknown=True
        refs += [{'id': root.get('run_id')} for root in row.get('flow_code_roots', {}).values()]
        fixture = row.get('fixture') or {}
        initial = [fixture, *fixture.get('versions', []), *fixture.get('children', [])]
        refs += [{'id': item.get('init_run_id')} for item in initial]
        for ref in refs:
            identifier = ref.get('id')
            if isinstance(identifier, str):
                try:
                    UUID(identifier)
                    roots.append(identifier)
                except ValueError:
                    pass
        intents = [*row.get('fixture_intents', {}).values(), *row.get('submission_intents', {}).values()]
        unknown |= any(intent.get('status') not in {'CONFIRMED', 'REJECTED'} for intent in intents)
    attempt.update(business_started=bool(roots), root_run_ids=sorted(set(roots)), unknown_submission=unknown)


def factual_failure(report):
    return any(fact.get('required',True) and fact.get('status')=='FAIL'
               for row in report.get('attempts',[]) for fact in row.get('fact_results',[]))


def bound_identity(data, state, attempt):
    """Bind locally valid evidence to this dispatched target, rather than another valid result."""
    target=Target.model_validate_json((state/'target-manifest.json').read_text(encoding='utf-8'))
    context=json.loads((state.parent/'suite-context.json').read_text(encoding='utf-8'))
    expected={'case_id':attempt['case_id'],'repeat_index':attempt['repeat_ordinal'],
              'candidate_commit':attempt['candidate_commit'],'case_definition_digest':attempt['case_digest']}
    if (any(context.get(key)!=value for key,value in expected.items())
            or data.get('environment_id')!=target.environment_id or data.get('owner_id')!=target.owner_id
            or data.get('target_digest')!=digest(target.model_dump()) or data.get('suite_context')!=context
            or data.get('case_definition_digest')!=context['case_definition_digest']
            or data.get('dataset_digest')!=context['catalog_digest']
            or context['candidate_commit']!=target.platform_commit):
        raise ValueError('Evidence belongs to a different target, case or repeat')


def verified_child_report(state,result,attempt):
    ready=validate_ready(result)
    bound_identity(ready,state,attempt)
    with (result/'report.json').open('rb') as stream:
        raw=stream.read(2*1024*1024+1)
    if len(raw)>2*1024*1024 or hashlib.sha256(raw).hexdigest()!=ready['files']['report.json']:
        raise ValueError('Report changed after its final receipt was verified')
    return json.loads(raw.decode('utf-8'))


def verify_frozen_source(record):
    try:
        if not record.get('source_sha256') or not record.get('candidate_commit'):
            raise ValueError('Missing candidate/source identity')
        check_candidate(record['candidate_commit'])
        if source_fingerprint(record.get('profile_version',1))!=record['source_sha256']:
            raise ValueError('Source content or file set changed')
    except Exception as error:
        record['source_verification']={'status':'CHANGED_OR_UNVERIFIED','error_type':type(error).__name__}
        record.setdefault('stopped_reason','CANDIDATE_OR_SOURCE_CHANGED')
        return False
    record['source_verification']={'status':'MATCHED'}
    return True


def finalize_target(attempt,state,result,call):
    """Always try owned cooperative teardown; never force-drop an uncertain target."""
    if not (state/'target-state.json').exists():
        attempt['cleanup']='NO_REGISTERED_TARGET'
        return
    if attempt.get("case_id") == "AET-F05" and attempt.get("f05_supervision"):
        request_owned_stop(attempt, state, result)
        return
    steps={}
    try:
        result.mkdir(exist_ok=True)
        (result/'cancel.request').touch()
        credentials=json.loads((state/'credentials.json').read_text(encoding='utf-8'))
        steps['formal_cleanup_exit']=call('cleanup',credentials)
    except Exception as error:
        steps['formal_cleanup_error']=type(error).__name__
    # Cleanup failure must never short-circuit the stop attempt.
    try:
        steps['stop_exit']=call('stop')
    except Exception as error:
        steps['stop_error']=type(error).__name__
    for _ in range(100):
        if (state/'stopped.json').exists():break
        time.sleep(0.1)
    if (steps.get('formal_cleanup_exit')==0 and steps.get('stop_exit')==0
            and (state/'stopped.json').exists() and not attempt.get('unknown_submission')):
        try:
            verified_child_report(state,result,attempt)
            steps['target_cleanup_exit']=call('target-cleanup')
        except Exception as error:
            steps['target_cleanup_error']=type(error).__name__
    attempt['teardown']=steps
    attempt['cleanup']='DATABASE_REMOVED_EVIDENCE_RETAINED' if steps.get('target_cleanup_exit')==0 else 'RETAINED'
    if attempt['cleanup']=='RETAINED' and attempt['status']=='PASS':attempt['status']='NEEDS_REVIEW'


def verify_parent_target(state, attempt, credentials):
    parent_file = state.parent.parent / 'platform-parent.json'
    expected_run = attempt.get('platform_parent_run')
    if not parent_file.exists():
        if expected_run is not None:
            raise ValueError('Required parent marker disappeared')
        return
    if expected_run is None:
        raise ValueError('Unexpected parent marker')
    import httpx
    parent = json.loads(parent_file.read_text(encoding='utf-8'))
    if parent.get('parent_run_id') != expected_run:
        raise ValueError('Parent run identity differs')
    target = Target.model_validate_json((state/'target-manifest.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url=target.api_base_url, headers={'Origin':target.api_base_url}, trust_env=False, timeout=5) as client:
        client.post('/api/v1/auth/login',json={'username':credentials['username'],'password':credentials['password']}).raise_for_status()
        response = client.get('/api/v1/agent-evaluations/profiles/capabilities')
        response.raise_for_status()
        proof = response.json().get('parent_effect_delegate')
    if (not isinstance(proof,dict) or proof.get('required') is not True or proof.get('ready') is not True
            or proof.get('contract') != 'platform-effect-permit-v1'
            or proof.get('parent_run_id') != parent['parent_run_id']
            or proof.get('candidate_commit') != parent['candidate_commit']
            or proof.get('case_id') != attempt['case_id']
            or set(proof.get('boundaries',{})) != {'provider','tool','sandbox','submission'}
            or any(value is not True for value in proof['boundaries'].values())):
        raise ValueError('Required parent effect bridge was not proved by the actual target')


def perform_attempt(attempt,state,result,call,candidate):
    reason=None
    report=None
    try:
        if call('start')!=0:return 'TARGET_START_REVIEW'
        check_candidate(candidate,state/'target-manifest.json')
        credentials=json.loads((state/'credentials.json').read_text(encoding='utf-8'))
        verify_parent_target(state,attempt,credentials)
        # Fixed preflight performs the genuine authenticated attestation against this manifest.
        for action in ('preflight','init'):
            if call(action,credentials)!=0:return action.upper()+'_REVIEW'
        code=call('run',credentials)
        report_path=result/'report.json'
        if report_path.is_file():
            try:
                report=verified_child_report(state,result,attempt)
                attempt['report_validation']={'status':'VERIFIED'}
                observe_started(attempt,report)
                attempt['status']=report.get('gate','NEEDS_REVIEW')
                if factual_failure(report):attempt['status']='FAIL'
                if attempt['status'] not in {'PASS','FAIL','NEEDS_REVIEW'}:attempt['status']='NEEDS_REVIEW'
                if attempt['status']=='PASS' and not attempt['business_started']:
                    attempt.update(status='NEEDS_REVIEW',reason='NO_CONFIRMED_BUSINESS_ROOT')
            except Exception as error:
                report=None
                attempt['report_validation']={'status':'REJECTED','error_type':type(error).__name__}
                attempt['status']='NEEDS_REVIEW'
        if code not in (0,1) or not report_path.is_file() or (attempt.get('profile_version',1)==2 and report is None):
            attempt['infrastructure_error']={'exit_code':code,'reason':'NO_VALID_PROCESS_RESULT'}
            if attempt['status']!='FAIL':attempt['status']='NEEDS_REVIEW'
        elif (code==0)!=((report is not None and report.get('control_gate')=='PASS')
                        if attempt.get('profile_version',1)==2 else attempt['status']=='PASS'):
            attempt['infrastructure_error']={'exit_code':code,'reason':'EXIT_REPORT_MISMATCH'}
            if attempt['status']!='FAIL':attempt['status']='NEEDS_REVIEW'
        return reason
    finally:
        # A crashed/blocked CLI can leave durable submission evidence without a final report.
        journal=result/'journal.json'
        if journal.is_file():
            try:
                stored=json.loads(journal.read_text(encoding='utf-8'))
                bound_identity(stored,state,attempt)
                observe_started(attempt,stored)
                if factual_failure(stored):attempt['status']='FAIL'
            except Exception:attempt['unknown_submission']=True
        finalize_target(attempt,state,result,call)


def invoke(args, directory, label, cancel_path, deadline, credentials=None, supervisor=None):
    allowed = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'}
    environment = {k:v for k,v in os.environ.items() if k.upper() in allowed}
    environment.update(DEEPEVAL_TELEMETRY_OPT_OUT='1', DEEPEVAL_DISABLE_DOTENV='1', PYTHON_DOTENV_DISABLED='1')
    if label == 'start' and '-ParentRunId' in args:
        for key in ('EVALUATION_PLATFORM_PERMIT_ORIGIN','EVALUATION_PLATFORM_PERMIT_SECRET'):
            if not os.environ.get(key):
                raise ValueError('Required parent delegation was not forwarded')
            environment[key] = os.environ[key]
    environment.update(DEEPEVAL_HOME=str(directory), ENV_DIR_PATH=str(directory),
                       DEEPEVAL_RESULTS_FOLDER=str(directory/'deepeval-results'), DEEPEVAL_CACHE_FOLDER=str(directory/'deepeval-cache'))
    if credentials:
        environment.update(EVALUATION_USERNAME=credentials['username'], EVALUATION_PASSWORD=credentials['password'])
    if supervisor is not None:
        refused = supervisor.before_spawn(label)
        if refused is not None:
            return refused
        environment[DEADLINE_ENV] = supervisor.checksum
    with (directory/(label+'-'+uuid4().hex+'.log')).open('wb') as log:
        process = subprocess.Popen(args, cwd=directory, env=environment, stdout=log, stderr=log,
                                   **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
        if supervisor is not None:
            return supervisor.wait(process, label, directory.parent/"cancel.request")
        while process.poll() is None:
            if (directory.parent/'cancel.request').exists() or time.monotonic() >= deadline:
                cancel_path.parent.mkdir(parents=True, exist_ok=True)
                cancel_path.touch()
            time.sleep(0.1)
        return process.returncode


def aggregate(directory, record):
    attempts = record['attempts']
    if not verify_frozen_source(record):
        for attempt in attempts:
            if attempt['status']=='PASS':attempt['status']='NEEDS_REVIEW'
    gate = 'FAIL' if any(a['status'] == 'FAIL' for a in attempts) else 'NEEDS_REVIEW'
    if len(attempts) == record['expected_attempts'] and attempts and all(a['status'] == 'PASS' for a in attempts):
        gate = 'PASS'
    if record.get('stopped_reason') and gate == 'PASS':
        gate = 'NEEDS_REVIEW'
    record['gate'] = gate
    record['completed_attempts'] = len(attempts)
    record['unique_cases_dispatched'] = len({a['case_id'] for a in attempts if a.get('dispatch_attempted')})
    record['unique_cases_business_started'] = len({a['case_id'] for a in attempts if a.get('business_started')})
    record['unknown_submission_attempts'] = sum(bool(a.get('unknown_submission')) for a in attempts)
    save(directory/'suite.json', record)
    tests = ET.Element('testsuite', name='isolated-agent-evaluation-suite', tests=str(record['expected_attempts']))
    failures = errors = 0
    rows=[]
    counts={'PASS':0,'FAIL':0,'NEEDS_REVIEW':0,'UNEXECUTED':0}
    for case_id, ordinal in record['planned']:
        item = next((a for a in attempts if a['case_id'] == case_id and a['repeat_ordinal'] == ordinal), None)
        node = ET.SubElement(tests, 'testcase', classname=case_id, name=f'repeat-{ordinal}')
        status = item['status'] if item else 'UNEXECUTED'
        counts[status]=counts.get(status,0)+1
        rows.append((case_id,ordinal,status,item))
        if status != 'PASS':
            ET.SubElement(node, 'failure' if status == 'FAIL' else 'error', message=status)
            failures += status == 'FAIL'
            errors += status != 'FAIL'
    tests.set('failures', str(failures)); tests.set('errors', str(errors))
    ET.ElementTree(tests).write(directory/'results.xml', encoding='utf-8', xml_declaration=True)
    labels={'PASS':'通过','FAIL':'失败','NEEDS_REVIEW':'待核验','UNEXECUTED':'未执行'}
    text=lambda value:str(value).replace('\r',' ').replace('\n',' ').replace('`','\\`')
    lines=['# 隔离 Agent 评测批次报告','',f"代码门禁：{labels.get(gate,gate)}（{gate}）。",
           '代码 PASS 不代表独立业务事实或出网验收已通过；原始证据完整性及独立核验另行判定。','',
           f"候选提交：`{text(record.get('candidate_commit','未记录'))}`。",
           f"启动 HEAD：`{text(record.get('head','未记录'))}`。",
           f"规则版本：`{text(record.get('rules_id','未记录'))}`；规则摘要：`{text(record.get('rules_digest','未记录'))}`。",
           f"原题集摘要：`{text(record.get('dataset_digest','未记录'))}`。",
           f"源码集合：{len(record.get('source_sha256',{}))} 文件；摘要 `{digest(record.get('source_sha256',{}))}`；核对 `{text(record['source_verification']['status'])}`。",'',
           f"计划执行 {record['expected_attempts']} 次；已有样本记录 {len(attempts)} 条；案例唯一数 {record.get('unique_case_count','未记录')}（独立重复不计新案例）。",
           f"通过 {counts['PASS']}；失败 {counts['FAIL']}；待核验 {counts['NEEDS_REVIEW']}；未执行 {counts['UNEXECUTED']}。",
           f"已尝试派发案例 {record['unique_cases_dispatched']}；平台已受理根运行的案例 {record['unique_cases_business_started']}；未知提交样本 {record['unknown_submission_attempts']}。",'']
    if record.get('stopped_reason'):lines.extend(['停止原因：`'+text(record['stopped_reason'])+'`。',''])
    for case_id,ordinal,status,item in rows:
        cleanup=item.get('cleanup','未记录') if item else '未执行'
        lines.append(f"- {case_id} / 独立重复 {ordinal}：{labels.get(status,status)}（{status}）；清理 `{text(cleanup)}`。")
        if item and item.get('child') and (directory/item['child']/'result/report.md').is_file():
            link=(Path(item['child'])/'result/report.md').as_posix()
            lines.append(f"  [子报告]({link})")
    (directory/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return 0 if gate == 'PASS' else 1


def run_suite(args):
    profile_version=getattr(args,'profile_version',1)
    if type(profile_version) is not int or profile_version not in {1,2}:
        raise ValueError('Unsupported suite profile version')
    if args.rules_id!=('controlled-a20-v1' if profile_version==1 else 'controlled-a20-v2'):
        raise ValueError('Rule/profile version mismatch')
    output = args.output.resolve()
    parent_run = getattr(args, 'platform_parent_run', None)
    if parent_run is not None:
        parent = json.loads((output/'platform-parent.json').read_text(encoding='utf-8'))
        if (str(UUID(str(parent_run))) != str(parent_run) or parent.get('parent_run_id') != str(parent_run)
                or parent.get('candidate_commit') != args.candidate_commit):
            raise ValueError('Required parent identity not bound')
    elif (output/'platform-parent.json').exists():
        raise ValueError('Unexpected parent marker')
    base = ROOT/'.local/agent-evaluations'
    if not output.is_relative_to(base) or output == base or (output/'suite.json').exists():
        raise ValueError('A new private suite output is required; no implicit replay')
    dataset = Dataset.model_validate_json(args.dataset.read_text(encoding='utf-8'))
    planned=selection(dataset,getattr(args,'case_id',None),getattr(args,'repeat_count',None))
    if profile_version==2:
        from evaluation_runner.profiles_v2.registry import selection as registered_selection
        for case,ordinal in planned:
            if case.model_dump()!=registered_selection(case.case_id,2)[0].model_dump():
                raise ValueError('Only exact separately registered v2 definitions are supported')
    # This trusted source contract is provided by the audited fixed profile, never by a dataset.
    contract_path = ROOT/('services/evaluation/profile_support/suite-capabilities.json' if profile_version==1 else 'services/evaluation/profile_support/suite-capabilities-v2.json')
    contract = json.loads(contract_path.read_text(encoding='utf-8'))
    if profile_version==2 and (contract.get('profile_version')!=2 or contract.get('rules_id')!='controlled-a20-v2'):
        raise ValueError('Versioned suite contract identity is not reviewed')
    initializer = ROOT/'services/evaluation/src/evaluation_runner/fixed_run.py'
    if not initializer.is_file() or contract.get('schema_version') != 1:
        raise ValueError('Reviewed fixed initializer/capability contract is not ready')
    if hashlib.sha256(initializer.read_bytes()).hexdigest() != contract['initializer_sha256']:
        raise ValueError('Fixed initializer changed after capability review')
    bounds = contract['sandbox_bounds']
    if any(case.case_id not in bounds for case,n in planned):
        raise ValueError('No reviewed sandbox upper bound for this case')
    ledger = Ledger(args.max_model_calls, args.max_tokens, args.max_sandboxes, args.max_seconds)
    if not re.fullmatch(r'[a-f0-9]{40}',args.candidate_commit):
        raise ValueError('Candidate commit must be a full SHA')
    check_candidate(args.candidate_commit)
    output.mkdir(parents=True, exist_ok=True)
    record = {'schema_version': 1, 'suite_id': str(uuid4()), 'dataset_digest': digest(dataset.model_dump(mode='json')), 'candidate_commit': args.candidate_commit,
              'profile_version':profile_version,'rules_id': args.rules_id, 'rules_digest':digest([{'case_id':c.case_id,'validators':[v.model_dump(mode='json') for v in c.validators],
                  'metrics':[v.model_dump(mode='json') for v in c.metrics],'approval_policy':c.approval_policy} for c in dataset.cases]),
              'profile_contract_sha256': hashlib.sha256(contract_path.read_bytes()).hexdigest(),
              'source_sha256':source_fingerprint(profile_version),
              'head':subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT,text=True).strip(),
              'catalog_unique_cases':len(dataset.cases),'unique_case_count':len({c.case_id for c,n in planned}), 'expected_attempts':len(planned),
              'planned':[(c.case_id,n) for c,n in planned],
              'max_live_targets':1, 'maximum':ledger.maximum, 'reserved':ledger.reserved, 'attempts':[],
              'cost':{'external_models':'NOT_EXECUTED','infrastructure':'NOT_PRICED','complete_total':None},
              'started_at':datetime.now(timezone.utc).isoformat()}
    lock = output/'suite.lock'
    with lock.open('x') as claim:
        claim.write(str(os.getpid()))
    started = time.monotonic()
    deadline = started + args.max_seconds
    try:
        for case,ordinal in planned:
                check_candidate(args.candidate_commit)
                if source_fingerprint(record.get('profile_version',1))!=record['source_sha256']:
                    record['stopped_reason']='SOURCE_CHANGED';return aggregate(output,record)
                if (output/'cancel.request').exists():
                    record['stopped_reason']='CANCELLED'; return aggregate(output,record)
                descriptor=describe_case(dataset,case,args.rules_id,profile_version)
                if descriptor.get('maximum_sandboxes')!=bounds[case.case_id]:
                    record['stopped_reason']='SANDBOX_BOUND_MISMATCH';return aggregate(output,record)
                if not ledger.reserve(case,bounds[case.case_id],time.monotonic()-started):
                    record['stopped_reason']='PARENT_BUDGET_EXHAUSTED';return aggregate(output,record)
                child = output/(case.case_id+'-r'+str(ordinal)+'-'+uuid4().hex[:8])
                child.mkdir()
                state, result = child/'target', child/'result'
                child_data = child/'dataset.json';save(child_data,child_dataset(dataset,case,ordinal))
                save(child/'suite-context.json', {'schema_version':1,'suite_id':record['suite_id'],
                     'catalog_digest':record['dataset_digest'],'case_definition_digest':digest(case.model_dump(mode='json')),
                     'repeat_index':ordinal,'case_id':case.case_id,'candidate_commit':args.candidate_commit,'rules_id':args.rules_id,
                     'profile_version':profile_version})
                attempt={'case_id':case.case_id,'repeat_ordinal':ordinal,'case_digest':digest(case.model_dump(mode='json')),
                         'profile_version':profile_version,
                         'candidate_commit':args.candidate_commit,'rules_id':args.rules_id,'status':'NEEDS_REVIEW',
                         'child':str(child.relative_to(output)), 'dispatch_attempted':False,
                         'business_started':False,'unknown_submission':False,'root_run_ids':[],'cleanup':'RETAINED'}
                if parent_run is not None:attempt['platform_parent_run'] = str(parent_run)
                record['attempts'].append(attempt);save(output/'suite.json',record)
                case_supervisor = None
                def call(action, auth=None):
                    nonlocal case_supervisor
                    if case.case_id == "AET-F05" and action in {"init", "run"}:
                        if case_supervisor is None:
                            if action != "init":
                                raise ValueError("Missing original F05 supervisor")
                            case_supervisor = Supervisor(child, state, result, attempt, deadline)
                        if action == "run":
                            attempt["dispatch_attempted"] = True
                        return invoke(command(action, state=state, admin=args.admin_connection_file, manager=args.manager_key_file,
                                              dataset=child_data, output=result, parent_run=parent_run), child, action,
                                      result/"cancel.request", deadline, auth, supervisor=case_supervisor)
                    if action in {'start','init','preflight','run'} and ((output/'cancel.request').exists() or time.monotonic()>=deadline):
                        return 130
                    if action=='run':attempt['dispatch_attempted']=True
                    return invoke(command(action,state=state,admin=args.admin_connection_file,manager=args.manager_key_file,
                                          dataset=child_data,output=result,parent_run=parent_run,profile_version=profile_version,case_id=case.case_id),child,action,result/'cancel.request',deadline,auth)
                reason=perform_attempt(attempt,state,result,call,args.candidate_commit)
                verified=verify_frozen_source(record)
                attempt['source_verification']=record['source_verification'].copy()
                if not verified and attempt['status']=='PASS':attempt['status']='NEEDS_REVIEW'
                if not verified or reason or attempt['cleanup']=='RETAINED':
                    record.setdefault('stopped_reason',reason or 'TARGET_RETAINED_CAPACITY_FULL');return aggregate(output,record)
                save(output/'suite.json',record)
        return aggregate(output,record)
    except KeyboardInterrupt:
        (output/'cancel.request').touch()
        if record['attempts']:
            current=output/record['attempts'][-1]['child']/'result'
            current.mkdir(exist_ok=True)
            (current/'cancel.request').touch()
        record['stopped_reason']='CANCELLED';aggregate(output,record)
        return 130
    except Exception as error:
        record['stopped_reason']='COORDINATOR_ERROR_'+type(error).__name__
        aggregate(output,record)
        return 3
    finally:
        lock.unlink()


def main():
    parser=argparse.ArgumentParser()
    for flag in ('dataset','output','admin-connection-file'):
        parser.add_argument('--'+flag,required=True,type=Path)
    parser.add_argument('--manager-key-file',type=Path)
    parser.add_argument('--case-id',action='append')
    parser.add_argument('--repeat-count',type=int)
    parser.add_argument('--profile-version',type=int,choices=[1,2],default=1)
    for flag in ('candidate-commit','rules-id'):
        parser.add_argument('--'+flag,required=True)
    for flag in ('max-seconds','max-model-calls','max-tokens','max-sandboxes'):
        parser.add_argument('--'+flag,required=True,type=int)
    try:
        return run_suite(parser.parse_args())
    except ValueError as error:
        print(json.dumps({'status':'NEEDS_REVIEW','error_type':type(error).__name__}),file=sys.stderr)
        return 2
    except Exception as error:
        print(json.dumps({'status':'NEEDS_REVIEW','error_type':type(error).__name__}),file=sys.stderr)
        return 3


if __name__=='__main__':
    raise SystemExit(main())
