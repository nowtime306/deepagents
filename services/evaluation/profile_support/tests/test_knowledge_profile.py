"""Private actual ProfileState/factory + substituted native meter tests; no DB/provider/index/Ask worker."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.resource_runtime import ResourceRun, active_resources
from evaluation_embeddings import install_embeddings
from evaluation_target import CASE_LIMITS, LIMITS, ProfileState, model_factory
from langchain_core.messages import HumanMessage, ToolMessage
from test_knowledge_protocol import sample

from evaluation_runner.knowledge_protocol import DOCUMENT


def scope(owner, mode, identifier=None):
    return ResourceRun(None, owner, mode, identifier or uuid4())


def reserve(state, current, role="lead", cost=15, *, finish=True, case="AET-A03"):
    token = active_resources.set(current)
    try:
        key = state.reserve(case, role, [HumanMessage(content="fixed")], cost=cost)
        if finish: state.complete(key)
        return key
    finally:
        active_resources.reset(token)


@pytest.mark.parametrize("case", ["AET-A03", "AET-A04"])
def test_resource_index_and_ask_embedding_chat_share_case_budget_after_reconstruction(tmp_path, case):
    owner = uuid4();index = scope(owner, "resource");ask = scope(owner, "ask")
    path = tmp_path / "provider.json";state = ProfileState(path)
    reserve(state,index,"embedding",7,case=case)
    state = ProfileState(path)
    for role,cost in [("lead",15),("embedding",7),("lead",15)]: reserve(state,ask,role,cost,case=case)
    summary = state.case_summary(owner,case)
    assert summary["calls"] == 4 and summary["tokens"] == 44 and summary["complete"]
    assert {(x["mode"],x["calls"],x["tokens"]) for x in summary["roots"]} == {("resource",1,7),("ask",3,37)}
    with pytest.raises(RuntimeError,match="budget"): reserve(state,scope(owner,"resource"),"embedding",7,case=case)
    assert state.case_summary(owner,case)["calls"] == 4


def test_six_calls_90_tokens_cannot_be_multiplied_by_a_second_root(tmp_path):
    owner = uuid4();state = ProfileState(tmp_path/"provider.json");a,b = scope(owner,"resource"),scope(owner,"ask")
    for current in (a,b,a,b,a,b): reserve(state,current)
    with pytest.raises(RuntimeError,match="budget"): reserve(state,b)
    assert state.case_summary(owner,"AET-A03")["calls"] == 6
    assert state.case_summary(owner,"AET-A03")["tokens"] == 90


def test_tokens_reject_before_new_root_or_reservation(tmp_path):
    state = ProfileState(tmp_path/"provider.json");owner = uuid4()
    reserve(state,scope(owner,"resource"),"embedding",85)
    with pytest.raises(RuntimeError,match="budget"): reserve(state,scope(owner,"ask"),"embedding",7)
    assert len(state.roots) == 1 and state.case_summary(owner,"AET-A03")["tokens"] == 85


@pytest.mark.parametrize("unknown", [False,True])
def test_pending_or_unknown_resource_root_poison_new_ask_after_reload(tmp_path,unknown):
    owner = uuid4();path = tmp_path/"provider.json";state = ProfileState(path)
    key = reserve(state,scope(owner,"resource"),"embedding",7,finish=False)
    if unknown: state.mark_unknown(key)
    rebuilt = ProfileState(path)
    with pytest.raises(RuntimeError,match="budget"): reserve(rebuilt,scope(owner,"ask"))
    assert rebuilt.case_summary(owner,"AET-A03")["tokens"] == 7
    assert not rebuilt.case_summary(owner,"AET-A03")["complete"]


def test_two_roots_and_many_threads_cannot_exceed_owner_case_cap():
    owner=uuid4();state=ProfileState();a,b=scope(owner,"resource"),scope(owner,"ask")
    def generate(n):
        try: reserve(state,a if n%2 else b);return True
        except RuntimeError: return False
    with ThreadPoolExecutor(max_workers=16) as pool: accepted=sum(pool.map(generate,range(64)))
    assert 1 <= accepted <= 6
    assert state.case_summary(owner,"AET-A03")["calls"] == accepted
    assert state.case_summary(owner,"AET-A03")["tokens"] <= 90 and len(state.roots) <= 2


@pytest.mark.parametrize("case", ["AET-A03","AET-A04"])
def test_actual_embedding_adapter_reserves_then_native_meter_reports_index_plus_ask(case):
    owner=uuid4();state=ProfileState();config=SimpleNamespace(id="embed",kind="embedding",protocol="openai",model=f"eval::{case}::v1")
    state.register_model(config.id,case,"embedding",owner)
    knowledge=SimpleNamespace(embedder=None,reranker=None);app=SimpleNamespace(state=SimpleNamespace(knowledge=knowledge));restore=install_embeddings(app,state)
    class Meter:
        started=0
        def __init__(self): self.usage=[]
        async def start(self,texts): self.started+=1;return (str(uuid4()),)
        async def finish(self,receipt,usage): self.usage.append(usage)
        async def error(self,receipt,error): raise AssertionError("Unexpected native error")
    meter=Meter()
    async def actual(current,text):
        token=active_resources.set(current)
        try:return await knowledge.embedder(config,[text],meter=meter)
        finally:active_resources.reset(token)
    index,ask=scope(owner,"resource"),scope(owner,"ask")
    try:
        assert len(asyncio.run(actual(index,DOCUMENT))[0]) == 4
        reserve(state,ask,case=case)
        assert len(asyncio.run(actual(ask,"星桥 保修" if case=="AET-A03" else "木星 核动力"))[0])==4
        reserve(state,ask,case=case)
        assert meter.started==2 and meter.usage==[{"prompt_tokens":7,"total_tokens":7}]*2
        assert state.case_summary(owner,case)["tokens"]==44
        with pytest.raises(RuntimeError,match="budget"):asyncio.run(actual(scope(owner,"resource"),DOCUMENT))
        assert meter.started==2
    finally:restore()


@pytest.mark.parametrize("case,populated",[("AET-A03",True),("AET-A04",False),("AET-A04",True)])
def test_actual_controlled_factory_final_uses_real_item_objects_without_changing_candidates(case,populated):
    state=ProfileState();owner=uuid4();current=scope(owner,"ask");config=SimpleNamespace(id="lead",model=f"eval::{case}::v1")
    state.register_model(config.id,case,"lead",owner);result,args,_binding=sample(case,populated);raw=json.dumps([result])
    token=active_resources.set(current)
    try:
        first=model_factory(config,state).invoke([HumanMessage(content="fixed")])
        assert first.tool_calls[0]["name"]=="search_knowledge" and first.tool_calls[0]["args"]==args
        second=model_factory(config,state).invoke([HumanMessage(content="fixed"),ToolMessage(content=raw,name="search_knowledge",tool_call_id=first.tool_calls[0]["id"])])
        body=json.loads(second.content)
        if case=="AET-A03": assert body["sources"][0]["chunk_id"]==result["items"][0]["chunk_id"] and body["sources"][0]["quote"]==DOCUMENT
        else:assert body=={"answer":"资料不足","sources":[]}
        assert json.dumps([result])==raw
    finally:active_resources.reset(token)


def test_new_private_case_map_preserves_existing_a01_w02_and_f06_zero():
    assert CASE_LIMITS["AET-A01"]=={"max_model_calls":8,"max_tokens":120,"max_roots":2}
    assert CASE_LIMITS["AET-W02"]==CASE_LIMITS["AET-A03"]==CASE_LIMITS["AET-A04"]=={"max_model_calls":6,"max_tokens":90,"max_roots":2}
    assert LIMITS["AET-F06"]==(0,0)


@pytest.mark.parametrize("case,mutation",[("AET-A03","no_source"),("AET-A03","keyword"),("AET-A04","warning")])
def test_actual_profile_does_not_answer_on_failed_or_unsupported_retrieval(case,mutation):
    owner=uuid4();state=ProfileState();current=scope(owner,"ask");config=SimpleNamespace(id="lead",model=f"eval::{case}::v1")
    state.register_model(config.id,case,"lead",owner);result,_args,_binding=sample(case,mutation!="no_source")
    if mutation=="keyword":result["mode"]="keyword"
    if mutation=="warning":result["warning"]="index unavailable"
    token=active_resources.set(current)
    try:
        with pytest.raises(ValueError):model_factory(config,state).invoke([HumanMessage(content="fixed"),ToolMessage(content=json.dumps([result]),name="search_knowledge",tool_call_id="actual-tool")])
    finally:active_resources.reset(token)
    with pytest.raises(RuntimeError,match="budget"):reserve(state,scope(owner,"resource"),"embedding",7,case=case)
    assert state.case_summary(owner,case)["complete"] is False


@pytest.mark.parametrize("mutation",["unsealed","wrong_kind","foreign_owner"])
def test_actual_embedding_rejects_before_meter_for_wrong_identity(mutation):
    owner=uuid4();state=ProfileState();config=SimpleNamespace(id="embed",kind="chat" if mutation=="wrong_kind" else "embedding",protocol="openai",model="eval::AET-A03::v1")
    if mutation!="unsealed":state.register_model(config.id,"AET-A03","embedding",uuid4() if mutation=="foreign_owner" else owner)
    app=SimpleNamespace(state=SimpleNamespace(knowledge=SimpleNamespace(embedder=None,reranker=None)))
    restore=install_embeddings(app,state)
    class Meter:
        started=0
        async def start(self,texts):self.started+=1;raise AssertionError("Must reject before meter")
    meter=Meter()
    async def query():
        token=active_resources.set(scope(owner,"resource"))
        try:await app.state.knowledge.embedder(config,[DOCUMENT],meter=meter)
        finally:active_resources.reset(token)
    try:
        with pytest.raises(ValueError):asyncio.run(query())
        assert meter.started==0 and not state.roots
    finally:restore()
