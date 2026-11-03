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
from uuid import uuid4
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from evaluation_sources import source_fingerprint as inventory_fingerprint
from evaluation_runner.contracts import Dataset
from evaluation_runner.journal import digest


def check_candidate(candidate, manifest=None):
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,encoding='utf-8').strip()
    if candidate!=head:
        raise ValueError('Candidate does not match the actual checkout HEAD')
    if manifest is not None and json.loads(manifest.read_text(encoding='utf-8')).get('platform_commit')!=candidate:
        raise ValueError('Child target belongs to a different candidate')


def source_fingerprint():
    return inventory_fingerprint(ROOT)


def describe_case(dataset, case, rules):
    result=subprocess.run([sys.executable,'-I','-m','evaluation_runner.fixed_run','describe','--case-id',case.case_id],
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


def command(action, *, state, admin=None, manager=None, dataset=None, output=None):
    """Every executable/module/script is fixed in code, never read from a case."""
    pwsh = 'pwsh'
    if action in {'start', 'stop', 'target-cleanup'}:
        args = [pwsh, '-NoProfile', '-File', str(ROOT/'scripts/evaluation-target.ps1'), '-Action',
                'cleanup' if action == 'target-cleanup' else action, '-StateDirectory', str(state)]
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
    args += ['--case-id', context['case_id'], '--suite-context', str(dataset.parent/'suite-context.json')]
    return args


def observe_started(attempt, report):
    rows=report.get('attempts',[])
    roots=[]
    unknown=False
    for row in rows:
        root=row.get('platform_root') or {}
        if row.get('started') is True and isinstance(root.get('id'),str):
            try:
                from uuid import UUID
                UUID(root['id'])
                roots.append(root['id'])
            except ValueError:
                pass
        unknown |= any(intent.get('status') not in {'CONFIRMED','REJECTED'}
                       for intent in row.get('submission_intents',{}).values())
    attempt.update(business_started=bool(roots),root_run_ids=sorted(set(roots)),unknown_submission=unknown)


def factual_failure(report):
    return any(fact.get('required',True) and fact.get('status')=='FAIL'
               for row in report.get('attempts',[]) for fact in row.get('fact_results',[]))


def verify_frozen_source(record):
    try:
        if not record.get('source_sha256') or not record.get('candidate_commit'):
            raise ValueError('Missing candidate/source identity')
        check_candidate(record['candidate_commit'])
        if source_fingerprint()!=record['source_sha256']:
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
            steps['target_cleanup_exit']=call('target-cleanup')
        except Exception as error:
            steps['target_cleanup_error']=type(error).__name__
    attempt['teardown']=steps
    attempt['cleanup']='DATABASE_REMOVED_EVIDENCE_RETAINED' if steps.get('target_cleanup_exit')==0 else 'RETAINED'
    if attempt['cleanup']=='RETAINED' and attempt['status']=='PASS':attempt['status']='NEEDS_REVIEW'


def perform_attempt(attempt,state,result,call,candidate):
    reason=None
    try:
        if call('start')!=0:return 'TARGET_START_REVIEW'
        check_candidate(candidate,state/'target-manifest.json')
        credentials=json.loads((state/'credentials.json').read_text(encoding='utf-8'))
        # Fixed preflight performs the genuine authenticated attestation against this manifest.
        for action in ('preflight','init'):
            if call(action,credentials)!=0:return action.upper()+'_REVIEW'
        code=call('run',credentials)
        report_path=result/'report.json'
        if report_path.is_file():
            report=json.loads(report_path.read_text(encoding='utf-8'))
            observe_started(attempt,report)
            attempt['status']=report.get('gate','NEEDS_REVIEW')
            if factual_failure(report):attempt['status']='FAIL'
            if attempt['status'] not in {'PASS','FAIL','NEEDS_REVIEW'}:attempt['status']='NEEDS_REVIEW'
            if attempt['status']=='PASS' and not attempt['business_started']:
                attempt.update(status='NEEDS_REVIEW',reason='NO_CONFIRMED_BUSINESS_ROOT')
        if code not in (0,1) or not report_path.is_file():
            attempt['infrastructure_error']={'exit_code':code,'reason':'NO_VALID_PROCESS_RESULT'}
            if attempt['status']!='FAIL':attempt['status']='NEEDS_REVIEW'
        elif (code==0)!=(attempt['status']=='PASS'):
            attempt['infrastructure_error']={'exit_code':code,'reason':'EXIT_REPORT_MISMATCH'}
            if attempt['status']!='FAIL':attempt['status']='NEEDS_REVIEW'
        return reason
    finally:
        # A crashed/blocked CLI can leave durable submission evidence without a final report.
        journal=result/'journal.json'
        if journal.is_file():
            try:
                stored=json.loads(journal.read_text(encoding='utf-8'))
                observe_started(attempt,stored)
                if factual_failure(stored):attempt['status']='FAIL'
            except Exception:attempt['unknown_submission']=True
        finalize_target(attempt,state,result,call)


def invoke(args, directory, label, cancel_path, deadline, credentials=None):
    allowed = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'}
    environment = {k:v for k,v in os.environ.items() if k.upper() in allowed}
    environment.update(DEEPEVAL_TELEMETRY_OPT_OUT='1', DEEPEVAL_DISABLE_DOTENV='1', PYTHON_DOTENV_DISABLED='1')
    environment.update(DEEPEVAL_HOME=str(directory), ENV_DIR_PATH=str(directory),
                       DEEPEVAL_RESULTS_FOLDER=str(directory/'deepeval-results'), DEEPEVAL_CACHE_FOLDER=str(directory/'deepeval-cache'))
    if credentials:
        environment.update(EVALUATION_USERNAME=credentials['username'], EVALUATION_PASSWORD=credentials['password'])
    with (directory/(label+'-'+uuid4().hex+'.log')).open('wb') as log:
        process = subprocess.Popen(args, cwd=directory, env=environment, stdout=log, stderr=log,
                                   **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
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
    for case_id, ordinal in record['planned']:
        item = next((a for a in attempts if a['case_id'] == case_id and a['repeat_ordinal'] == ordinal), None)
        node = ET.SubElement(tests, 'testcase', classname=case_id, name=f'repeat-{ordinal}')
        status = item['status'] if item else 'UNEXECUTED'
        if status != 'PASS':
            ET.SubElement(node, 'failure' if status == 'FAIL' else 'error', message=status)
            failures += status == 'FAIL'
            errors += status != 'FAIL'
    tests.set('failures', str(failures)); tests.set('errors', str(errors))
    ET.ElementTree(tests).write(directory/'results.xml', encoding='utf-8', xml_declaration=True)
    return 0 if gate == 'PASS' else 1


def run_suite(args):
    output = args.output.resolve()
    base = ROOT/'.local/agent-evaluations'
    if not output.is_relative_to(base) or output == base or (output/'suite.json').exists():
        raise ValueError('A new private suite output is required; no implicit replay')
    dataset = Dataset.model_validate_json(args.dataset.read_text(encoding='utf-8'))
    planned=selection(dataset,getattr(args,'case_id',None),getattr(args,'repeat_count',None))
    # This trusted source contract is provided by the audited fixed profile, never by a dataset.
    contract_path = ROOT/'services/evaluation/profile_support/suite-capabilities.json'
    contract = json.loads(contract_path.read_text(encoding='utf-8'))
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
              'rules_id': args.rules_id, 'rules_digest':digest([{'case_id':c.case_id,'validators':[v.model_dump(mode='json') for v in c.validators],
                  'metrics':[v.model_dump(mode='json') for v in c.metrics],'approval_policy':c.approval_policy} for c in dataset.cases]),
              'profile_contract_sha256': hashlib.sha256(contract_path.read_bytes()).hexdigest(),
              'source_sha256':source_fingerprint(),
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
                if source_fingerprint()!=record['source_sha256']:
                    record['stopped_reason']='SOURCE_CHANGED';return aggregate(output,record)
                if (output/'cancel.request').exists():
                    record['stopped_reason']='CANCELLED'; return aggregate(output,record)
                descriptor=describe_case(dataset,case,args.rules_id)
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
                     'repeat_index':ordinal,'case_id':case.case_id,'candidate_commit':args.candidate_commit,'rules_id':args.rules_id})
                attempt={'case_id':case.case_id,'repeat_ordinal':ordinal,'case_digest':digest(case.model_dump(mode='json')),
                         'candidate_commit':args.candidate_commit,'rules_id':args.rules_id,'status':'NEEDS_REVIEW',
                         'child':str(child.relative_to(output)), 'dispatch_attempted':False,
                         'business_started':False,'unknown_submission':False,'root_run_ids':[],'cleanup':'RETAINED'}
                record['attempts'].append(attempt);save(output/'suite.json',record)
                def call(action, auth=None):
                    if action in {'start','init','preflight','run'} and ((output/'cancel.request').exists() or time.monotonic()>=deadline):
                        return 130
                    if action=='run':attempt['dispatch_attempted']=True
                    return invoke(command(action,state=state,admin=args.admin_connection_file,manager=args.manager_key_file,
                                          dataset=child_data,output=result),child,action,result/'cancel.request',deadline,auth)
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
