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
