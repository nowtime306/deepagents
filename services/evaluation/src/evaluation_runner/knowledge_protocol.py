"""Private closed A03/A04 protocol. No platform imports, requests, provider or storage effects."""
import json
import math
from uuid import UUID

DOCUMENT = "星桥产品保修三年，须提供发票。"
QUERIES = {"AET-A03": "星桥 保修", "AET-A04": "木星 核动力"}
TOTAL = {"calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens"}
RESULT_KEYS = {"operation_id", "mode", "reranked", "warning", "evidence_status", "guidance", "keyword_candidates",
               "keyword_filtered", "keyword_min_coverage", "score_type", "items"}
ITEM_KEYS = {"chunk_id", "document_id", "document_revision", "name", "location", "text", "score", "keyword_score",
             "keyword_coverage", "vector_score"}
SOURCE_KEYS = {"document_id", "chunk_id", "document_revision", "quote"}


def exact(value, fields):
    if type(value) is not dict or set(value) != fields:
        raise ValueError("Unexpected fixed knowledge fields")


def identifier(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Expected canonical resource identity")
    return value


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected observed nonnegative integer")


def decode(text):
    if type(text) is not str or len(text.encode("utf-8")) > 262144:
        raise ValueError("Bounded original tool/final text required")
    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("Duplicate JSON key")
            output[key] = value
        return output
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Nonfinite JSON number")
        return number
    def invalid_constant(value):
        raise ValueError("Nonfinite JSON constant")
    return json.loads(text, object_pairs_hook=unique, parse_float=finite, parse_constant=invalid_constant)


def retrieval(raw, case_id, tool_args, *, document_id, revision, chunks):
    """Validate the complete original single-KB list without changing candidate order or contents."""
    if case_id not in QUERIES or tool_args != {"query": QUERIES[case_id]}:
        raise ValueError("Fixed actual query differs")
    identifier(document_id)
    if type(revision) is not int or revision != 1 or type(chunks) is not dict or not chunks:
        raise ValueError("Immutable revision/chunk evidence is required")
    for key, text in chunks.items():
        identifier(key)
        if text != DOCUMENT:
            raise ValueError("Original chunk is not the fixed uploaded document")
    rows = decode(raw)
    if type(rows) is not list or len(rows) != 1:
        raise ValueError("Expected the real search_knowledge single-KB result list")
    result = rows[0]
    exact(result, RESULT_KEYS)
    if result["mode"] != "hybrid" or result["warning"] is not None or result["reranked"] is not False:
        raise ValueError("Retrieval failed or degraded; this is not no evidence")
    if result["operation_id"] is not None or result["score_type"] != "rrf":
        raise ValueError("Query must belong to its existing Ask root")
    for name in ("keyword_candidates", "keyword_filtered"):
        integer(result[name])
    if (result["keyword_filtered"] > result["keyword_candidates"] or type(result["guidance"]) is not str
            or type(result["keyword_min_coverage"]) not in (int, float)
            or not 0 <= result["keyword_min_coverage"] <= 1):
        raise ValueError("Invalid retained retrieval metadata")
    items = result["items"]
    if type(items) is not list or len(items) > 20 or result["evidence_status"] != ("candidates" if items else "no_evidence"):
        raise ValueError("Incomplete candidate evidence")
    seen = set()
    for item in items:
        exact(item, ITEM_KEYS)
        if (item["document_id"] != document_id or type(item["document_revision"]) is not int
                or item["document_revision"] != revision or item["chunk_id"] not in chunks
                or item["text"] != chunks[item["chunk_id"]] or item["chunk_id"] in seen):
            raise ValueError("Candidate identity/revision/quote differs from the original chunks")
        if any(type(item[name]) not in (int, float) for name in ("score", "keyword_coverage")):
            raise ValueError("Actual candidate scores are required")
        if any(item[name] is not None and type(item[name]) not in (int, float) for name in ("keyword_score", "vector_score")):
            raise ValueError("Invalid optional candidate scores")
        if any(type(item[name]) is not str for name in ("name", "location")):
            raise ValueError("Original candidate location required")
        seen.add(item["chunk_id"])
    return result


def final_answer(raw_final, result, case_id):
    """Check citations against the retained real candidates; unrelated candidates remain untouched."""
    final = decode(raw_final)
    exact(final, {"answer", "sources"})
    if type(final["sources"]) is not list:
        raise ValueError("Sources must be concrete objects")
    if case_id == "AET-A04":
        return final == {"answer": "资料不足", "sources": []}
    if case_id != "AET-A03" or final["answer"] != "星桥产品保修三年" or len(final["sources"]) != 1:
        return False
    source = final["sources"][0]
    exact(source, SOURCE_KEYS)
    if type(source["document_revision"]) is not int:
        return False
    return any(source == {"document_id": item["document_id"], "chunk_id": item["chunk_id"],
                          "document_revision": item["document_revision"], "quote": item["text"]}
               for item in result["items"])


def sources_from_real_tool(raw, case_id, tool_args, **binding):
    """Private controlled-model draft: parse actual results before constructing the final object."""
    result = retrieval(raw, case_id, tool_args, **binding)
    if case_id == "AET-A04":
        return {"answer": "资料不足", "sources": []}
    if not result["items"]:
        raise ValueError("A03 cannot fabricate a citation when no actual candidate exists")
    item = result["items"][0]
    return {"answer": "星桥产品保修三年", "sources": [{"document_id": item["document_id"],
            "chunk_id": item["chunk_id"], "document_revision": item["document_revision"], "quote": item["text"]}]}


def metering(roots, case_id):
    """Reconcile two genuinely observed roots. Never synthesize provider observations from usage."""
    if type(roots) is not list or len(roots) != 2:
        raise ValueError("Both exact index-resource and Ask roots are required")
    expected = {"resource": (1, 7, 0), "ask": (3, 27, 10)}
    seen, calls, tokens = set(), 0, 0
    for row in roots:
        exact(row, {"mode", "run_id", "native", "provider", "trace_call_ids"})
        mode = row["mode"]
        identifier(row["run_id"])
        if mode not in expected or mode in seen:
            raise ValueError("Foreign or duplicate provider root")
        seen.add(mode)
        native, provider = row["native"], row["provider"]
        exact(native, TOTAL)
        for count in native.values(): integer(count)
        n, inputs, outputs = expected[mode]
        if native != {"calls": n, "reported_calls": n, "unknown_calls": 0, "pending_calls": 0,
                      "input_tokens": inputs, "output_tokens": outputs}:
            raise ValueError("Unknown/incomplete or unexpected native usage")
        exact(provider, {"recorded", "complete", "case_id", "provider_calls", "tokens"})
        if (provider["recorded"] is not True or provider["complete"] is not True or provider["case_id"] != case_id
                or type(provider["provider_calls"]) is not int or provider["provider_calls"] != n
                or type(provider["tokens"]) is not int or provider["tokens"] != inputs + outputs):
            raise ValueError("Genuine root_summary observations are required")
        if type(row["trace_call_ids"]) is not list or len(row["trace_call_ids"]) != n:
            raise ValueError("Every native call needs its actual trace linkage")
        for call in row["trace_call_ids"]: identifier(call)
        calls += n
        tokens += inputs + outputs
    identifiers = [call for row in roots for call in row["trace_call_ids"]]
    if len(set(identifiers)) != 4 or len({row["run_id"] for row in roots}) != 2:
        raise ValueError("Calls/roots must be unique; do not count trace and native twice")
    return {"calls": calls, "tokens": tokens, "scope": "two explicitly observed roots; whole-case completeness additionally requires attested hard max_roots=2"}
