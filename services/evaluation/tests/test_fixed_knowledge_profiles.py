"""Private full CLI + MockTransport. No HTTP socket, DB, target, index or Qdrant executes."""
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import httpx
import pytest
from test_core import attestation, target

from evaluation_runner import adapters, fixed_audit, fixed_evidence, fixed_run
from evaluation_runner import fixed_knowledge_evidence as module
from evaluation_runner.client import Platform
from evaluation_runner.journal import atomic_json
from evaluation_runner.knowledge_protocol import DOCUMENT

ROOT=Path(__file__).resolve().parents[3]


def usage(n,inputs,outputs):
    return {"calls":n,"reported_calls":n,"unknown_calls":0,"pending_calls":0,"input_tokens":inputs,"output_tokens":outputs}


def harness(tmp_path,monkeypatch,case="AET-A03",*,populated=True,lost=None,degraded=False,bad_final=False):
    ids={name:str(uuid4()) for name in ("lead","embedding","kb","document","chunk","index","agent","version","conversation","ask","namespace")}
    value=target().model_copy(update={"resource_bindings":{case:ids["lead"]}})
    proof=attestation(value);proof.update(sandbox_namespace=ids["namespace"],permissions=["evaluation.read","evaluation.manage","evaluation.run","agents.manage","chat.read","chat.manage","chat.run","knowledge.read","knowledge.manage","knowledge.search"])
    proof["execution_limits"]["profiles"][case]={"mode":"ask","max_model_calls":6,"max_tokens":90}
    caps={"observers":["root-model-context-v1","root-model-context-resource-v1"],"case_limits":{"schema_version":1,"enforcement":"controlled-case-reserve-v1","scope":"evaluation-owner-and-case","profiles":{case:{"max_model_calls":6,"max_tokens":90,"max_roots":2}}}}
    specimen=next(c for c in fixed_run.catalog().cases if c.case_id==case)
    output=tmp_path/"output";posts=[];state={"uploaded":False,"submitted":False}
    stamp="2026-11-04T03:00:00+00:00";tool_id="ae-1111111111111111"
    def trace(mode,run):
        models=[("embedding",1)] if mode=="resource" else [("lead",2),("embedding",1)]
        items=[]
        for role,count in models:
            calls=[{"call_id":str(uuid5(UUID(run),role+str(i))),"run_id":run,"mode":mode,"model_id":ids[role],"model_revision":1,"status":"KNOWN","input_tokens":7 if role=="embedding" else 10,"output_tokens":0 if role=="embedding" else 5} for i in range(count)]
            items.append({"resource_kind":"model","resource_id":ids[role],"snapshot_id":str(uuid5(UUID(run),role)),"revision":"1","used_at":stamp,"snapshot":{},"calls":calls})
        items.append({"resource_kind":"knowledge","resource_id":ids["kb"],"snapshot_id":str(uuid5(UUID(run),"kb")),"revision":"1","used_at":stamp,"snapshot":{"embedding_model_id":ids["embedding"],"rerank_model_id":None},"calls":[]})
        return {"mode":mode,"run_id":run,"status":"SUCCEEDED","configuration_scope":{"mode":mode,"run_id":run},"business_version":{"published_version_id":ids["version"],"published_version":1} if mode=="ask" else {},"items":items}
    def result():
        item={"document_id":ids["document"],"chunk_id":ids["chunk"],"document_revision":1,"text":DOCUMENT,"name":"warranty.txt","location":"section 1","score":0.03,"keyword_score":1.0,"keyword_coverage":1.0,"vector_score":0.7}
        return {"operation_id":None,"mode":"keyword" if degraded else "hybrid","warning":"vector unavailable" if degraded else None,"reranked":False,"evidence_status":"candidates" if populated else "no_evidence","guidance":"核对来源","keyword_candidates":int(populated),"keyword_filtered":0,"keyword_min_coverage":0.2,"score_type":"rrf","items":[item] if populated else []}
    def messages():
        args={"query":"星桥 保修" if case=="AET-A03" else "木星 核动力"}
        final={"answer":"资料不足","sources":[]} if case=="AET-A04" else {"answer":"星桥产品保修三年","sources":[{"document_id":ids["document"],"chunk_id":ids["chunk"],"document_revision":1,"quote":DOCUMENT}]}
        if bad_final:final["sources"][0]["quote"]="星桥产品保修99年"
        rows=[{"role":"user","run_id":ids["ask"],"content":{"text":specimen.input}},
              {"role":"assistant","run_id":ids["ask"],"content":{"data":{"content":"","tool_calls":[{"id":tool_id,"name":"search_knowledge","args":args}]}}},
              {"role":"tool","run_id":ids["ask"],"content":{"data":{"content":json.dumps([result()],ensure_ascii=False),"name":"search_knowledge","tool_call_id":tool_id,"status":"success"}}},
              {"role":"assistant","run_id":ids["ask"],"content":{"data":{"content":json.dumps(final,ensure_ascii=False),"tool_calls":[]}}}]
        return {"items":rows,"next_before":None}
    def transport(request):
        path,method=request.url.path,request.method
        if path.endswith("/environment"):return httpx.Response(200,json=proof)
        if path.endswith("/auth/me"):return httpx.Response(200,json={"is_admin":True})
        if path.endswith("/profiles/capabilities"):return httpx.Response(200,json=caps)
        if method=="POST":
            posts.append(path)
            if path.endswith("/documents"):
                assert DOCUMENT.encode() in request.content and b'warranty.txt' in request.content
                assert state["uploaded"] is False;state["uploaded"]=True
                if lost=="upload":raise httpx.ReadError("Controlled lost upload reply")
                return httpx.Response(201,json={"id":ids["document"],"revision":1,"chunks":1,"index":{"operation_id":ids["index"],"status":"SUCCEEDED"}})
            body=json.loads(request.content)
            if path=="/api/v1/admin/models":
                role="embedding" if body["kind"]=="embedding" else "lead";assert body["model"]==f"eval::{case}::v1"
                return httpx.Response(201,json={"id":ids[role]})
            if path.endswith("/model-bindings"):
                assert body=={"model_id":ids[body["role"]],"role":body["role"],"case_id":case}
                return httpx.Response(200,json={"sealed":True})
            if path==module.BASE:
                assert body["embedding_model_id"]==ids["embedding"] and body["rerank_model_id"] is None
                return httpx.Response(201,json={"id":ids["kb"]})
            if path=="/api/v1/agents":
                assert body["knowledge_ids"]==[ids["kb"]] and body["model_id"]==ids["lead"]
                return httpx.Response(201,json={"id":ids["agent"]})
            if path.endswith("/publish"):return httpx.Response(201,json={"id":ids["version"],"version":1})
            if path=="/api/v1/conversations":return httpx.Response(201,json={"id":ids["conversation"]})
            if path.endswith("/runs"):
                data=json.loads((output/"journal.json").read_text(encoding="utf-8"));assert data["attempts"][0]["knowledge_baseline_ref"]
                assert body=={"message":specimen.input,"model_id":ids["lead"]};state["submitted"]=True
                if lost=="ask":raise httpx.ReadError("Controlled lost Ask reply")
                return httpx.Response(202,json={"run_id":ids["ask"],"status":"PENDING"})
            raise AssertionError((method,path))
        if method!="GET":raise AssertionError("No KB/doc DELETE is allowed for historical cleanup")
        if path==module.BASE:return httpx.Response(200,json={"items":[{"id":ids["kb"],"owner_id":value.owner_id,"embedding_model_id":ids["embedding"],"rerank_model_id":None,"keyword_min_coverage":0.2,"chunk_size":800,"chunk_overlap":120,"processing_revision":1}]})
        if path.endswith("/documents"):return httpx.Response(200,json={"items":[{"id":ids["document"],"knowledge_id":ids["kb"],"name":"warranty.txt","revision":1,"status":"READY","source_digest":fixed_evidence.text_digest(DOCUMENT),"chunks":1,"vector_status":"READY","vector_model_id":ids["embedding"],"vector_model_revision":1,"vector_fingerprint":"f"*64,"vector_dimensions":4}]})
        if path.endswith("/revisions"):return httpx.Response(200,json={"items":[{"document_id":ids["document"],"revision":1,"source_digest":fixed_evidence.text_digest(DOCUMENT),"chunks":1}]})
        if path.endswith("/chunks"):return httpx.Response(200,json={"items":[{"id":ids["chunk"],"position":0,"location":"section 1","content":DOCUMENT,"document_revision":1}]})
        if path.endswith("/operations"):return httpx.Response(200,json={"items":[{"id":ids["index"],"action":"index","source_id":ids["document"],"source_revision":1,"status":"SUCCEEDED","error_code":None,"usage":{**usage(1,7,0),"run_id":ids["index"]}}]})
        mode="resource" if ids["index"] in path else "ask";run=ids["index"] if mode=="resource" else ids["ask"]
        if path.endswith("/configurations"):return httpx.Response(200,json=trace(mode,run))
        if path.endswith("/observations"):
            return httpx.Response(200,json={"mode":mode,"run_id":run,"status":"SUCCEEDED","controlled_model":{"recorded":True,"complete":True,"case_id":case,"provider_calls":1 if mode=="resource" else 3,"tokens":7 if mode=="resource" else 37},"context_assertions":[]})
        if path.endswith("/messages"):return httpx.Response(200,json=messages())
        if path.endswith("/usage"):return httpx.Response(200,json={"total":usage(3,27,10)})
        if path.endswith("/events"):
            rows=[{"event":"run.started","data":{}},{"event":"tool.started","data":{"name":"search_knowledge","call_id":tool_id}},{"event":"tool.completed","data":{"name":"search_knowledge","call_id":tool_id}},{"event":"run.completed","data":{}}]
            frames=[dict(x,run_id=ids["ask"],event_id=i) for i,x in enumerate(rows,1)]+[{"event":"stream.end","data":{"status":"SUCCEEDED"}}]
            return httpx.Response(200,text="".join("data: "+json.dumps(x)+"\n\n" for x in frames))
        if path=="/api/v1/runs/"+ids["ask"]:return httpx.Response(200,json={"id":ids["ask"],"status":"SUCCEEDED","conversation_id":ids["conversation"],"model_id":ids["lead"]})
        raise AssertionError((method,path))
    monkeypatch.setattr(fixed_run,"Platform",lambda val:Platform(val,transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(Platform,"login",lambda _:None)
    manifest=tmp_path/"target.json";atomic_json(manifest,value.model_dump())
    def command(action):
        monkeypatch.setattr(sys,"argv",["fixed_run",action,"--case-id",case,"--output",str(output),"--target-manifest",str(manifest)])
        return fixed_run.main()
    return command,output,posts,ids,proof,caps


@pytest.fixture(autouse=True)
def catalog_path(monkeypatch):
    monkeypatch.setattr(fixed_run,"CATALOG_PATH",ROOT/"services/evaluation/datasets/a20-fixed.json")


@pytest.mark.parametrize("case,populated",[("AET-A03",True),("AET-A04",False),("AET-A04",True)])
def test_complete_cli_ready_preserves_real_candidates_and_two_native_roots(tmp_path,monkeypatch,case,populated):
    command,output,posts,_ids,_,_=harness(tmp_path,monkeypatch,case,populated=populated)
    assert command("preflight")==command("init")==command("run")==command("cleanup")==command("report")==0
    ready=fixed_audit.validate_ready(output);assert ready["gate"]=="PASS"
    data=json.loads((output/"journal.json").read_text(encoding="utf-8"));a=data["attempts"][0]
    value=module.verified(output,data,a,next(x for x in fixed_run.catalog().cases if x.case_id==case))
    assert value["usage"]==usage(4,34,10) and a["cleanup_status"]=="CLEAN"
    assert a["retained_resources"] and len(posts)==10
    assert sum(x.endswith("/documents") for x in posts)==1 and sum(x.endswith("/runs") for x in posts)==1
    candidate=value["body"]["messages"][2]["text"]
    assert len(json.loads(candidate)[0]["items"])==int(populated)
    audits=[fixed_evidence.load(output,ref,"network-audit") for ref in ready["network_audit_refs"]]
    assert [x["action"] for x in audits]==["preflight","init","run","cleanup","report"]
    assert all(x["status"]=="COMPLETE" and {p["name"] for p in x["phases"]}=={"execution","export"} for x in audits)


@pytest.mark.parametrize("lost",["upload","ask"])
def test_unknown_upload_or_ask_is_never_reposted_and_cannot_clean(tmp_path,monkeypatch,lost):
    command,output,posts,_,_,_=harness(tmp_path,monkeypatch,lost=lost)
    if lost=="upload":assert command("init")!=0 and command("init")!=0
    else:
        assert command("init")==0
        assert command("run")!=0 and command("run")!=0
    assert command("cleanup")!=0
    suffix="/documents" if lost=="upload" else "/runs"
    assert sum(x.endswith(suffix) for x in posts)==1
    assert fixed_audit.validate_ready(output)["gate"]=="NEEDS_REVIEW"
    assert json.loads((output/"journal.json").read_text(encoding="utf-8"))["attempts"][0]["cleanup_status"]=="NEEDS_REVIEW"


def test_degraded_search_is_not_no_evidence_and_retains_raw_result(tmp_path,monkeypatch):
    command,output,_,_,_,_=harness(tmp_path,monkeypatch,"AET-A04",degraded=True)
    assert command("init")==0 and command("run")!=0
    data=json.loads((output/"journal.json").read_text(encoding="utf-8"));a=data["attempts"][0]
    ref=next(x for x in a["evidence_refs"] if x["kind"]=="knowledge-facts")
    raw=fixed_evidence.load(output,ref,"knowledge-facts")["body"]["messages"][2]["text"]
    assert json.loads(raw)[0]["mode"]=="keyword" and json.loads(raw)[0]["warning"]
    assert command("cleanup")!=0


def test_known_factual_fail_stays_fail_but_can_retain_settled_history(tmp_path,monkeypatch):
    command,output,_,_,_,_=harness(tmp_path,monkeypatch,bad_final=True)
    assert command("init")==0 and command("run")==1 and command("cleanup")==0
    assert fixed_audit.validate_ready(output)["gate"]=="FAIL"
    a=json.loads((output/"journal.json").read_text(encoding="utf-8"))["attempts"][0]
    assert a["cleanup_status"]=="CLEAN" and a["task_outcome"]=="FAIL"


@pytest.mark.parametrize("kind",["knowledge-facts","a02-facts"])
def test_wrong_baseline_ref_kind_is_rejected_even_if_bytes_hash_is_valid(tmp_path,monkeypatch,kind):
    command,output,_,_,_,_=harness(tmp_path,monkeypatch)
    assert command("init")==command("run")==0
    data=json.loads((output/"journal.json").read_text(encoding="utf-8"));a=data["attempts"][0]
    a["knowledge_baseline_ref"]["kind"]=kind
    with pytest.raises(ValueError):module.verified(output,data,a,next(x for x in fixed_run.catalog().cases if x.case_id=="AET-A03"))


@pytest.mark.parametrize("mutation",["owner","source_id","revision","quote","call_id","boolean_provider","event_order","nonfinite_score"])
def test_rehashed_retained_facts_cannot_change_scope_source_or_metering(tmp_path,monkeypatch,mutation):
    command,output,_,_,_,_=harness(tmp_path,monkeypatch)
    assert command("init")==command("run")==0
    data=json.loads((output/"journal.json").read_text(encoding="utf-8"));a=data["attempts"][0];ref=next(x for x in a["evidence_refs"] if x["kind"]=="knowledge-facts")
    path=output/ref["path"];value=json.loads(path.read_text(encoding="utf-8"));b=value["body"]
    if mutation=="owner":value["binding"]["owner_id"]=str(uuid4())
    elif mutation=="source_id":b["baseline"]["operation"]["source_id"]=str(uuid4())
    elif mutation=="revision":b["messages"][2]["text"]=b["messages"][2]["text"].replace('"document_revision": 1','"document_revision": true')
    elif mutation=="quote":b["messages"][3]["text"]=b["messages"][3]["text"].replace(DOCUMENT,"伪造引文")
    elif mutation=="call_id":b["trace"]["items"][0]["calls"][0]["call_id"]=b["baseline"]["trace"]["items"][0]["calls"][0]["call_id"]
    elif mutation=="boolean_provider":b["provider"]["controlled_model"]["provider_calls"]=True
    elif mutation=="event_order":b["events"][0]["event_id"]=True
    else:b["messages"][2]["text"]=b["messages"][2]["text"].replace('"score": 0.03','"score": 1e999')
    atomic_json(path,value);ref["sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
    specimen=next(x for x in fixed_run.catalog().cases if x.case_id=="AET-A03")
    if mutation=="quote":
        recomputed=module.verified(output,data,a,specimen)
        assert module.evaluate(recomputed,specimen,a)[0][0].status=="FAIL"
        with pytest.raises(ValueError):fixed_run.validate_offline_report(data,specimen,fixed_run.catalog(),directory=output)
    else:
        with pytest.raises((ValueError,adapters.Blocked)):module.verified(output,data,a,specimen)


def test_non_io_save_failure_poison_blocks_grading_cleanup_and_new_effects(tmp_path,monkeypatch):
    command,output,posts,_,_,_=harness(tmp_path,monkeypatch)
    assert command("init")==command("run")==0
    data=json.loads((output/"journal.json").read_text(encoding="utf-8"));a=deepcopy(data["attempts"][0]);ref=next(x for x in a["evidence_refs"] if x["kind"]=="knowledge-facts");body=fixed_evidence.load(output,ref,"knowledge-facts")["body"]
    def fail():raise ValueError("Controlled non-IO persistence failure")
    journal=SimpleNamespace(directory=tmp_path/"poison",data=data,save=fail)
    with pytest.raises(ValueError):module.retain(journal,a,"knowledge-facts",body)
    assert a["knowledge_evidence_failed"]
    count=len(posts)
    with pytest.raises(adapters.Blocked):module.verified(output,data,a,next(x for x in fixed_run.catalog().cases if x.case_id=="AET-A03"))
    assert len(posts)==count


def test_rerun_borrows_only_same_immutable_fact_snapshot_without_new_business_posts(tmp_path,monkeypatch):
    command,output,posts,_,_,_=harness(tmp_path,monkeypatch)
    assert command("init")==command("run")==0
    before=json.loads((output/"journal.json").read_text(encoding="utf-8"))["attempts"][0]["evidence_refs"]
    count=len(posts)
    assert command("run")==0 and len(posts)==count
    after=json.loads((output/"journal.json").read_text(encoding="utf-8"))["attempts"][0]["evidence_refs"]
    assert before==after


def test_duplicate_final_snapshots_cannot_select_a_later_verdict(tmp_path,monkeypatch):
    command,output,_,_,_,_=harness(tmp_path,monkeypatch)
    assert command("init")==command("run")==0
    data=json.loads((output/"journal.json").read_text(encoding="utf-8"));a=data["attempts"][0]
    a["evidence_refs"].append(deepcopy(a["evidence_refs"][-1]))
    with pytest.raises(ValueError):module.verified(output,data,a,next(x for x in fixed_run.catalog().cases if x.case_id=="AET-A03"))
