"""Offline coordinator engineering assertions; never starts a target or runs A20."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from uuid import uuid4

import pytest
from evaluation_runner.contracts import Dataset

spec = importlib.util.spec_from_file_location('evaluation_suite', Path(__file__).with_name('evaluation-suite.py'))
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


def dataset(repeats=1, payload='quoted $(unexpected-command)'):
    return Dataset.model_validate({'dataset_id':'engineering-only','dataset_version':1,'cases':[{
        'case_id':'AET-A02','name':'中文任务','mode':'ask','requirements':['AE-04'],'tags':['engineering'],
        'input':payload,'fixture_profile':{'id':'controlled-v1','version':1},'repeats':repeats,
        'validators':[{'registered_id':'output_equals','parameters':{'expected':{}}}],
        'budgets':{'max_model_calls':2,'max_tokens':30,'timeout_seconds':10}}]})


def test_parent_reservation_never_refunds_unknown_and_blocks_next_dispatch():
    case=dataset().cases[0]
    ledger=suite.Ledger(3,60,4,100)
    assert ledger.reserve(case,2,0)
    # Unknown outcome has no refund operation; next target cannot reset the parent call ceiling.
    assert not ledger.reserve(case,2,1)
    assert ledger.reserved=={'calls':2,'tokens':30,'sandboxes':2}


@pytest.mark.parametrize('limits',[(1,100,10,100),(10,29,10,100),(10,100,1,100),(10,100,10,0)])
def test_each_parent_budget_stops_before_allocation(limits):
    case=dataset().cases[0]
    if limits[-1]==0:
        with pytest.raises(ValueError):suite.Ledger(*limits)
    else:
        ledger=suite.Ledger(*limits)
        assert not ledger.reserve(case,2,0)
        assert not any(ledger.reserved.values())


def test_repeat_becomes_single_child_but_keeps_parent_definition():
    parent=dataset(repeats=3)
    original=parent.model_dump()
    child=suite.child_dataset(parent,parent.cases[0],2)
    assert child['cases'][0]['repeats']==1
    assert child['dataset_id'].endswith(':AET-A02:r2')
    assert parent.model_dump()==original and parent.cases[0].repeats==3


def test_selected_batch_and_repeat_override_do_not_change_catalog_identity():
    parent=dataset();before=suite.digest(parent.model_dump(mode='json'))
    planned=suite.selection(parent,['AET-A02'],3)
    assert len(planned)==3 and len({case.case_id for case,n in planned})==1
    assert suite.digest(parent.model_dump(mode='json'))==before
    with pytest.raises(ValueError):suite.selection(parent,['AET-A02','AET-A02'])
    with pytest.raises(ValueError):suite.selection(parent,['AET-W99'])


def test_case_payload_cannot_become_an_executable_or_command(tmp_path):
    data=dataset()
    assert 'unexpected-command' in data.cases[0].input
    command=suite.command('start',state=tmp_path,admin=tmp_path/'admin.txt')
    assert not any('unexpected-command' in value for value in command)
    assert command[0]=='pwsh' and '-File' in command
    with pytest.raises(ValueError):suite.command('arbitrary-code',state=tmp_path)


def test_partial_repeat_json_and_junit_preserve_fail_and_unexecuted(tmp_path):
    record={'attempts':[{'case_id':'AET-A02','repeat_ordinal':1,'status':'FAIL','business_started':True,'dispatch_attempted':True}],
            'expected_attempts':3,'planned':[('AET-A02',1),('AET-A02',2),('AET-A02',3)]}
    assert suite.aggregate(tmp_path,record)==1
    stored=json.loads((tmp_path/'suite.json').read_text(encoding='utf-8'))
    xml=ET.parse(tmp_path/'results.xml').getroot()
    assert stored['gate']=='FAIL' and stored['unique_cases_business_started']==1
    assert xml.get('tests')=='3' and xml.get('failures')=='1' and xml.get('errors')=='2'


def test_missing_reviewed_profile_contract_never_starts_a_target(tmp_path,monkeypatch):
    monkeypatch.setattr(suite,'ROOT',tmp_path)
    p=tmp_path/'dataset.json';p.write_text(dataset().model_dump_json(),encoding='utf-8')
    def forbidden(*args,**kwargs):raise AssertionError('No process is authorized before capability review')
    monkeypatch.setattr(suite,'invoke',forbidden)
    args=SimpleNamespace(output=tmp_path/'.local/agent-evaluations/suite',dataset=p)
    with pytest.raises(FileNotFoundError):suite.run_suite(args)
    assert not (args.output/'suite.json').exists()


def test_candidate_and_child_commit_must_match_checkout_before_mutation(tmp_path,monkeypatch):
    monkeypatch.setattr(suite.subprocess,'check_output',lambda *a,**k:'a'*40+'\n')
    with pytest.raises(ValueError):suite.check_candidate('b'*40)
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'platform_commit':'b'*40}),encoding='utf-8')
    with pytest.raises(ValueError):suite.check_candidate('a'*40,manifest)
    manifest.write_text(json.dumps({'platform_commit':'a'*40}),encoding='utf-8')
    suite.check_candidate('a'*40,manifest)


def test_source_inventory_detects_added_deleted_sql_and_profile_files(tmp_path,monkeypatch):
    monkeypatch.setattr(suite,'ROOT',tmp_path)
    migration=tmp_path/'services/api/migrations/001_test.sql';migration.parent.mkdir(parents=True)
    migration.write_text('SELECT 1',encoding='utf-8');first=suite.source_fingerprint()
    observer=tmp_path/'services/evaluation/profile_support/evaluation_observers.py';observer.parent.mkdir(parents=True)
    observer.write_text('OBSERVE=True',encoding='utf-8')
    second=suite.source_fingerprint();assert second!=first
    migration.write_text('SELECT 2',encoding='utf-8');assert suite.source_fingerprint()!=second
    migration.unlink();assert 'services/api/migrations/001_test.sql' not in suite.source_fingerprint()


def test_registered_stock_mismatch_blocks_changed_parent_input(monkeypatch):
    parent=dataset();case=parent.cases[0]
    described={'catalog_digest':suite.digest(parent.model_dump(mode='json')),
               'case_definition_digest':suite.digest(case.model_dump(mode='json')),
               'case_id':case.case_id,'rules_id':'controlled-a20-v1','implemented':True}
    monkeypatch.setattr(suite.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps(described)))
    suite.describe_case(parent,case,'controlled-a20-v1')
    changed=dataset(payload='a different actual input')
    with pytest.raises(ValueError):suite.describe_case(changed,changed.cases[0],'controlled-a20-v1')


def test_dispatch_missing_report_unknown_and_confirmed_start_are_distinct():
    attempt={'dispatch_attempted':True}
    suite.observe_started(attempt,{'attempts':[{'started':False}]})
    assert not attempt['business_started'] and not attempt['unknown_submission']
    suite.observe_started(attempt,{'attempts':[{'started':False,'submission_intents':{'submit':{'status':'UNKNOWN'}}}]})
    assert not attempt['business_started'] and attempt['unknown_submission']
    identifier=str(uuid4())
    suite.observe_started(attempt,{'attempts':[{'started':True,'platform_root':{'id':identifier}}]})
    assert attempt['business_started'] and attempt['root_run_ids']==[identifier]


@pytest.mark.parametrize('failure',['start','preflight','init','cleanup','cleanup_exception'])
def test_all_early_returns_attempt_cooperative_stop_and_retain_unknown(tmp_path,monkeypatch,failure):
    state,result=tmp_path/'target',tmp_path/'result';state.mkdir();result.mkdir()
    (state/'target-state.json').write_text('{}',encoding='utf-8')
    (state/'credentials.json').write_text(json.dumps({'username':'test','password':'test-only'}),encoding='utf-8')
    monkeypatch.setattr(suite,'check_candidate',lambda *args:None)
    calls=[]
    attempt={'status':'NEEDS_REVIEW','dispatch_attempted':False,'unknown_submission':False}
    def call(action,auth=None):
        calls.append(action)
        if action=='run':
            suite.save(result/'report.json',{'gate':'PASS','attempts':[{'started':True,'platform_root':{'id':str(uuid4())}}]})
        if action=='stop':suite.save(state/'stopped.json',{'environment_id':'test'})
        if action=='target-cleanup':raise AssertionError('Unknown cleanup must not drop any database')
        if action=='cleanup' and failure=='cleanup_exception':raise OSError('controlled local fixture failure')
        return 1 if action==failure or action=='cleanup' else 0
    suite.perform_attempt(attempt,state,result,call,'a'*40)
    assert 'cleanup' in calls and 'stop' in calls
    assert 'target-cleanup' not in calls and attempt['cleanup']=='RETAINED'


def test_cleanup_error_does_not_override_fact_failure(tmp_path):
    state,result=tmp_path/'target',tmp_path/'result';state.mkdir();result.mkdir()
    (state/'target-state.json').write_text('{}',encoding='utf-8')
    attempt={'status':'FAIL'}
    def call(action,auth=None):
        if action=='stop':suite.save(state/'stopped.json',{'environment_id':'test'})
        return 1
    suite.finalize_target(attempt,state,result,call)
    assert attempt['status']=='FAIL' and attempt['cleanup']=='RETAINED'


@pytest.mark.parametrize('change',['head','content','file-set'])
def test_final_aggregate_rechecks_candidate_and_complete_source_after_last_case(tmp_path,monkeypatch,change):
    def candidate(*args):
        if change=='head':raise ValueError('Checkout changed during final teardown')
    monkeypatch.setattr(suite,'check_candidate',candidate)
    current={'source.py':'new'} if change=='content' else {'source.py':'old','new.sql':'added'}
    monkeypatch.setattr(suite,'source_fingerprint',lambda:current)
    record={'candidate_commit':'a'*40,'source_sha256':{'source.py':'old'},
            'attempts':[{'case_id':'AET-A02','repeat_ordinal':1,'status':'PASS','business_started':True}],
            'expected_attempts':1,'planned':[('AET-A02',1)]}
    assert suite.aggregate(tmp_path,record)==1
    assert record['gate']=='NEEDS_REVIEW' and record['attempts'][0]['status']=='NEEDS_REVIEW'
    assert ET.parse(tmp_path/'results.xml').getroot().get('errors')=='1'


@pytest.mark.parametrize('code',[0,3,130])
def test_persisted_fact_fail_survives_infrastructure_or_mismatched_exit(tmp_path,monkeypatch,code):
    state,result=tmp_path/'target',tmp_path/'result';state.mkdir();result.mkdir()
    suite.save(state/'target-state.json',{})
    suite.save(state/'credentials.json',{'username':'test','password':'test-only'})
    monkeypatch.setattr(suite,'check_candidate',lambda *args:None)
    attempt={'status':'NEEDS_REVIEW','unknown_submission':False}
    def call(action,auth=None):
        if action=='run':
            suite.save(result/'report.json',{'gate':'FAIL','attempts':[{'started':True,
                'platform_root':{'id':str(uuid4())},'fact_results':[{'required':True,'status':'FAIL'}]}]})
            return code
        if action=='stop':suite.save(state/'stopped.json',{'environment_id':'test'})
        return 1 if action=='cleanup' else 0
    suite.perform_attempt(attempt,state,result,call,'a'*40)
    assert attempt['status']=='FAIL' and attempt['infrastructure_error']['exit_code']==code


def test_durable_fact_failure_in_journal_survives_missing_final_report(tmp_path,monkeypatch):
    state,result=tmp_path/'target',tmp_path/'result';state.mkdir();result.mkdir()
    suite.save(state/'target-state.json',{})
    suite.save(state/'credentials.json',{'username':'test','password':'test-only'})
    monkeypatch.setattr(suite,'check_candidate',lambda *args:None)
    attempt={'status':'NEEDS_REVIEW','unknown_submission':False}
    def call(action,auth=None):
        if action=='run':
            suite.save(result/'journal.json',{'attempts':[{'fact_results':[{'status':'FAIL','required':True}]}]})
            return 3
        if action=='stop':suite.save(state/'stopped.json',{'environment_id':'test'})
        return 1 if action=='cleanup' else 0
    suite.perform_attempt(attempt,state,result,call,'a'*40)
    assert attempt['status']=='FAIL' and attempt['infrastructure_error']['exit_code']==3


def test_parent_markdown_matches_json_junit_case_repeat_counts_and_identity(tmp_path,monkeypatch):
    monkeypatch.setattr(suite,'check_candidate',lambda *args:None)
    monkeypatch.setattr(suite,'source_fingerprint',lambda:{'source.py':'hash'})
    record={'candidate_commit':'a'*40,'head':'a'*40,'source_sha256':{'source.py':'hash'},
            'dataset_digest':'b'*64,'rules_id':'controlled-a20-v1','rules_digest':'c'*64,'unique_case_count':2,
            'expected_attempts':4,'planned':[(('AET-A02',n)) for n in range(1,4)]+[('AET-W01',1)],
            'attempts':[{'case_id':'AET-A02','repeat_ordinal':n,'status':status,
                         'dispatch_attempted':True,'business_started':n<3,'unknown_submission':n==3,
                         'cleanup':'RETAINED' if n==3 else 'CLEAN','private_prompt':'DO_NOT_RENDER'}
                        for n,status in [(1,'PASS'),(2,'FAIL'),(3,'NEEDS_REVIEW')]]}
    assert suite.aggregate(tmp_path,record)==1
    markdown=(tmp_path/'report.md').read_text(encoding='utf-8')
    stored=json.loads((tmp_path/'suite.json').read_text(encoding='utf-8'))
    xml=ET.parse(tmp_path/'results.xml').getroot()
    assert stored['gate']=='FAIL' and '代码门禁：失败（FAIL）' in markdown
    assert '通过 1；失败 1；待核验 1；未执行 1' in markdown
    assert xml.get('tests')=='4' and xml.get('failures')=='1' and xml.get('errors')=='2'
    assert all(f'AET-A02 / 独立重复 {n}' in markdown for n in range(1,4))
    assert 'AET-W01 / 独立重复 1：未执行' in markdown
    assert 'a'*40 in markdown and 'b'*64 in markdown and 'c'*64 in markdown
    assert suite.digest(record['source_sha256']) in markdown and 'MATCHED' in markdown
    assert '不代表独立业务事实或出网验收已通过' in markdown
    assert 'DO_NOT_RENDER' not in markdown


def test_parent_markdown_keeps_zero_execution_non_green(tmp_path,monkeypatch):
    monkeypatch.setattr(suite,'check_candidate',lambda *args:None)
    monkeypatch.setattr(suite,'source_fingerprint',lambda:{'source.py':'hash'})
    record={'candidate_commit':'a'*40,'source_sha256':{'source.py':'hash'},'unique_case_count':1,
            'expected_attempts':1,'planned':[('AET-A02',1)],'attempts':[]}
    assert suite.aggregate(tmp_path,record)==1
    markdown=(tmp_path/'report.md').read_text(encoding='utf-8')
    assert '代码门禁：待核验（NEEDS_REVIEW）' in markdown
    assert '通过 0；失败 0；待核验 0；未执行 1' in markdown
    assert ET.parse(tmp_path/'results.xml').getroot().get('errors')=='1'
