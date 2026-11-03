"""API-venv local target guards; no PostgreSQL, HTTP, worker or container is started."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

spec=importlib.util.spec_from_file_location('isolated_target',Path(__file__).with_name('evaluation-target.py'))
target=importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)


def state(directory):
    identifier=uuid4()
    value={'environment_id':str(identifier),'database_name':'owner_agent_evaluation_'+identifier.hex+'_test','pid':123}
    target.save(directory/'target-state.json',value)
    return value


def test_stop_receipt_cannot_authorize_another_environment(tmp_path):
    value=state(tmp_path)
    with pytest.raises(RuntimeError):target.cleanup_identity(tmp_path)
    target.save(tmp_path/'stopped.json',{'environment_id':str(uuid4()),'graceful':True})
    with pytest.raises(ValueError):target.cleanup_identity(tmp_path)
    target.save(tmp_path/'stopped.json',{'environment_id':value['environment_id'],'graceful':False,'startup_failed':True})
    assert target.cleanup_identity(tmp_path)[0]==value


def test_changed_state_database_is_rejected_without_connecting(tmp_path):
    value=state(tmp_path);value['database_name']='owner_agent'
    target.save(tmp_path/'target-state.json',value)
    with pytest.raises(ValueError):target.cleanup_identity(tmp_path)


@pytest.mark.parametrize('status',['PENDING','UNKNOWN'])
def test_pending_or_unknown_usage_blocks_cleanup_before_admin_drop(tmp_path,monkeypatch,status):
    value=state(tmp_path);namespace=str(uuid4());value['sandbox_namespace']=namespace
    target.save(tmp_path/'target-state.json',value)
    target.save(tmp_path/'stopped.json',{'environment_id':value['environment_id'],'graceful':True})
    monkeypatch.setattr(target,'admin_params',lambda path:{'dbname':'postgres'})
    queries=[]
    class Database:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query):
            text=query if isinstance(query,str) else query.as_string();queries.append(text)
            return self
        def fetchone(self):
            if 'work_sandbox_namespace' in queries[-1]:return (namespace,)
            return (1,) if 'model_usage' in queries[-1] else None
    connections=[]
    def connect(**kwargs):
        connections.append(kwargs)
        assert len(connections)==1,'Admin DROP connection must not be reached'
        return Database()
    monkeypatch.setattr(target.psycopg,'connect',connect)
    with pytest.raises(RuntimeError):target.cleanup(tmp_path,tmp_path/'private-admin.txt')
    assert 'model_usage' in queries[-1] and status in queries[-1]
    assert len(connections)==1 and not any('DROP' in query for query in queries)


@pytest.mark.parametrize('table',['task_runs','work_queue_runs','workflow_runs',None])
def test_any_business_history_including_initialization_requires_evidence(table):
    class Database:
        def execute(self,query):
            self.query=query.as_string();return self
        def fetchone(self):return (1,) if table and '"'+table+'"' in self.query else None
    assert bool(target.business_history(Database())) is (table is not None)


def test_missing_business_evidence_stops_before_admin_drop(tmp_path,monkeypatch):
    value=state(tmp_path);value['sandbox_namespace']=str(uuid4())
    target.save(tmp_path/'target-state.json',value)
    target.save(tmp_path/'stopped.json',{'environment_id':value['environment_id'],'graceful':True})
    monkeypatch.setattr(target,'admin_params',lambda path:{'dbname':'postgres'})
    monkeypatch.setattr(target,'pending_resources',lambda database:None)
    monkeypatch.setattr(target,'business_history',lambda database:True)
    def refuse(directory):raise RuntimeError('Missing ready receipt')
    monkeypatch.setattr(target,'confirm_cleanup_evidence',refuse)
    class Database:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):return self
        def fetchone(self):return (value['sandbox_namespace'],)
    connections=[]
    def connect(**kwargs):
        connections.append(kwargs)
        assert len(connections)==1,'Admin DROP must not be reached'
        return Database()
    monkeypatch.setattr(target.psycopg,'connect',connect)
    with pytest.raises(RuntimeError,match='Missing ready'):target.cleanup(tmp_path,tmp_path/'private-admin.txt')


def test_cleanup_validator_uses_fixed_command_timeout_and_no_credentials(tmp_path,monkeypatch):
    monkeypatch.setenv('EVALUATION_PASSWORD','secret-canary')
    monkeypatch.setenv('OPENAI_API_KEY','provider-canary')
    for key in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA'):
        monkeypatch.setenv(key,'inherited-user-home')
    def run(command,**kwargs):
        assert command==[str(target.ROOT/'services/evaluation/.venv/Scripts/python.exe'),'-I',
                         str(target.ROOT/'scripts/evaluation-ready.py'),'--state',str(tmp_path)]
        assert kwargs['timeout']==20 and kwargs['capture_output']
        assert 'EVALUATION_PASSWORD' not in kwargs['env'] and 'OPENAI_API_KEY' not in kwargs['env']
        assert kwargs['env']['DEEPEVAL_TELEMETRY_OPT_OUT']=='1'
        assert all(kwargs['env'][key]==str(tmp_path) for key in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA'))
        return type('Result',(),{'returncode':3})()
    monkeypatch.setattr(target.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='retained'):target.confirm_cleanup_evidence(tmp_path)


@pytest.mark.parametrize('has_history',[False,True])
def test_preflight_reclaims_without_ready_but_business_checks_ready_first(tmp_path,monkeypatch,has_history):
    value=state(tmp_path);value['sandbox_namespace']=str(uuid4())
    target.save(tmp_path/'target-state.json',value)
    target.save(tmp_path/'stopped.json',{'environment_id':value['environment_id'],'graceful':True})
    monkeypatch.setattr(target,'admin_params',lambda path:{'dbname':'postgres'})
    monkeypatch.setattr(target,'pending_resources',lambda database:None)
    monkeypatch.setattr(target,'business_history',lambda database:has_history)
    events=[]
    monkeypatch.setattr(target,'confirm_cleanup_evidence',lambda directory:events.append('ready'))
    async def empty(*args):events.append('namespace')
    monkeypatch.setattr(target,'confirm_namespace_empty',empty)
    class Database:
        def __init__(self,admin):self.admin=admin
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,*args):
            if 'DROP DATABASE' in (query if isinstance(query,str) else query.as_string()):events.append('drop')
            return self
        def fetchone(self):return (0,) if self.admin else (value['sandbox_namespace'],)
    monkeypatch.setattr(target.psycopg,'connect',lambda **kwargs:Database(kwargs['dbname']=='postgres'))
    target.cleanup(tmp_path,tmp_path/'private-admin.txt')
    assert events==(['ready'] if has_history else [])+['namespace','drop']
