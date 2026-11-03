"""Bounded A02 facts retained independently of the disposable platform database."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from . import adapters
from .contracts import Result
from .journal import atomic_json, digest

SHA = re.compile(r"[0-9a-f]{64}")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_path(directory: Path, relative: str) -> Path:
    """Only canonical relative files inside this result directory are evidence."""
    parts = PurePosixPath(relative).parts
    if (not parts or "\\" in relative or ":" in relative or relative != "/".join(parts)
            or any(part in {".", ".."} for part in parts) or PurePosixPath(relative).is_absolute()):
        raise ValueError("Unsafe evidence path")
    base = directory.resolve()
    path = base
    for part in parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise ValueError("Evidence links are forbidden")
    if not path.resolve().is_relative_to(base):
        raise ValueError("Evidence escaped the result directory")
    return path


def store(directory: Path, kind: str, value: dict) -> dict:
    relative = f"evidence/{kind}/{uuid4()}.json"
    path = artifact_path(directory, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value)
    return {"schema_version": 1, "kind": kind, "path": relative, "sha256": file_digest(path)}


def load(directory: Path, reference: dict, kind: str) -> dict:
    if (set(reference) != {"schema_version", "kind", "path", "sha256"}
            or type(reference["schema_version"]) is not int or reference["schema_version"] != 1
            or reference["kind"] != kind or not SHA.fullmatch(reference["sha256"])):
        raise ValueError("Invalid evidence reference")
    path = artifact_path(directory, reference["path"])
    with path.open("rb") as stream:
        payload = stream.read(131073)
    if len(payload) > 131072 or hashlib.sha256(payload).hexdigest() != reference["sha256"]:
        raise ValueError("Missing, oversized or changed evidence")
    return json.loads(payload.decode("utf-8"))


def binding(data: dict, attempt: dict) -> dict:
    context = data["suite_context"]
    return {key: data[key] for key in ("environment_id", "owner_id", "target_digest")} | {
        "attempt_id": attempt["attempt_id"], "case_id": attempt["case_id"],
        "repeat_index": context["repeat_index"], "suite_id": context["suite_id"],
        **{key: context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id", "candidate_commit")}}


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def message_projection(row: dict) -> dict:
    role = row["role"]
    if role not in {"user", "assistant", "tool", "system"}:
        raise ValueError("Unrecognized message role")
    identifier = str(UUID(str(row["run_id"])))
    content = row.get("content", {})
    text = content.get("text") if role == "user" else content.get("data", {}).get("content")
    if role in {"user", "assistant"} and not isinstance(text, str):
        raise ValueError("Official message text is unavailable")
    if text is not None and (not isinstance(text, str) or len(text) > 4096):
        raise ValueError("Message exceeds this fixed profile's observation bound")
    output = None
    if role == "assistant" and text is not None:
        try:
            output = json.loads(text, parse_constant=lambda _: None)
        except ValueError:
            pass
    declared = output if (isinstance(output, dict) and set(output) == {"project", "people"}
                          and output["project"] == "星桥" and type(output["people"]) is int
                          and 0 <= output["people"] <= 10) else None
    return {"role": role, "run_id": identifier, "text_sha256": text_digest(text) if text is not None else None,
            "output_digest": digest(output) if output is not None else None, "declared_output": declared,
            "has_tools": role == "tool" or bool(content.get("data", {}).get("tool_calls"))}


def capture(platform, journal, attempt: dict, specimen, evidence: dict) -> dict:
    """Save only declared fictional facts, identifiers, counters and hashes; no tool payloads."""
    references = [*attempt.get("prior_run_refs", {}).values(), attempt["platform_root"]]
    runs = []
    for reference in references:
        identifier = str(UUID(reference["id"]))
        if reference["mode"] != "ask" or reference["path"] != "/api/v1/runs/" + identifier:
            raise ValueError("Unexpected A02 run reference")
        row = adapters.read_run(platform, reference)
        if str(row.get("id")) != identifier:
            raise ValueError("Official run identity is missing")
        runs.append({"id": identifier, "status": row["status"], "source": reference["path"]})
    root = references[-1]
    observed = platform.get("/api/v1/agent-evaluations/profiles/runs/ask/" + root["id"] + "/observations")
    keys = ("mode", "run_id", "case_id", "role", "provider_calls", "human_turns", "human_context_digest",
            "has_project_context", "has_update_to_three")
    assertions = observed["context_assertions"]
    if not isinstance(assertions, list) or len(assertions) > 8:
        raise ValueError("Context observation exceeds the fixed profile bound")
    messages = evidence["messages"]
    if "messages" not in evidence["observed"] or len(messages["items"]) > 16:
        raise ValueError("Complete bounded official messages are required")
    model = observed["controlled_model"]
    snapshot = {"schema_version": 1, "kind": "a02-facts", "binding": binding(journal.data, attempt),
                "scope": "formal HTTP facts; not SSE, hidden reasoning or a complete trajectory",
                "runs": runs, "final_run_id": root["id"], "messages_complete": True,
                "messages": [message_projection(row) for row in messages["items"]],
                "observer": {"mode": observed["mode"], "run_id": str(observed["run_id"]),
                             "status": observed["status"],
                             "controlled_model": {key: model[key] for key in ("recorded", "complete", "case_id", "provider_calls", "tokens")},
                             "context_assertions": [{key: row[key] for key in keys} for row in assertions]},
                "usage": {key: adapters.known_usage(evidence, specimen)[key] for key in (
                    "calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens")}}
    validate_projection(snapshot)
    reference = store(journal.directory, "a02-facts", snapshot)
    attempt.setdefault("evidence_refs", []).append(reference)
    journal.save()  # The reference is durable before any grading or cleanup.
    return verified(journal.directory, journal.data, attempt, specimen)


def verified(directory: Path, data: dict, attempt: dict, specimen) -> dict:
    references = attempt.get("evidence_refs", [])
    if not references:
        raise ValueError("A02 facts were not retained")
    expected = binding(data, attempt)
    for reference in references:
        snapshot = load(directory, reference, "a02-facts")
        if (type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1
                or snapshot.get("kind") != "a02-facts" or snapshot.get("binding") != expected):
            raise ValueError("Fact snapshot identity changed")
        validate_projection(snapshot)
    # Recompute from the last immutable observation; all earlier observations remain verified.
    evaluate(snapshot, specimen, attempt)
    return snapshot


def validate_projection(snapshot: dict) -> None:
    """Reject invalid or expanded projections even when someone supplies a fresh file hash."""
    if set(snapshot) != {"schema_version", "kind", "binding", "scope", "runs", "final_run_id", "messages_complete",
                         "messages", "observer", "usage"}:
        raise ValueError("Unexpected factual fields")
    for row in snapshot["messages"]:
        if (set(row) != {"role", "run_id", "text_sha256", "output_digest", "declared_output", "has_tools"}
                or row["role"] not in {"user", "assistant", "tool", "system"} or type(row["has_tools"]) is not bool):
            raise ValueError("Invalid message projection")
        UUID(row["run_id"])
        for key in ("text_sha256", "output_digest"):
            if row[key] is not None and not SHA.fullmatch(row[key]):
                raise ValueError("Invalid message digest")
        declared = row["declared_output"]
        if declared is not None and (set(declared) != {"project", "people"} or declared["project"] != "星桥"
                                     or type(declared["people"]) is not int or not 0 <= declared["people"] <= 10
                                     or digest(declared) != row["output_digest"]):
            raise ValueError("Invalid declared fictional output")
    for row in snapshot["runs"]:
        if set(row) != {"id", "status", "source"} or row["status"] not in adapters.TERMINAL:
            raise ValueError("Invalid official terminal observation")
        UUID(row["id"])
    observer = snapshot["observer"]
    if (set(observer) != {"mode", "run_id", "status", "controlled_model", "context_assertions"}
            or observer["mode"] != "ask" or observer["status"] not in adapters.TERMINAL):
        raise ValueError("Unexpected context observation fields")
    UUID(observer["run_id"])
    model = observer["controlled_model"]
    if (set(model) != {"recorded", "complete", "case_id", "provider_calls", "tokens"}
            or type(model["recorded"]) is not bool or type(model["complete"]) is not bool
            or model["case_id"] != "AET-A02" or type(model["provider_calls"]) is not int
            or not 0 <= model["provider_calls"] <= 1 or type(model["tokens"]) is not int or not 0 <= model["tokens"] <= 15):
        raise ValueError("Invalid controlled model observation")
    for row in observer["context_assertions"]:
        if (set(row) != {"mode", "run_id", "case_id", "role", "provider_calls", "human_turns", "human_context_digest",
                         "has_project_context", "has_update_to_three"}
                or type(row["human_turns"]) is not int or type(row["provider_calls"]) is not int
                or not SHA.fullmatch(row["human_context_digest"])
                or row["mode"] != "ask" or row["case_id"] != "AET-A02" or row["role"] != "lead"
                or not 0 <= row["human_turns"] <= 2 or not 0 <= row["provider_calls"] <= 1
                or type(row["has_project_context"]) is not bool or type(row["has_update_to_three"]) is not bool):
            raise ValueError("Invalid bounded context assertion")
        UUID(row["run_id"])
    if set(snapshot["usage"]) != {"calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens"}:
        raise ValueError("Unexpected native usage fields")


def evaluate(snapshot: dict, specimen, attempt: dict) -> tuple[list[Result], dict]:
    runs = snapshot["runs"]
    expected_runs = [row["id"] for row in attempt.get("prior_run_refs", {}).values()] + [attempt["platform_root"]["id"]]
    if (len(runs) != 2 or [row["id"] for row in runs] != expected_runs
            or snapshot["final_run_id"] != expected_runs[-1]
            or any(row["source"] != "/api/v1/runs/" + row["id"] for row in runs)
            or snapshot["messages_complete"] is not True):
        raise ValueError("Incomplete or mismatched official run observations")
    messages = snapshot["messages"]
    if len(messages) > 16 or any(row["run_id"] not in expected_runs for row in messages):
        raise ValueError("Messages do not belong to the observed roots")
    humans = [row for row in messages if row["role"] == "user"]
    declared = specimen.fixture_parameters["fixed_inputs"]["messages"]
    valid_inputs = len(humans) == 2 and all(
        sum(row["run_id"] == identifier and row["text_sha256"] == text_digest(text) for row in humans) == 1
        for identifier, text in zip(expected_runs, declared, strict=True))
    answers = [row for row in messages if row["role"] == "assistant" and row["run_id"] == expected_runs[-1]]
    answer = bool(answers) and answers[-1]["output_digest"] == digest({"project": "星桥", "people": 3})
    observer = snapshot["observer"]
    model = observer["controlled_model"]
    context_digest = text_digest(json.dumps(declared, ensure_ascii=False))
    assertions = [row for row in observer["context_assertions"] if row["mode"] == "ask"
                  and row["run_id"] == expected_runs[-1] and row["case_id"] == specimen.case_id and row["role"] == "lead"]
    if not assertions:
        raise adapters.Blocked("A matching root context assertion is unavailable")
    context = (observer["mode"] == "ask" and observer["run_id"] == expected_runs[-1]
               and observer["status"] == runs[-1]["status"] and model["recorded"] is True
               and model["complete"] is True and model["case_id"] == specimen.case_id
               and type(model["provider_calls"]) is int and model["provider_calls"] == 1
               and any(row["mode"] == "ask" and row["run_id"] == expected_runs[-1]
                       and row["case_id"] == specimen.case_id and row["role"] == "lead"
                       and type(row["human_turns"]) is int and row["human_turns"] == 2
                       and row["human_context_digest"] == context_digest
                       and row["has_project_context"] is True and row["has_update_to_three"] is True
                       for row in assertions))
    facts = [Result(registered_id=name, status="PASS" if passed else "FAIL") for name, passed in (
        ("user_message_count", valid_inputs), ("output_equals", answer), ("received_context", context))]
    tools = snapshot["messages_complete"] is True and not any(row["has_tools"] for row in messages)
    evidence = {"tools_complete": tools, "tools": [], "run": {"status": runs[-1]["status"]},
                "usage": {"total": snapshot["usage"]}, "observed": ["usage"]}
    adapters.known_usage(evidence, specimen)
    if snapshot["usage"]["calls"] != 2:
        raise ValueError("Two controlled root calls must be present in native usage")
    if any(row["status"] not in {"SUCCEEDED", "COMPLETED"} for row in runs):
        facts.append(Result(registered_id="terminal_status", status="FAIL"))
    return facts, evidence
