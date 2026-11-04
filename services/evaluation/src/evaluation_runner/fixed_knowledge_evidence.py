"""PRIVATE A03/A04 formal APIs and immutable facts. No direct SQL, provider or Qdrant access."""
import re
from datetime import datetime

from . import adapters
from .contracts import Result
from .fixed_evidence import binding, load, store, text_digest
from .journal import digest
from .knowledge_protocol import (
    DOCUMENT,
    QUERIES,
    decode,
    exact,
    final_answer,
    identifier,
    metering,
    retrieval,
)

LIMITS = {"max_model_calls": 6, "max_tokens": 90, "max_roots": 2}
BASE = "/api/v1/capabilities/knowledge"
TOTAL = ("calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens")


def check_case(specimen):
    if specimen.case_id not in QUERIES or specimen.mode != "ask" or specimen.metrics:
        raise ValueError("Only the two fixed knowledge profiles are supported")


def enforce_case_budget(capabilities, specimen):
    proof = capabilities.get("case_limits") or {}
    if (type(proof.get("schema_version")) is not int or proof["schema_version"] != 1
            or proof.get("enforcement") != "controlled-case-reserve-v1" or proof.get("scope") != "evaluation-owner-and-case"
            or digest(proof.get("profiles", {}).get(specimen.case_id)) != digest(LIMITS)
            or specimen.budgets.max_model_calls < 6 or specimen.budgets.max_tokens < 90
            or "root-model-context-resource-v1" not in capabilities.get("observers", [])):
        raise adapters.Blocked("Whole-case index+Ask budget and owned resource observer are required")


def initialize(fixtures, model, specimen):
    check_case(specimen)
    embedding = fixtures.model(specimen.case_id, "embedding")
    kb = fixtures.create("knowledge", BASE, {"name": "AE warranty " + fixtures.prefix,
                         "embedding_model_id": embedding, "rerank_model_id": None})
    upload = fixtures.mutate("knowledge-document", "POST", f"{BASE}/{kb}/documents",
                            files={"file": ("warranty.txt", DOCUMENT.encode("utf-8"))})
    if type(upload.get("revision")) is not int or upload["revision"] != 1:
        raise ValueError("Original uploaded document revision was not confirmed")
    resource = {"kind": "knowledge-document", "id": upload["id"], "path": f"{BASE}/{kb}/documents/{upload['id']}"}
    if resource not in fixtures.attempt.setdefault("created_resources", []):
        fixtures.attempt["created_resources"].append(resource)
        fixtures.journal.save()
    agent = fixtures.published_agent(specimen.case_id, model, knowledge=[kb])
    return {"model_id": model, "embedding_model_id": embedding, "knowledge_id": kb,
            "document_id": upload["id"], "document_revision": upload["revision"],
            "id": fixtures.conversation(agent), **agent}


def fields(value, keys):
    return {key: value[key] for key in keys.split()}


def native(value):
    row = {key: value[key] for key in TOTAL}
    if any(type(n) is not int or n < 0 for n in row.values()):
        raise ValueError("Complete known native usage must contain integers")
    return row


def root_observation(platform, mode, run):
    value = platform.get(f"/api/v1/agent-evaluations/profiles/runs/{mode}/{run}/observations")
    if value["mode"] != mode or value["run_id"] != run or value["status"] != "SUCCEEDED":
        raise adapters.Blocked("The exact owned provider root is not terminal")
    return fields(value, "mode run_id status controlled_model context_assertions")


def trace_projection(platform, mode, run):
    value = platform.get(f"/api/v1/resource-runs/{mode}/{run}/configurations")
    if (value["mode"] != mode or value["run_id"] != run or value["status"] != "SUCCEEDED"
            or value["configuration_scope"] != {"mode": mode, "run_id": run}):
        raise adapters.Blocked("Resource trace does not belong to the exact successful root")
    items = []
    for row in value["items"]:
        if row["resource_kind"] not in {"model", "knowledge"}:
            continue
        item = fields(row, "snapshot_id resource_kind resource_id revision used_at")
        snapshot = row["snapshot"]
        item["snapshot"] = ({key: snapshot[key] for key in ("embedding_model_id", "rerank_model_id")}
                            if row["resource_kind"] == "knowledge" else {})
        item["calls"] = [fields(call, "call_id run_id mode model_id model_revision status input_tokens output_tokens")
                         for call in row["calls"]]
        items.append(item)
    return {"mode": mode, "run_id": run, "business_version": value["business_version"], "items": items}


def collect_baseline(platform, attempt, specimen):
    fixture = attempt["fixture"]
    kb, doc = fixture["knowledge_id"], fixture["document_id"]
    rows = platform.get(BASE)["items"]
    if len(rows) != 1 or rows[0]["id"] != kb:
        raise adapters.Blocked("The target must contain its single owned fixed knowledge base")
    knowledge = fields(rows[0], "id owner_id embedding_model_id rerank_model_id keyword_min_coverage chunk_size chunk_overlap processing_revision")
    docs = platform.get(f"{BASE}/{kb}/documents")["items"]
    if len(docs) != 1 or docs[0]["id"] != doc:
        raise adapters.Blocked("Complete original document list is required")
    document = fields(docs[0], "id knowledge_id name revision status source_digest chunks vector_status vector_model_id vector_model_revision vector_fingerprint vector_dimensions")
    revisions = platform.get(f"{BASE}/{kb}/documents/{doc}/revisions")["items"]
    versions = [fields(row, "document_id revision source_digest chunks") for row in revisions]
    chunks = platform.get(f"{BASE}/{kb}/documents/{doc}/chunks", params={"revision": fixture["document_revision"]})["items"]
    chunks = [fields(row, "id position location content document_revision") for row in chunks]
    operations = platform.get(f"{BASE}/{kb}/operations")["items"]
    if len(operations) != 1:
        raise adapters.Blocked("Exactly one actual index operation must be observed; never reindex for proof")
    operation = fields(operations[0], "id action source_id source_revision status error_code")
    run = identifier(operation["id"])
    value = {"knowledge": knowledge, "document": document, "revisions": versions, "chunks": chunks,
             "operation": operation, "usage": native(operations[0]["usage"]),
             "trace": trace_projection(platform, "resource", run),
             "provider": root_observation(platform, "resource", run),
             "case_budget": platform.get("/api/v1/agent-evaluations/profiles/capabilities")}
    validate_baseline(value, attempt, specimen)
    return value


def retain(journal, attempt, kind, body):
    value = {"schema_version": 1, "kind": kind, "binding": binding(journal.data, attempt), "body": body}
    if kind == "knowledge-facts":
        value["usage"] = aggregate(body)
    try:
        ref = store(journal.directory, kind, value)
        attempt.setdefault("evidence_refs", []).append(ref)
        if kind == "knowledge-baseline": attempt["knowledge_baseline_ref"] = ref
        journal.save()
    except BaseException:
        attempt["knowledge_evidence_failed"] = True
        raise
    return value


def read(directory, data, attempt, ref, kind):
    value = load(directory, ref, kind)
    expected = {"schema_version", "kind", "binding", "body"} | ({"usage"} if kind == "knowledge-facts" else set())
    if (set(value) != expected or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != kind or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("Knowledge fact kind/scope binding differs")
    return value


def baseline(platform, journal, attempt, specimen):
    if attempt.get("knowledge_evidence_failed"):
        raise adapters.Blocked("Knowledge persistence failed; no further index/Ask or cleanup")
    ref = attempt.get("knowledge_baseline_ref")
    if ref:
        value = read(journal.directory, journal.data, attempt, ref, "knowledge-baseline")
        validate_baseline(value["body"], attempt, specimen)
    else:
        value = retain(journal, attempt, "knowledge-baseline", collect_baseline(platform, attempt, specimen))
    return value["body"]


def validate_trace(trace, attempt, mode, run, counts):
    fixture = attempt["fixture"]
    exact(trace, {"mode", "run_id", "business_version", "items"})
    if trace["mode"] != mode or trace["run_id"] != run:
        raise ValueError("Trace scope differs")
    if mode == "ask" and (trace["business_version"].get("published_version_id") != fixture["agent_version_id"]
                          or trace["business_version"].get("published_version") != 1):
        raise ValueError("The published Agent snapshot was not used")
    models = [x for x in trace["items"] if x["resource_kind"] == "model"]
    knowledge = [x for x in trace["items"] if x["resource_kind"] == "knowledge"]
    if (len(models) != len(counts) or {x["resource_id"] for x in models} != set(counts)
            or len(knowledge) != 1 or knowledge[0]["resource_id"] != fixture["knowledge_id"]
            or knowledge[0]["snapshot"] != {"embedding_model_id":fixture["embedding_model_id"],"rerank_model_id":None}
            or knowledge[0]["calls"] != []):
        raise ValueError("Actual model/KB snapshot identities differ")
    calls = []
    for item in [*models, *knowledge]:
        identifier(item["snapshot_id"])
        if type(item["revision"]) is not str or not item["revision"].isdigit() or item["used_at"] is None:
            raise ValueError("Actual used revision/time is required")
        datetime.fromisoformat(item["used_at"])
    for item in models:
        if item["revision"] != "1" or len(item["calls"]) != counts[item["resource_id"]]:
            raise ValueError("Unexpected model revision or calls")
        for row in item["calls"]:
            exact(row, {"call_id","run_id","mode","model_id","model_revision","status","input_tokens","output_tokens"})
            n_in,n_out = (7,0) if row["model_id"] == fixture["embedding_model_id"] else (10,5)
            if (row["run_id"] != run or row["mode"] != mode or row["model_id"] != item["resource_id"]
                    or type(row["model_revision"]) is not int or row["model_revision"] != 1 or row["status"] != "KNOWN"
                    or type(row["input_tokens"]) is not int or row["input_tokens"] != n_in
                    or type(row["output_tokens"]) is not int or row["output_tokens"] != n_out):
                raise ValueError("A model call has foreign identity or unknown/unexpected usage")
            calls.append(identifier(row["call_id"]))
    if len(set(calls)) != len(calls): raise ValueError("Duplicate actual model call")
    return calls


def validate_baseline(value, attempt, specimen):
    check_case(specimen)
    exact(value, {"knowledge","document","revisions","chunks","operation","usage","trace","provider","case_budget"})
    f = attempt["fixture"];k,d,op = value["knowledge"],value["document"],value["operation"]
    if (k["id"] != f["knowledge_id"] or k["owner_id"] != attempt["owner_id"] or k["embedding_model_id"] != f["embedding_model_id"]
            or k["rerank_model_id"] is not None or d["knowledge_id"] != k["id"] or d["id"] != f["document_id"]
            or type(d["revision"]) is not int or d["revision"] != f["document_revision"] or d["revision"] != 1
            or d["status"] != "READY" or d["source_digest"] != text_digest(DOCUMENT) or type(d["chunks"]) is not int or d["chunks"] != 1
            or d["vector_status"] != "READY" or d["vector_model_id"] != f["embedding_model_id"]
            or type(d["vector_model_revision"]) is not int or d["vector_model_revision"] != 1
            or type(d["vector_dimensions"]) is not int or d["vector_dimensions"] != 4
            or type(d["vector_fingerprint"]) is not str or not re.fullmatch(r"[0-9a-f]{64}",d["vector_fingerprint"])):
        raise adapters.Blocked("Original indexed document/binding is not complete")
    if digest(value["revisions"]) != digest([{"document_id":d["id"],"revision":1,"source_digest":d["source_digest"],"chunks":1}]):
        raise ValueError("Original source revision changed")
    if len(value["chunks"]) != 1 or value["chunks"][0]["content"] != DOCUMENT or type(value["chunks"][0]["document_revision"]) is not int or value["chunks"][0]["document_revision"] != 1:
        raise ValueError("Complete real original chunks are required")
    identifier(value["chunks"][0]["id"])
    if (op["action"] != "index" or op["source_id"] != f["document_id"] or type(op["source_revision"]) is not int
            or op["source_revision"] != 1 or op["status"] != "SUCCEEDED" or op["error_code"] is not None):
        raise adapters.Blocked("Index failed/unknown; it is not empty retrieval")
    expected = {"calls":1,"reported_calls":1,"unknown_calls":0,"pending_calls":0,"input_tokens":7,"output_tokens":0}
    if value["usage"] != expected or any(type(n) is not int for n in value["usage"].values()):
        raise ValueError("Actual index native usage differs")
    validate_trace(value["trace"],attempt,"resource",op["id"],{f["embedding_model_id"]:1})
    provider = value["provider"]
    if provider["mode"] != "resource" or provider["run_id"] != op["id"] or provider["status"] != "SUCCEEDED":
        raise ValueError("Index provider root differs")
    if digest(provider["controlled_model"]) != digest({"recorded":True,"complete":True,"case_id":specimen.case_id,"provider_calls":1,"tokens":7}):
        raise ValueError("Actual resource provider observation is absent/incomplete")
    enforce_case_budget(value["case_budget"],specimen)


def messages(platform, attempt):
    root=attempt["platform_root"];response=platform.get(f"/api/v1/conversations/{root['fixture_id']}/messages")
    if response.get("next_before","missing") is not None or not isinstance(response.get("items"),list) or len(response["items"]) != 4:
        raise adapters.Blocked("The complete original four messages are required")
    output=[]
    for row in response["items"]:
        if row["run_id"] != root["id"]: raise ValueError("Foreign message root")
        role=row["role"];data=row["content"].get("data",{})
        text=row["content"].get("text") if role=="user" else data.get("content")
        if type(text) is not str or len(text.encode("utf-8"))>32768:raise ValueError("Bounded actual text required")
        projected={"role":role,"text":text}
        if role=="assistant":projected["calls"]=[fields(x,"id name args") for x in data["tool_calls"]]
        elif role=="tool":projected.update(fields(data,"name tool_call_id status"))
        elif role!="user":raise ValueError("Unexpected message role")
        output.append(projected)
    return output


def events(platform, run):
    rows=[];ended=False
    with platform.http.stream("GET",f"/api/v1/runs/{run}/events",params={"after_event_id":0}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if len(line)>65536:raise ValueError("Bounded event required")
            if not line.startswith("data: "):continue
            value=decode(line[6:])
            if type(value) is not dict or type(value.get("event")) is not str:
                raise ValueError("Typed event frame required")
            if ended:raise ValueError("No event data is allowed after stream end")
            event=value["event"]
            if event.startswith("stream."):
                exact(value,{"event","data"})
                data=value["data"]
                if event=="stream.error":raise adapters.Blocked("Native event stream reported an error")
                if event=="stream.snapshot":
                    exact(data,{"run","diagnostics","usage","team"})
                    if (type(data["run"]) is not dict or data["run"].get("id")!=run
                            or data["run"].get("status")!="SUCCEEDED"
                            or any(type(data[name]) is not dict for name in ("diagnostics","usage","team"))):
                        raise ValueError("Presentation snapshot differs from the settled root")
                elif event=="stream.end":
                    exact(data,{"status"})
                    if data["status"]!="SUCCEEDED":raise adapters.Blocked("Native stream did not end successfully")
                    ended=True
                else:raise ValueError("Unknown native stream control frame")
                continue
            if (value.get("run_id")!=run or type(value.get("event_id")) is not int
                    or value["event_id"]!=len(rows)+1 or len(rows)>=128):raise ValueError("Complete ordered root events required")
            item=fields(value,"event_id event")
            if value["event"].startswith("tool."):item.update(fields(value["data"],"name call_id"))
            rows.append(item)
    if not ended or not rows or rows[-1]["event"]!="run.completed":raise adapters.Blocked("Terminal native events incomplete")
    return rows


def capture(platform,journal,attempt,specimen,evidence):
    before=baseline(platform,journal,attempt,specimen);root=attempt["platform_root"]
    body={"baseline":before,"after_index":collect_baseline(platform,attempt,specimen),
          "run":fields(evidence["run"],"id status conversation_id model_id"),"messages":messages(platform,attempt),
          "events":events(platform,root["id"]),"trace":trace_projection(platform,"ask",root["id"]),
          "provider":root_observation(platform,"ask",root["id"]),"ask_usage":native(evidence["usage"]["total"])}
    value=retain(journal,attempt,"knowledge-facts",body)
    evaluate(value,specimen,attempt)
    return verified(journal.directory,journal.data,attempt,specimen)


def execute(platform,journal,attempt,specimen,*,cancelled=lambda:False):
    if any(ref.get("kind")=="knowledge-facts" for ref in attempt.get("evidence_refs",[])):
        return verified(journal.directory,journal.data,attempt,specimen)
    baseline(platform,journal,attempt,specimen)
    if cancelled():raise adapters.Blocked("Cancelled before knowledge Ask dispatch")
    actual=specimen.model_copy(update={"fixture_parameters":{"prior_messages":[]}})
    if not attempt.get("submission_complete"):adapters.submit(platform,journal,attempt,actual,attempt["fixture"])
    observed=adapters.observe(platform,journal,attempt,actual,cancelled=cancelled)
    return capture(platform,journal,attempt,specimen,observed)


def aggregate(body):
    return {key:body["baseline"]["usage"][key]+body["ask_usage"][key] for key in TOTAL}


def evaluate(snapshot,specimen,attempt):
    body=snapshot["body"];f=attempt["fixture"];root=attempt["platform_root"]
    exact(body,{"baseline","after_index","run","messages","events","trace","provider","ask_usage"})
    validate_baseline(body["baseline"],attempt,specimen);validate_baseline(body["after_index"],attempt,specimen)
    if digest(body["baseline"])!=digest(body["after_index"]):raise ValueError("Index source/usage changed after Ask")
    intent=attempt.get("submission_intents",{}).get("submit-run",{})
    if (root!={"id":body["run"]["id"],"mode":"ask","fixture_id":f["id"],"path":"/api/v1/runs/"+body["run"]["id"]}
            or body["run"]!={"id":root["id"],"status":"SUCCEEDED","conversation_id":f["id"],"model_id":f["model_id"]}
            or intent.get("status")!="CONFIRMED" or intent.get("response",{}).get("run_id")!=root["id"]
            or intent.get("path")!=f"/api/v1/conversations/{f['id']}/runs"
            or intent.get("payload_digest")!=digest({"message":specimen.input,"model_id":f["model_id"]})):
        raise ValueError("Actual confirmed Ask submission differs")
    rows=body["messages"]
    if [x["role"] for x in rows]!=["user","assistant","tool","assistant"] or rows[0]["text"]!=specimen.input:
        raise ValueError("Actual input/tool/final sequence differs")
    calls=rows[1]["calls"]
    if len(calls)!=1 or calls[0]["name"]!="search_knowledge" or rows[3]["calls"]!=[]:
        raise ValueError("Exactly one genuine knowledge tool call is required")
    call=calls[0]
    if type(call["id"]) is not str or not re.fullmatch(r"ae-[0-9a-f]{16}",call["id"]):raise ValueError("Actual controlled call identity differs")
    if rows[2]["name"]!="search_knowledge" or rows[2]["tool_call_id"]!=call["id"] or rows[2]["status"]!="success":
        raise adapters.Blocked("Retrieval failed; it is not empty evidence")
    chunks={x["id"]:x["content"] for x in body["baseline"]["chunks"]}
    result=retrieval(rows[2]["text"],specimen.case_id,call["args"],document_id=f["document_id"],revision=1,chunks=chunks)
    events_=body["events"]
    if not isinstance(events_,list) or not 1<=len(events_)<=128:raise ValueError("Bounded complete events required")
    for index,event in enumerate(events_,1):
        expected={"event_id","event"}|({"name","call_id"} if str(event.get("event","")).startswith("tool.") else set())
        exact(event,expected)
        if type(event["event_id"]) is not int or event["event_id"]!=index or type(event["event"]) is not str or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}",event["event"]):
            raise ValueError("Actual retained event sequence changed")
    if (not events_ or events_[0]["event"]!="run.started" or events_[-1]["event"]!="run.completed"
            or sum(x["event"]=="run.started" for x in events_)!=1 or sum(x["event"]=="run.completed" for x in events_)!=1
            or any(x["event"] in {"run.failed","run.cancelled","tool.interrupted"} for x in events_)):
        raise ValueError("Actual native lifecycle differs")
    tool_events=[fields(event,"event name call_id") for event in events_ if event["event"].startswith("tool.")]
    expected_tools=[{"event":name,"name":"search_knowledge","call_id":call["id"]}
                    for name in ("tool.started","tool.completed")]
    if tool_events!=expected_tools:
        raise ValueError("Complete native tool lifecycle or binding differs")
    index=body["baseline"];ask=body["provider"]
    if ask["mode"]!="ask" or ask["run_id"]!=root["id"] or ask["status"]!="SUCCEEDED":raise ValueError("Actual Ask provider scope differs")
    roots=[{"mode":"resource","run_id":index["operation"]["id"],"native":index["usage"],"provider":index["provider"]["controlled_model"],
            "trace_call_ids":validate_trace(index["trace"],attempt,"resource",index["operation"]["id"],{f["embedding_model_id"]:1})},
           {"mode":"ask","run_id":root["id"],"native":body["ask_usage"],"provider":ask["controlled_model"],
            "trace_call_ids":validate_trace(body["trace"],attempt,"ask",root["id"],{f["embedding_model_id"]:1,f["model_id"]:2})}]
    metering(roots,specimen.case_id)
    if digest(snapshot["usage"])!=digest(aggregate(body)):raise ValueError("Whole-case native totals changed")
    passed=final_answer(rows[3]["text"],result,specimen.case_id)
    return [Result(registered_id=specimen.validators[0].registered_id,status="PASS" if passed else "FAIL")],{
        "run":{"status":body["run"]["status"]},"usage":{"total":snapshot["usage"]},"observed":["usage"]}


def verified(directory,data,attempt,specimen):
    if attempt.get("knowledge_evidence_failed"):raise adapters.Blocked("Poisoned evidence cannot grade/clean")
    ref=attempt.get("knowledge_baseline_ref")
    if ref not in attempt.get("evidence_refs",[]):raise ValueError("Original baseline reference missing")
    before=read(directory,data,attempt,ref,"knowledge-baseline");validate_baseline(before["body"],attempt,specimen)
    kinds=[item.get("kind") for item in attempt["evidence_refs"]]
    if kinds.count("knowledge-baseline")!=1 or kinds.count("knowledge-facts")!=1:
        raise ValueError("Exactly one immutable baseline and final snapshot are required")
    final=None
    for ref in attempt["evidence_refs"]:
        if ref["kind"]=="knowledge-baseline":
            if read(directory,data,attempt,ref,"knowledge-baseline")!=before:raise ValueError("Foreign/duplicate baseline")
        elif ref["kind"]=="knowledge-facts":
            value=read(directory,data,attempt,ref,"knowledge-facts")
            if digest(value["body"]["baseline"])!=digest(before["body"]):raise ValueError("Retained baseline changed")
            evaluate(value,specimen,attempt);final=value
        else:raise ValueError("Unexpected knowledge evidence kind")
    if final is None:raise adapters.Blocked("Original knowledge facts missing")
    return final


def cleanup_ready(directory,data,attempt,specimen):
    verified(directory,data,attempt,specimen)
    return True


def cleanup(platform,journal,attempt,specimen):
    value=verified(journal.directory,journal.data,attempt,specimen)
    current=collect_baseline(platform,attempt,specimen)
    root=attempt["platform_root"]["id"]
    provider=root_observation(platform,"ask",root)
    return "CLEAN" if digest(current)==digest(value["body"]["after_index"]) and digest(provider)==digest(value["body"]["provider"]) else "NEEDS_REVIEW"
