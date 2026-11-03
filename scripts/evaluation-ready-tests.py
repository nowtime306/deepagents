"""Evaluation-venv cleanup reader binding tests; no API/DB/model is started."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from evaluation_runner.contracts import Target
from evaluation_runner.journal import digest

spec=importlib.util.spec_from_file_location('cleanup_reader',Path(__file__).with_name('evaluation-ready.py'))
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def fixture(directory,monkeypatch):
    state=directory/'target';state.mkdir()
    result=directory/'result';result.mkdir()
    target=Target(environment_id=str(uuid4()),owner_id=str(uuid4()),platform_commit='a'*40,
                  api_base_url='http://127.0.0.1:8199',required_migrations=[],fixture_profile={'id':'controlled-v1','version':1})
    metadata={key:getattr(target,key) for key in ('environment_id','owner_id','platform_commit')}
    (state/'target-state.json').write_text(json.dumps(metadata),encoding='utf-8')
    (state/'target-manifest.json').write_text(target.model_dump_json(),encoding='utf-8')
    report={'gate':'FAIL','attempts':[{'cleanup_status':'CLEAN'}]}
    content=json.dumps(report).encode('utf-8');(result/'report.json').write_bytes(content)
    receipt={'environment_id':target.environment_id,'owner_id':target.owner_id,'target_digest':digest(target.model_dump()),
             'suite_context':{'candidate_commit':target.platform_commit},'files':{'report.json':hashlib.sha256(content).hexdigest()}}
    monkeypatch.setattr(reader,'validate_ready',lambda path:receipt)
    return state,result,receipt,report


def test_known_fail_with_complete_clean_receipt_can_be_reclaimed(tmp_path,monkeypatch):
    state,_,_,_=fixture(tmp_path,monkeypatch)
    reader.check(state)


@pytest.mark.parametrize('field',['owner_id','environment_id','target_digest','candidate_commit'])
def test_foreign_or_mismatched_ready_is_rejected(tmp_path,monkeypatch,field):
    state,_,receipt,_=fixture(tmp_path,monkeypatch)
    if field=='candidate_commit':receipt['suite_context'][field]='b'*40
    else:receipt[field]='different'
    with pytest.raises(ValueError):reader.check(state)


@pytest.mark.parametrize('mutation',['unknown','changed_bytes','oversized','manifest_owner'])
def test_unclean_or_changed_report_and_manifest_are_retained(tmp_path,monkeypatch,mutation):
    state,result,receipt,report=fixture(tmp_path,monkeypatch)
    if mutation=='manifest_owner':
        manifest=json.loads((state/'target-manifest.json').read_text(encoding='utf-8'));manifest['owner_id']=str(uuid4())
        (state/'target-manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    elif mutation=='oversized':(result/'report.json').write_bytes(b' '*131073)
    elif mutation=='unknown':
        report['attempts'][0]['cleanup_status']='NEEDS_REVIEW'
        content=json.dumps(report).encode();(result/'report.json').write_bytes(content)
        receipt['files']['report.json']=hashlib.sha256(content).hexdigest()
    else:(result/'report.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError):reader.check(state)


def test_main_installs_no_socket_policy_before_checking_the_receipt(tmp_path,monkeypatch):
    state,_,_,_=fixture(tmp_path,monkeypatch)
    code='''
import runpy,socket,sys
from evaluation_runner import fixed_audit
def denied(directory):
    socket.getaddrinfo('127.0.0.1',8199)
    raise AssertionError('Read-only receipt check allowed a socket destination')
fixed_audit.validate_ready=denied
sys.argv=[sys.argv[1],'--state',sys.argv[2]]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
    allowed={'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP'}
    environment={key:value for key,value in os.environ.items() if key.upper() in allowed}
    result=subprocess.run([sys.executable,'-I','-c',code,str(Path(reader.__file__).resolve()),str(state)],
                          env=environment,capture_output=True,text=True,encoding='utf-8',timeout=15,check=False)
    assert result.returncode==3
    assert json.loads(result.stdout)=={'status':'RETAINED','error_type':'PermissionError'}
