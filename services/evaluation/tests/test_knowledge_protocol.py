"""Closed-protocol substitutes only. No business endpoint/index/Qdrant or provider is executed."""
import copy
import json
from uuid import uuid4

import pytest

from evaluation_runner.knowledge_protocol import (
    DOCUMENT,
    final_answer,
    metering,
    retrieval,
    sources_from_real_tool,
)


def sample(case="AET-A03", populated=True):
    document, chunk = str(uuid4()), str(uuid4())
    item = {"document_id": document, "chunk_id": chunk, "document_revision": 1, "text": DOCUMENT,
            "name": "warranty.txt", "location": "section 1", "score": 0.03, "keyword_score": 2.0,
            "keyword_coverage": 1.0, "vector_score": 1.0}
    result = {"operation_id": None, "mode": "hybrid", "reranked": False, "warning": None,
              "evidence_status": "candidates" if populated else "no_evidence", "guidance": "核对证据",
              "keyword_candidates": 1, "keyword_filtered": 0, "keyword_min_coverage": 0.2,
              "score_type": "rrf", "items": [item] if populated else []}
    args = {"query": "星桥 保修" if case == "AET-A03" else "木星 核动力"}
    binding = {"document_id": document, "revision": 1, "chunks": {chunk: DOCUMENT}}
    return result, args, binding


def test_a03_sources_retain_actual_single_kb_list_identifiers_and_quote():
    result, args, binding = sample()
    raw = json.dumps([result], ensure_ascii=False)
    original = copy.deepcopy(result)
    final = sources_from_real_tool(raw, "AET-A03", args, **binding)
    assert final_answer(json.dumps(final), retrieval(raw, "AET-A03", args, **binding), "AET-A03")
    assert final["sources"][0]["quote"] == DOCUMENT and result == original
    assert final["sources"][0]["chunk_id"] == result["items"][0]["chunk_id"]


@pytest.mark.parametrize("populated", [False, True])
def test_a04_empty_or_unrelated_actual_candidates_are_preserved(populated):
    result, args, binding = sample("AET-A04", populated)
    raw = json.dumps([result]);retained = retrieval(raw, "AET-A04", args, **binding)
    final = sources_from_real_tool(raw, "AET-A04", args, **binding)
    assert final == {"answer": "资料不足", "sources": []}
    assert final_answer(json.dumps(final), retained, "AET-A04")
    assert json.dumps([retained]) == raw
    assert len(retained["items"]) == int(populated)


@pytest.mark.parametrize("mutation", ["top_dict", "extra_kb", "fallback", "warning", "wrong_query", "new_operation", "fake_empty"])
def test_failure_fallback_or_shape_cannot_be_interpreted_as_no_evidence(mutation):
    result, args, binding = sample("AET-A04", False);rows = [result]
    if mutation == "top_dict": rows = result
    elif mutation == "extra_kb": rows.append(copy.deepcopy(result))
    elif mutation == "fallback": result["mode"] = "keyword"
    elif mutation == "warning": result["warning"] = "embedding failed"
    elif mutation == "wrong_query": args["query"] = "星桥 保修"
    elif mutation == "new_operation": result["operation_id"] = str(uuid4())
    else: result["evidence_status"] = "candidates"
    with pytest.raises(ValueError): retrieval(json.dumps(rows), "AET-A04", args, **binding)


@pytest.mark.parametrize("field,value", [("document_id", "foreign"), ("chunk_id", "foreign"),
    ("document_revision", 2), ("document_revision", True), ("text", "星桥保修99年")])
def test_real_candidate_identity_revision_and_content_are_required(field, value):
    result, args, binding = sample();result["items"][0][field] = value
    with pytest.raises(ValueError): retrieval(json.dumps([result]), "AET-A03", args, **binding)


@pytest.mark.parametrize("field,value", [("document_id", "foreign"), ("chunk_id", "foreign"),
    ("document_revision", 2), ("document_revision", True), ("quote", "保修99年")])
def test_final_cannot_borrow_a_fabricated_citation(field, value):
    result, args, binding = sample();raw = json.dumps([result])
    final = sources_from_real_tool(raw, "AET-A03", args, **binding);final["sources"][0][field] = value
    assert not final_answer(json.dumps(final), result, "AET-A03")


def test_empty_a03_cannot_fall_back_to_static_document_ids():
    result, args, binding = sample(populated=False)
    with pytest.raises(ValueError): sources_from_real_tool(json.dumps([result]), "AET-A03", args, **binding)


def roots():
    output = []
    for mode,n,inputs,outputs in [("resource",1,7,0),("ask",3,27,10)]:
        output.append({"mode":mode,"run_id":str(uuid4()),
            "native":{"calls":n,"reported_calls":n,"unknown_calls":0,"pending_calls":0,"input_tokens":inputs,"output_tokens":outputs},
            "provider":{"recorded":True,"complete":True,"case_id":"AET-A03","provider_calls":n,"tokens":inputs+outputs},
            "trace_call_ids":[str(uuid4()) for _ in range(n)]})
    return output


def test_native_index_and_ask_provider_accounting_are_separate_and_total44():
    observed = metering(roots(), "AET-A03")
    assert observed["calls"] == 4 and observed["tokens"] == 44
    assert "additionally requires" in observed["scope"]


@pytest.mark.parametrize("mutation", ["unknown", "provider_unobserved", "provider_pending", "wrong_case", "third_root", "duplicate_call", "numeric_bool"])
def test_unknown_or_invented_provider_and_double_accounting_are_rejected(mutation):
    rows = roots()
    if mutation == "unknown": rows[0]["native"]["unknown_calls"] = 1
    elif mutation == "provider_unobserved": rows[0]["provider"] = {"recorded":False,"complete":False}
    elif mutation == "provider_pending": rows[0]["provider"]["complete"] = False
    elif mutation == "wrong_case": rows[0]["provider"]["case_id"] = "AET-A04"
    elif mutation == "third_root": rows.append(copy.deepcopy(rows[0]))
    elif mutation == "duplicate_call": rows[1]["trace_call_ids"][0] = rows[0]["trace_call_ids"][0]
    else: rows[0]["native"]["calls"] = True
    with pytest.raises(ValueError): metering(rows, "AET-A03")


@pytest.mark.parametrize("field",["score","keyword_score","vector_score","keyword_coverage"])
@pytest.mark.parametrize("literal",["1e999","-1e999"])
def test_exponent_overflow_is_rejected_in_original_raw_json(field,literal):
    result,args,binding=sample();result["items"][0][field]=0.123456
    raw=json.dumps([result]).replace('"'+field+'": 0.123456','"'+field+'": '+literal)
    assert literal in raw
    with pytest.raises(ValueError):retrieval(raw,"AET-A03",args,**binding)
