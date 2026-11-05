"""Reconstruct the latest native Work DeltaChannel state without executing the graph."""
from types import SimpleNamespace

import psycopg
from app.graphs import build_work
from app.work_backend import WorkBackend
from app.work_pause import WorkPauseMiddleware
from app.work_shared_budget_guard import WorkSharedBudgetGuard
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from pydantic import PrivateAttr

from profiles_v2.w08.w08_model import Model


class InspectionModel(Model):
    _effects: dict = PrivateAttr()

    def _generate(self,*args,**kwargs):
        self._effects['model']+=1
        raise RuntimeError('Checkpoint inspection cannot generate')


async def read_database(database_url,database,run):
    async with await psycopg.AsyncConnection.connect(database_url,autocommit=True,prepare_threshold=0,row_factory=dict_row,
            options='-c default_transaction_read_only=on') as connection:
        cursor=await connection.execute('SHOW transaction_read_only')
        if (await cursor.fetchone())['transaction_read_only']!='on':
            raise ValueError('Checkpoint reader requires an enforced read-only connection')
        messages,proof=await read(AsyncPostgresSaver(connection),database,run)
        proof['transaction_read_only']=True
        return messages,proof


async def read(saver,database,run):
    snapshot=run['agent_snapshot'] or {}
    if (run['environment']!='coding' or run['restore_snapshot_id'] or run['network_domains']
            or any(snapshot.get(key) for key in ('knowledge_ids','skill_ids','mcp_ids','workflow_version_ids','team_pins','web_enabled'))):
        raise ValueError('Original plain fixed W08 Work graph snapshot required')
    config={'configurable':{'thread_id':'work:'+str(run['id']),'checkpoint_ns':''}}
    latest=await saver.aget_tuple(config)
    if latest is None:
        raise ValueError('Actual latest original checkpoint missing')
    identifier=latest.checkpoint['id']
    pinned={'configurable':{**config['configurable'],'checkpoint_id':identifier}}
    if latest.config['configurable']!=pinned['configurable']:
        raise ValueError('Foreign latest checkpoint identity or namespace')
    effects={'model':0,'sdk':0,'tool':0}
    async def deny():
        effects['tool']+=1
        raise RuntimeError('Checkpoint inspection cannot run backend tools')
    async def deny_sdk(*args,**kwargs):
        effects['sdk']+=1
        raise RuntimeError('Checkpoint inspection cannot submit SDK commands')
    backend=WorkBackend(SimpleNamespace(id='read-only-inspector',commands=SimpleNamespace(run=deny_sdk)),deny)
    model=InspectionModel(case_id='AET-W08');model._root=str(run['id']);model._effects=effects
    graph=build_work(model,saver,backend=backend,
        middleware=[WorkPauseMiddleware(database,run),WorkSharedBudgetGuard(database,run)],instructions=run['instructions'])
    # aget_state reconstructs DeltaChannel snapshots+deltas. No invoke, stream,
    # update_state, model_factory, Service.bind, or sandbox connection is used.
    state=await graph.aget_state(pinned)
    if (state.config['configurable']!=pinned['configurable'] or state.next or state.interrupts
            or not isinstance(state.values,dict) or not state.values.get('messages') or any(effects.values())):
        raise ValueError('Original terminal latest graph state was not reconstructed read-only')
    after=await saver.aget_tuple(config)
    if after is None or after.checkpoint['id']!=identifier:
        raise ValueError('Latest checkpoint changed during read-only inspection')
    return state.values['messages'],{'checkpoint_id':identifier,'checkpoint_ns':'','effects':effects,
                                    'reconstruction':'native-work-graph-aget_state-delta-v1'}
