"""Fixed v2 team fixtures, formal HTTP, immutable facts and conservative retained cleanup."""

import json
from pathlib import Path
from uuid import UUID

from . import adapters, fixed_work_observation
from .contracts import Dataset, Result
from .fixed_evidence import binding, load, store, text_digest
from .journal import digest
from .knowledge_protocol import DOCUMENT, retrieval
from .team_budget_protocol import fixed_call_sequence, ledger, separate_call_ids

DEFINITIONS_PATH = (
    Path(__file__).resolve().parents[2] / "datasets/team-definitions-v2.json"
)
CASES = {"AET-W05", "AET-W06"}
BASE = "/api/v1/capabilities/knowledge"
LIMITS = {
    "AET-W05": {"max_model_calls": 3, "max_tokens": 45, "max_roots": 1},
    "AET-W06": {"max_model_calls": 7, "max_tokens": 16007, "max_roots": 2},
}


def check_case(specimen):
    registered = Dataset.model_validate_json(
        DEFINITIONS_PATH.read_text(encoding="utf-8")
    )
    original = next(
        (case for case in registered.cases if case.case_id == specimen.case_id), None
    )
    if original is None or digest(original.model_dump(mode="json")) != digest(
        specimen.model_dump(mode="json")
    ):
        raise ValueError("Only the exact frozen v2 team definition is executable")


def supported(specimen):
    try:
        check_case(specimen)
        return True
    except (ValueError, OSError):
        return False


def readiness(platform, specimen, capabilities):
    check_case(specimen)
    proof = capabilities.get("team_profiles") or {}
    budget = capabilities.get("case_limits") or {}
    if (
        proof.get("protocol") != "owned-team-native-effects-v2"
        or type(budget.get("schema_version")) is not int
        or budget["schema_version"] != 1
        or budget.get("enforcement") != "controlled-case-reserve-v1"
        or budget.get("scope") != "evaluation-owner-and-case"
        or proof.get("audit") != "controlled-team-effects-v2"
        or proof.get("native_guard") != "native-work-model-tool-v1"
        or proof.get("member_resource_context") != "work-member-resource-v1"
        or proof.get("definition_versions", {}).get(specimen.case_id) != 2
        or capabilities.get("case_limits", {}).get("profiles", {}).get(specimen.case_id)
        != LIMITS[specimen.case_id]
    ):
        raise adapters.Blocked(
            "Actual team/native guard/member resource bridge is unavailable"
        )
    current = proof.get("case_state", {}).get(specimen.case_id, {})
    if (
        current.get("observed") is not True
        or current.get("complete") is not True
        or any(
            type(current.get(key)) is not int or current[key] < 0
            for key in ("calls", "tokens", "pending", "unknown_roots")
        )
        or current["pending"]
        or current["unknown_roots"]
        or current["calls"] >= LIMITS[specimen.case_id]["max_model_calls"]
        or current["tokens"] >= LIMITS[specimen.case_id]["max_tokens"]
        or not isinstance(current.get("roots"), list)
        or len(current["roots"]) >= LIMITS[specimen.case_id]["max_roots"]
    ):
        raise adapters.Blocked(
            "The case budget is exhausted/unsettled; no fresh target effect is allowed"
        )


def initialize(fixtures, model, specimen):
    check_case(specimen)
    case = specimen.case_id
    resources = {}
    if case == "AET-W06":
        embedding = fixtures.model(case, "embedding")
        kb = fixtures.create(
            "team-knowledge",
            BASE,
            {
                "name": "AE team " + fixtures.prefix,
                "embedding_model_id": embedding,
                "rerank_model_id": None,
            },
        )
        document = fixtures.mutate(
            "team-document",
            "POST",
            f"{BASE}/{kb}/documents",
            files={"file": ("team-warranty.txt", DOCUMENT.encode())},
        )
        if type(document.get("revision")) is not int or document["revision"] != 1:
            raise ValueError("Original team knowledge upload was not confirmed")
        resource = {
            "kind": "knowledge-document",
            "id": document["id"],
            "path": f"{BASE}/{kb}/documents/{document['id']}",
        }
        if resource not in fixtures.attempt.setdefault("created_resources", []):
            fixtures.attempt["created_resources"].append(resource)
            fixtures.journal.save()
        resources = {
            "knowledge_id": kb,
            "document_id": document["id"],
            "document_revision": 1,
            "embedding_model_id": embedding,
        }
    knowledge = [resources["knowledge_id"]] if resources else []
    reviewer = fixtures.model(case, "reviewer")
    child = fixtures.published_agent(case, reviewer, knowledge=knowledge)
    parent = fixtures.published_agent(
        case,
        model,
        knowledge=knowledge,
        team=[
            {
                "name": "reviewer",
                "version_id": child["agent_version_id"],
                "goal": "读取固定输入并审阅"
                if case == "AET-W05"
                else "查询星桥保修并保留真实来源",
            }
        ],
    )
    inputs = specimen.fixture_parameters["fixed_inputs"]
    task = fixtures.work_task(inputs["goal"], inputs["files"])
    return {
        **task,
        **parent,
        **resources,
        "reviewer_agent_id": child["agent_id"],
        "reviewer_version_id": child["agent_version_id"],
        "reviewer_model_id": reviewer,
        "model_id": model,
    }


def trace(platform, mode, identifier):
    value = platform.get(f"/api/v1/resource-runs/{mode}/{identifier}/configurations")
    if (
        value["mode"] != mode
        or value["run_id"] != identifier
        or value["configuration_scope"] != {"mode": mode, "run_id": identifier}
    ):
        raise ValueError("Foreign actual resource trace")
    return {
        "mode": mode,
        "run_id": identifier,
        "status": value["status"],
        "business_version": value["business_version"],
        "items": [
            {
                "resource_kind": row["resource_kind"],
                "resource_id": row["resource_id"],
                "revision": row["revision"],
                "snapshot_id": row["snapshot_id"],
                "used_at": row["used_at"],
                "snapshot": {
                    key: row["snapshot"].get(key)
                    for key in ("id", "revision", "model", "kind", "protocol")
                },
                "calls": [
                    {
                        key: call.get(key)
                        for key in (
                            "call_id",
                            "run_id",
                            "mode",
                            "model_id",
                            "model_revision",
                            "status",
                            "input_tokens",
                            "output_tokens",
                        )
                    }
                    for call in row["calls"]
                ],
            }
            for row in value["items"]
            if row["resource_kind"] == "model"
        ],
    }


def index_baseline(platform, attempt):
    fixture = attempt["fixture"]
    kb, document = fixture["knowledge_id"], fixture["document_id"]
    docs = platform.get(f"{BASE}/{kb}/documents")["items"]
    chunks = platform.get(
        f"{BASE}/{kb}/documents/{document}/chunks", params={"revision": 1}
    )["items"]
    operations = platform.get(f"{BASE}/{kb}/operations")["items"]
    if len(docs) != 1 or len(chunks) != 1 or len(operations) != 1:
        raise adapters.Blocked("Original single document/index/chunk is not complete")
    row, chunk, operation = docs[0], chunks[0], operations[0]
    usage = operation["usage"]
    if not isinstance(usage, dict) or usage.get("run_id") != operation["id"]:
        raise ValueError("Original index usage belongs to another operation")
    if (
        row["id"] != document
        or row["revision"] != 1
        or row["status"] != "READY"
        or row["vector_status"] != "READY"
        or row["vector_model_id"] != fixture["embedding_model_id"]
        or row["vector_dimensions"] != 4
        or row["source_digest"] != text_digest(DOCUMENT)
        or chunk["content"] != DOCUMENT
        or chunk["document_revision"] != 1
        or operation["source_id"] != document
        or operation["action"] != "index"
        or operation["status"] != "SUCCEEDED"
    ):
        raise adapters.Blocked(
            "Real original index is unavailable, not empty retrieval"
        )
    result = {
        "document_id": document,
        "chunk_id": chunk["id"],
        "document_revision": 1,
        "content": chunk["content"],
        "operation_id": operation["id"],
        "usage": {key: usage[key] for key in (
            "calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens"
        )},
        "trace": trace(platform, "resource", operation["id"]),
        "provider": platform.get(
            f"/api/v1/agent-evaluations/profiles/runs/resource/{operation['id']}/observations"
        ),
    }
    bases = platform.get(BASE)["items"]
    revisions = platform.get(f"{BASE}/{kb}/documents/{document}/revisions")["items"]
    if len(bases) != 1 or bases[0]["id"] != kb:
        raise adapters.Blocked("Exactly one original owner knowledge base is required")
    result["knowledge"] = {
        key: bases[0][key]
        for key in ("id", "owner_id", "embedding_model_id", "rerank_model_id")
    }
    result["document"] = {
        key: row[key]
        for key in (
            "id",
            "knowledge_id",
            "revision",
            "status",
            "source_digest",
            "chunks",
            "vector_status",
            "vector_model_id",
            "vector_model_revision",
            "vector_fingerprint",
            "vector_dimensions",
        )
    }
    result["revisions"] = [
        {
            key: version[key]
            for key in ("document_id", "revision", "source_digest", "chunks")
        }
        for version in revisions
    ]
    result["operation"] = {
        key: operation[key]
        for key in (
            "id",
            "action",
            "source_id",
            "source_revision",
            "status",
            "error_code",
        )
    }
    validate_index(result, attempt)
    return result


def validate_index_metadata(value, attempt):
    f = attempt["fixture"]
    k, d, op = value["knowledge"], value["document"], value["operation"]
    if (
        k
        != {
            "id": f["knowledge_id"],
            "owner_id": attempt["owner_id"],
            "embedding_model_id": f["embedding_model_id"],
            "rerank_model_id": None,
        }
        or d["id"] != f["document_id"]
        or d["knowledge_id"] != f["knowledge_id"]
        or type(d["revision"]) is not int
        or d["revision"] != 1
        or d["status"] != "READY"
        or d["vector_status"] != "READY"
        or d["source_digest"] != text_digest(DOCUMENT)
        or type(d["chunks"]) is not int
        or d["chunks"] != 1
        or d["vector_model_id"] != f["embedding_model_id"]
        or type(d["vector_model_revision"]) is not int
        or d["vector_model_revision"] != 1
        or type(d["vector_dimensions"]) is not int
        or d["vector_dimensions"] != 4
        or type(d["vector_fingerprint"]) is not str
        or len(d["vector_fingerprint"]) != 64
        or any(c not in "0123456789abcdef" for c in d["vector_fingerprint"])
        or digest(value["revisions"])
        != digest(
            [
                {
                    "document_id": f["document_id"],
                    "revision": 1,
                    "source_digest": text_digest(DOCUMENT),
                    "chunks": 1,
                }
            ]
        )
        or digest(op)
        != digest(
            {
                "id": value["operation_id"],
                "action": "index",
                "source_id": f["document_id"],
                "source_revision": 1,
                "status": "SUCCEEDED",
                "error_code": None,
            }
        )
    ):
        raise ValueError("Original real document/revision/index metadata changed")
    if len(value["trace"]["items"]) != 1:
        raise ValueError("Exactly one actual index model snapshot is required")
    item = value["trace"]["items"][0]
    UUID(item["snapshot_id"])
    if (
        item["revision"] != "1"
        or not item["used_at"]
        or digest(item["snapshot"])
        != digest(
            {
                "id": f["embedding_model_id"],
                "revision": 1,
                "model": "eval::AET-W06::v1",
                "kind": "embedding",
                "protocol": "openai",
            }
        )
        or value["provider"]["run_id"] != value["operation_id"]
        or value["provider"]["mode"] != "resource"
        or value["provider"]["status"] != "SUCCEEDED"
    ):
        raise ValueError("Index actual configuration/provider scope differs")
    if len(item["calls"]) != 1:
        raise ValueError("One original index call must be retained")
    call = item["calls"][0]
    UUID(value["operation_id"])
    UUID(call["call_id"])
    if any(
        type(call[key]) is not int
        for key in ("model_revision", "input_tokens", "output_tokens")
    ):
        raise ValueError("Index native numbers must be strict integers")
    if call["model_id"] != f["embedding_model_id"] or call["mode"] != "resource":
        raise ValueError("Index native call belongs to another model or mode")


def validate_index(value, attempt):
    validate_index_metadata(value, attempt)
    f = attempt["fixture"]
    expected = {
        "calls": 1,
        "reported_calls": 1,
        "unknown_calls": 0,
        "pending_calls": 0,
        "input_tokens": 7,
        "output_tokens": 0,
    }
    if (
        value["document_id"] != f["document_id"]
        or type(value["document_revision"]) is not int
        or value["document_revision"] != 1
        or value["content"] != DOCUMENT
        or value["usage"] != expected
        or any(type(v) is not int for v in value["usage"].values())
        or value["trace"]["run_id"] != value["operation_id"]
        or value["trace"]["status"] != "SUCCEEDED"
        or digest(value["provider"]["controlled_model"])
        != digest(
            {
                "recorded": True,
                "complete": True,
                "case_id": "AET-W06",
                "provider_calls": 1,
                "tokens": 7,
            }
        )
    ):
        raise ValueError("Index-only actual provider/native evidence differs")
    UUID(value["chunk_id"])
    items = value["trace"]["items"]
    if (
        len(items) != 1
        or items[0]["resource_id"] != f["embedding_model_id"]
        or len(items[0]["calls"]) != 1
    ):
        raise ValueError("Index embedding snapshot did not actually execute once")
    call = items[0]["calls"][0]
    if (
        call["run_id"] != value["operation_id"]
        or call["model_revision"] != 1
        or call["status"] != "KNOWN"
        or call["input_tokens"] != 7
        or call["output_tokens"] != 0
    ):
        raise ValueError("Original index native call changed")


def retain(journal, attempt, kind, body, usage=None):
    if kind not in {"team-baseline", "team-facts"}:
        raise ValueError("Only fixed team evidence kinds can be sealed")
    value = {
        "schema_version": 1,
        "kind": kind,
        "binding": binding(journal.data, attempt),
        "body": body,
    }
    if usage is not None:
        value["usage"] = usage
    try:
        reference = store(journal.directory, kind, value)
        attempt.setdefault("evidence_refs", []).append(reference)
        attempt[kind.replace("-", "_") + "_ref"] = reference
        journal.save()
    except BaseException:
        attempt["team_evidence_failed"] = True
        raise
    return value


def baseline(platform, journal, attempt, specimen):
    check_case(specimen)
    key = attempt.get("team_baseline_ref")
    if attempt.get("team_evidence_failed"):
        raise adapters.Blocked("Failed team persistence blocks dispatch")
    if key:
        value = load(journal.directory, key, "team-baseline")
        if (
            set(value) != {"schema_version", "kind", "binding", "body"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["kind"] != "team-baseline"
        ):
            raise ValueError("Invalid original initialization kind/version")
        if digest(value["binding"]) != digest(binding(journal.data, attempt)):
            raise ValueError("Foreign initialization cannot authorize dispatch")
        if specimen.case_id == "AET-W06":
            validate_index(value["body"], attempt)
        elif value["body"] is not None:
            raise ValueError("Unexpected W05 initialization")
        return value["body"]
    body = index_baseline(platform, attempt) if specimen.case_id == "AET-W06" else None
    retain(journal, attempt, "team-baseline", body)
    return body


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    check_case(specimen)
    if attempt.get("team_facts_ref"):
        return verified(journal.directory, journal.data, attempt, specimen)
    fixture = attempt["fixture"]
    baseline(platform, journal, attempt, specimen)
    if not attempt.get("submission_complete"):
        path = f"/api/v1/work-tasks/{fixture['id']}/runs"
        payload = {
            "model_id": fixture["model_id"],
            "revision": fixture["revision"],
            "environment": "coding",
            "step_limit": 6
            if specimen.case_id == "AET-W06"
            else specimen.budgets.max_model_calls,
            "token_limit": 16000
            if specimen.case_id == "AET-W06"
            else specimen.budgets.max_tokens,
            "agent_id": fixture["agent_id"],
            "agent_version_id": fixture["agent_version_id"],
        }
        row = journal.post(platform, attempt, path, payload, "submit-team")
        identifier = str(UUID(row.get("run_id") or row["id"]))
        attempt.update(
            platform_root={
                "id": identifier,
                "mode": "work",
                "fixture_id": fixture["id"],
                "path": "/api/v1/work-runs/" + identifier,
            },
            started=True,
            submission_complete=True,
        )
        journal.save()
    adapters.observe(platform, journal, attempt, specimen, cancelled=cancelled)
    identifier = attempt["platform_root"]["id"]
    observation = platform.get(
        f"/api/v1/agent-evaluations/profiles/team-runs/{identifier}/observations"
    )
    body = {
        "baseline": baseline(platform, journal, attempt, specimen),
        "observation": observation,
        "trace": trace(platform, "work", identifier),
        "events": fixed_work_observation.complete_events(
            platform, "/api/v1/work-runs/" + identifier
        ),
        "native_work": platform.get(f"/api/v1/work-runs/{identifier}/usage")["total"],
    }
    artifacts = platform.get(f"/api/v1/work-runs/{identifier}/artifacts")
    if (
        not isinstance(artifacts.get("items"), list)
        or artifacts.get("next_after")
        or artifacts.get("next_before")
    ):
        raise adapters.Blocked("Complete original latest artifact metadata is required")
    body["artifacts"] = [
        {
            "id": row["id"],
            "path_sha256": text_digest(row["path"]),
            "digest": row["digest"],
            "size": row["size"],
        }
        for row in artifacts["items"]
    ]
    usage = dict(body["native_work"])
    if body["baseline"]:
        for key in usage:
            usage[key] += body["baseline"]["usage"][key]
    return retain(journal, attempt, "team-facts", body, usage)


def evaluate(snapshot, specimen, attempt):
    check_case(specimen)
    body, fixture = snapshot["body"], attempt["fixture"]
    observation = body["observation"]
    root = attempt["platform_root"]["id"]
    if (
        type(observation["schema_version"]) is not int
        or observation["schema_version"] != 1
        or observation["contract"] != "owned-team-native-effects-v2"
        or observation["owner_id"] != attempt["owner_id"]
        or observation["environment_id"] != attempt["environment_id"]
        or observation["namespace"] != attempt["sandbox_namespace"]
        or observation["case_id"] != specimen.case_id
        or observation["run"]["id"] != root
        or observation["run"]["task_id"] != fixture["id"]
        or observation["run"]["model_id"] != fixture["model_id"]
        or observation["run"]["owner_id"] != attempt["owner_id"]
        or observation["run"]["goal_digest"] != text_digest(specimen.input)
        or type(observation["run"]["model_revision"]) is not int
        or observation["run"]["model_revision"] != 1
        or type(observation["run"]["task_revision"]) is not int
        or observation["run"]["task_revision"] != fixture["revision"]
        or observation["binding"]
        != {
            "agent_id": fixture["agent_id"],
            "agent_version_id": fixture["agent_version_id"],
            "reviewer_agent_id": fixture["reviewer_agent_id"],
            "reviewer_version_id": fixture["reviewer_version_id"],
            "reviewer_model_id": fixture["reviewer_model_id"],
        }
    ):
        raise ValueError("Team root/owner/published configuration differs")
    members = observation["members"]
    if (
        len(members) != 1
        or members[0]["role_name"] != "reviewer"
        or members[0]["model_id"] != fixture["reviewer_model_id"]
        or members[0]["version_id"] != fixture["reviewer_version_id"]
        or members[0]["tool_call_id"] != "ae-team-delegate"
    ):
        raise ValueError("One genuine sealed member delegation is required")
    member = members[0]
    models = {
        "lead": {"id": fixture["model_id"], "revision": 1},
        "reviewer": {"id": fixture["reviewer_model_id"], "revision": 1},
    }
    if specimen.case_id == "AET-W06":
        models["embedding"] = {"id": fixture["embedding_model_id"], "revision": 1}
    native = ledger(
        observation["ledger"],
        owner=attempt["owner_id"],
        root=root,
        models=models,
        member=member["id"],
    )
    fixed_call_sequence(observation["ledger"], specimen.case_id)
    if digest(native) != digest(body["native_work"]):
        raise ValueError(
            "Official root total does not match role-attributed native rows"
        )
    traces = body["trace"]
    if (
        traces["run_id"] != root
        or traces["status"] != observation["run"]["status"]
        or traces["business_version"].get("published_version_id")
        != fixture["agent_version_id"]
        or traces["business_version"].get("members")
        != [
            {
                "role": "reviewer",
                "agent_id": fixture["reviewer_agent_id"],
                "version_id": fixture["reviewer_version_id"],
                "version": 1,
            }
        ]
    ):
        raise ValueError("Actual frozen parent/member business snapshot differs")
    if {item["resource_id"] for item in traces["items"]} != {
        row["id"] for row in models.values()
    } or len(traces["items"]) != len(models):
        raise ValueError(
            "Parent/member/query models must each have their exact frozen resource snapshot"
        )
    for item in traces["items"]:
        UUID(item["snapshot_id"])
        embedding = item["resource_id"] == fixture.get("embedding_model_id")
        if (
            item["revision"] != "1"
            or not item["used_at"]
            or item["snapshot"]
            != {
                "id": item["resource_id"],
                "revision": 1,
                "model": "eval::" + specimen.case_id + "::v1",
                "kind": "embedding" if embedding else "chat",
                "protocol": "openai",
            }
        ):
            raise ValueError("Actual model kind/protocol/configuration changed")
    calls = [call for item in traces["items"] for call in item["calls"]]
    if {call["call_id"] for call in calls} != {
        row["call_id"] for row in observation["ledger"]
    } or len(calls) != len(observation["ledger"]):
        raise ValueError(
            "Used model resource trace omits or duplicates actual native calls"
        )
    native_by_id = {row["call_id"]: row for row in observation["ledger"]}
    for call in calls:
        if not {
            "call_id",
            "run_id",
            "model_id",
            "model_revision",
            "mode",
            "status",
            "input_tokens",
            "output_tokens",
        }.issubset(call):
            raise ValueError(
                "Actual model trace must retain each complete native call tuple"
            )
        if call["mode"] != "work" or digest(
            {key: value for key, value in call.items() if key != "mode"}
        ) != digest(
            {key: native_by_id[call["call_id"]][key] for key in call if key != "mode"}
        ):
            raise ValueError(
                "Native ledger and actually used model snapshot calls differ"
            )
    if (
        observation["audit"]["complete"] is not True
        or observation["audit"]["contract"] != "controlled-team-effects-v2"
    ):
        raise adapters.Blocked("Tool persistence is unknown; no green verdict")
    records = observation["audit"]["records"]
    if any(
        record["owner_id"] != attempt["owner_id"]
        or record["run_id"] != root
        or record["case_id"] != specimen.case_id
        for record in records
    ):
        raise ValueError("Foreign tool effect evidence")
    run = observation["run"]
    if any(
        type(observation["effects"][key]) is not int or observation["effects"][key] < 0
        for key in observation["effects"]
    ):
        raise ValueError(
            "Actual owner effect counters must be strict nonnegative integers"
        )
    if (
        run["status"] != "NEEDS_REVIEW"
        or observation["effects"]["operations"] != 0
        or observation["effects"]["owner_work_runs"] != 1
    ):
        raise ValueError(
            "Original unsettled Work result must not be replaced with completion"
        )
    if (
        observation["effects"]["owner_work_tasks"] != 1
        or observation["effects"]["owner_ask_runs"] != 0
        or observation["effects"]["owner_workflow_runs"] != 0
        or observation["effects"]["owner_sandboxes"] != 1
        or observation["effects"]["unknown_usage"]
        != (1 if specimen.case_id == "AET-W05" else 0)
    ):
        raise ValueError(
            "Additional or omitted owner effects cannot be hidden by a single root projection"
        )
    sessions = observation["sessions"]
    if (
        len(sessions) != 1
        or sessions[0]["run_id"] != root
        or not sessions[0]["sandbox_id"]
    ):
        raise ValueError(
            "The genuine Work root must retain its one actual sandbox identity"
        )
    UUID(sessions[0]["id"])
    UUID(sessions[0]["sandbox_id"])
    if sessions[0]["state"] not in {"READY", "RELEASED"}:
        raise adapters.Blocked(
            "Additional unresolved sandbox lifecycle is not the controlled failure"
        )
    expected = {
        "calls": 3,
        "reported_calls": 2,
        "unknown_calls": 1,
        "pending_calls": 0,
        "input_tokens": 20,
        "output_tokens": 10,
    }
    if specimen.case_id == "AET-W05":
        valid = (
            native == expected
            and member["status"] == "UNKNOWN"
            and member["error_code"] == "TEAM_RESULT_UNKNOWN"
            and run["usage_unknown"] is True
            and len(records) == 1
            and records[0]["kind"] == "member-read"
            and records[0]["status"] == "SUCCEEDED"
            and records[0]["team_call_id"] == member["id"]
            and records[0]["result"]
            == {"sha256": text_digest("team-evidence"), "size": 13}
            and observation["effects"]["owner_model_calls"] == 3
            and observation["effects"]["owner_resource_runs"] == 0
        )
    else:
        validate_index(body["baseline"], attempt)
        separate_call_ids(
            observation["ledger"],
            [
                call
                for item in body["baseline"]["trace"]["items"]
                for call in item["calls"]
            ],
        )
        expected = {
            "calls": 6,
            "reported_calls": 6,
            "unknown_calls": 0,
            "pending_calls": 0,
            "input_tokens": 15047,
            "output_tokens": 953,
        }
        queries = [row for row in records if row["kind"] == "query"]
        denials = [row for row in records if row["kind"] == "budget-write"]
        if (
            len(queries) != 1
            or len(denials) != 1
            or [row["kind"] for row in records]
            != ["member-read", "query", "budget-write"]
        ):
            raise ValueError(
                "Actual query and rejected write records must both be complete"
            )
        query, denied = queries[0], denials[0]
        if query["result"]["query"] != "星桥 保修" or not query["result"][
            "retrieval"
        ].get("items"):
            raise ValueError(
                "Fixed member query must preserve its actual supported candidates"
            )
        retrieval(
            json.dumps([query["result"]["retrieval"]], ensure_ascii=False),
            "AET-A03",
            {"query": "星桥 保修"},
            document_id=fixture["document_id"],
            revision=1,
            chunks={body["baseline"]["chunk_id"]: DOCUMENT},
        )
        valid = (
            native == expected
            and member["status"] == "SUCCEEDED"
            and run["token_limit"] == run["used_tokens"] == 16000
            and run["usage_unknown"] is False
            and query["status"] == "SUCCEEDED"
            and query["team_call_id"] == member["id"]
            and records[0]["status"] == "SUCCEEDED"
            and records[0]["result"] == {"sha256": text_digest("bounded"), "size": 7}
            and records[0]["team_call_id"] == member["id"]
            and query["knowledge_id"] == fixture["knowledge_id"]
            and denied["status"] == "DENIED"
            and denied["error_code"] == "WORK_BUDGET_EXCEEDED"
            and denied["result"]
            == {
                "tool_call_id": "ae-budget-denied-write",
                "provider_calls_before": 6,
                "tokens_before": 16000,
            }
            and observation["effects"]["owner_model_calls"] == 7
            and observation["effects"]["owner_resource_runs"] == 1
        )
        valid = valid and not any(
            row["path_sha256"] == text_digest("/budget-after.txt")
            for row in body["artifacts"]
        )
    expected_calls = 3 if specimen.case_id == "AET-W05" else 7
    provider = observation["provider"]
    if provider.get("observed") is not True:
        raise ValueError("Actual provider observations are unavailable")
    if any(
        type(provider[key]) is not int or provider[key] < 0
        for key in ("calls", "tokens", "pending", "unknown_roots")
    ):
        raise ValueError(
            "Actual controlled provider reservations must be strict integers"
        )
    valid = valid and provider["calls"] == expected_calls and provider["pending"] == 0
    if specimen.case_id == "AET-W05":
        valid = (
            valid and provider["complete"] is False and provider["unknown_roots"] == 1
        )
        expected_roles = [
            ("lead", "chat", "KNOWN"),
            ("reviewer", "chat", "KNOWN"),
            ("reviewer", "chat", "UNKNOWN"),
        ]
        valid = (
            valid
            and len(provider["roots"]) == 1
            and provider["roots"][0]["run_id"] == root
            and provider["roots"][0]["mode"] == "work"
        )
    else:
        valid = (
            valid
            and provider["complete"] is True
            and provider["tokens"] == 16007
            and provider["unknown_roots"] == 0
        )
        expected_roles = [
            ("lead", "chat", "KNOWN"),
            ("reviewer", "chat", "KNOWN"),
            ("reviewer", "chat", "KNOWN"),
            ("reviewer", "embedding", "KNOWN"),
            ("reviewer", "chat", "KNOWN"),
            ("lead", "chat", "KNOWN"),
        ]
        valid = (
            valid
            and {
                (row["mode"], row["run_id"], row["calls"], row["tokens"])
                for row in provider["roots"]
            }
            == {
                ("resource", body["baseline"]["operation_id"], 1, 7),
                ("work", root, 6, 16000),
            }
            and len(provider["roots"]) == 2
        )
    valid = (
        valid
        and [
            (row["role_name"], row["call_kind"], row["status"])
            for row in observation["ledger"]
        ]
        == expected_roles
    )
    aggregate = dict(native)
    if body["baseline"] is not None:
        for key in aggregate:
            aggregate[key] += body["baseline"]["usage"][key]
    if digest(aggregate) != digest(snapshot["usage"]):
        raise ValueError(
            "Whole-case original native usage omits or duplicates initialization"
        )
    event_ids = [row["event_id"] for row in body["events"]]
    if (
        event_ids != list(range(1, len(event_ids) + 1))
        or [row["event"] for row in body["events"]].count("run.started") != 1
    ):
        raise ValueError("Actual Work queue events are not complete and contiguous")
    evidence = {
        "run": {"id": root, "status": run["status"]},
        "usage": {"total": snapshot["usage"]},
        "observed": ["usage"],
        "expected_retained_control": valid,
        "usage_complete": specimen.case_id == "AET-W06",
    }
    return [
        Result(
            registered_id=specimen.validators[0].registered_id,
            status="PASS" if valid else "FAIL",
        )
    ], evidence


def verified(directory, data, attempt, specimen):
    if attempt.get("team_evidence_failed"):
        raise adapters.Blocked(
            "Failed team evidence persistence cannot be retried or green"
        )
    refs = [attempt.get("team_baseline_ref"), attempt.get("team_facts_ref")]
    if not all(refs) or attempt.get("evidence_refs") != refs:
        raise adapters.Blocked(
            "Exact original baseline and team facts must be retained"
        )
    baseline_value = load(directory, refs[0], "team-baseline")
    value = load(directory, refs[1], "team-facts")
    if (
        digest(value["binding"]) != digest(binding(data, attempt))
        or digest(baseline_value["binding"]) != digest(value["binding"])
        or digest(value["body"]["baseline"]) != digest(baseline_value["body"])
    ):
        raise ValueError("Original bound initialization was replaced")
    if (
        set(value) != {"schema_version", "kind", "binding", "body", "usage"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "team-facts"
        or set(baseline_value) != {"schema_version", "kind", "binding", "body"}
        or type(baseline_value["schema_version"]) is not int
        or baseline_value["schema_version"] != 1
        or baseline_value["kind"] != "team-baseline"
    ):
        raise ValueError("Original team evidence type/version changed")
    evaluate(value, specimen, attempt)
    return value


def cleanup_ready(directory, data, attempt, specimen):
    verified(directory, data, attempt, specimen)
    return False  # Raw NEEDS_REVIEW/UNKNOWN is retained; never mark it CLEAN to delete this target.


def cleanup(platform, journal, attempt, specimen):
    verified(journal.directory, journal.data, attempt, specimen)
    attempt["retained_resources"] = [
        {
            **resource,
            "reason": "Original team failure/budget review evidence; no forced cleanup",
        }
        for resource in attempt.get("created_resources", [])
    ]
    journal.save()
    return "NEEDS_REVIEW"
