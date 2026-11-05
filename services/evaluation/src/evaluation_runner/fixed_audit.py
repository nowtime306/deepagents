"""Append-only command receipts and a last-written report integrity receipt."""

import json
import os
from pathlib import Path

from . import network
from .contracts import Case
from .fixed_evidence import artifact_path, file_digest, load, store
from .journal import Journal, atomic_json, digest
from .reports import write_report

INDEX = "network-audit-index.json"
READY = "report-ready.json"
PARTIAL = "partial-report.json"
FILES = ("report.json", "results.xml", "report.md")


def index(directory: Path) -> dict:
    path = directory / INDEX
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "sessions": []}
    if (set(data) != {"schema_version", "sessions"} or type(data["schema_version"]) is not int
            or data["schema_version"] != 1 or not isinstance(data["sessions"], list)):
        raise ValueError("Invalid audit index")
    return data


def begin(directory: Path, session: network.Session) -> None:
    data = index(directory)
    data["sessions"].append({"session_id": session.data["session_id"], "status": "PENDING"})
    atomic_json(directory / INDEX, data)
    (directory / READY).unlink(missing_ok=True)
    (directory / PARTIAL).unlink(missing_ok=True)


def seal(directory: Path, session: network.Session, status: str) -> dict:
    session.data.update(status=status, ended_at=network.now(), policy_digest=digest(session.data["policy"]))
    session.closed = True  # Final local publication is forbidden from opening even a target socket.
    reference = store(directory, "network-audit", session.data)
    data = index(directory)
    row = next(row for row in data["sessions"] if row["session_id"] == session.data["session_id"])
    row.update(status="SEALED", reference=reference)
    atomic_json(directory / INDEX, data)
    return reference


def validate(directory: Path, data: dict, *, pending_session: str | None = None) -> list[dict]:
    sessions = index(directory)["sessions"]
    values, references, pending = {}, [], set()
    for row in sessions:
        if row["status"] == "PENDING" and row["session_id"] == pending_session:
            pending.add(row["session_id"])
            continue
        if row["status"] != "SEALED":
            raise ValueError("An audit session was not sealed")
        value = load(directory, row["reference"], "network-audit")
        if (value["session_id"] != row["session_id"] or value["guard_enabled"] is not True
                or value["status"] not in {"COMPLETE", "ERROR"} or not value["ended_at"]
                or value["policy_digest"] != digest(value["policy"])):
            raise ValueError("Invalid audit receipt")
        if (any(set(event) != {"phase", "event", "allowed"} or type(event["allowed"]) is not bool
                or event["event"] not in {"socket.connect", "socket.getaddrinfo"} for event in value["events"])
                or any(phase["status"] not in {"COMPLETE", "ERROR"} or not phase["ended_at"] for phase in value["phases"])):
            raise ValueError("Invalid or unfinished audit phase")
        if row["session_id"] in values:
            raise ValueError("Duplicate audit session")
        values[row["session_id"]] = value
        references.append(row["reference"])
    for attempt in data.get("attempts", []):
        if not attempt.get("started"):
            continue
        identifier = attempt.get("run_audit_session_id")
        if identifier in pending:
            continue
        value = values.get(identifier)
        identity = value["identity"] if value else {}
        specimen = Case.model_validate(attempt["case_snapshot"])
        required = {"execution", "export"} | ({"import", "metric"} if specimen.metrics else set())
        phases = {row["name"] for row in value["phases"] if row["status"] == "COMPLETE"} if value else set()
        if (not value or value["action"] != "run" or value["status"] != "COMPLETE"
                or not required.issubset(phases) or any(not event["allowed"] for event in value["events"])
                or identity.get("environment_id") != data["environment_id"]
                or identity.get("owner_id") != data["owner_id"]
                or identity.get("target_digest") != data["target_digest"]
                or identity.get("case_id") != attempt["case_id"]
                or identity.get("catalog_digest") != data["dataset_digest"]
                or identity.get("case_definition_digest") != data["case_definition_digest"]
                or identity.get("candidate_commit") != data["suite_context"]["candidate_commit"]
                or identity.get("suite_id") != data["suite_context"]["suite_id"]
                or type(identity.get("repeat_index")) is not int or identity.get("repeat_index") != attempt["repeat_index"]):
            raise ValueError("Complete identity-bound run/import/metric/export audit is required")
    if any(reference not in references for reference in data.get("network_audit_refs", [])):
        raise ValueError("A previously referenced audit disappeared")
    return references


def export_identity(data: dict) -> dict:
    context = data["suite_context"]
    return {key: data[key] for key in ("environment_id", "owner_id", "target_digest", "case_definition_digest")} | {
        "catalog_digest": data["dataset_digest"],
        **{key: context[key] for key in ("case_id", "rules_id", "candidate_commit", "suite_id", "repeat_index")}}


def publish(directory: Path, journal, session: network.Session) -> dict:
    """Stage, seal the command audit, then publish three files and their final receipt."""
    for key, value in export_identity(journal.data).items():
        identity = session.data["identity"]
        if key in identity and (type(identity[key]) is not type(value) or identity[key] != value):
            raise ValueError("Export session identity differs from the recorded execution")
        identity[key] = value
    staging = directory / (".export-" + session.data["session_id"])
    staging.mkdir()
    with network.phase("export"):
        write_report(staging, journal.data,evidence_directory=directory)
    seal(directory, session, "COMPLETE")
    references = validate(directory, journal.data)
    journal.data["network_audit_refs"] = references
    for attempt in journal.data.get("attempts", []):
        attempt["network_audit_refs"] = references
    journal.save()
    # Regenerate references locally after sealing. The audit hook now rejects all sockets.
    report = write_report(staging, journal.data,evidence_directory=directory)
    ready = {"schema_version": 1, "session_id": session.data["session_id"], "gate": report["gate"],
             "environment_id": journal.data["environment_id"], "dataset_digest": journal.data["dataset_digest"],
             "owner_id": journal.data["owner_id"], "target_digest": journal.data["target_digest"],
             "case_definition_digest": journal.data["case_definition_digest"], "suite_context": journal.data["suite_context"],
             "network_audit_refs": references,
             "files": {name: file_digest(staging / name) for name in FILES}}
    (directory / READY).unlink(missing_ok=True)
    for name in FILES:
        os.replace(staging / name, directory / name)
    atomic_json(directory / READY, ready)  # No cross-file atomicity is claimed.
    staging.rmdir()
    return report


def publish_error(directory: Path, data: dict, session: network.Session, error_type: str) -> None:
    """Save current diagnostic state without replacing accepted reports or issuing readiness."""
    (directory / READY).unlink(missing_ok=True)
    attempts = data.get("attempts", [])
    atomic_json(directory / PARTIAL, {
        "schema_version": 1, "publication_status": "INCOMPLETE", "acceptance_ready": False,
        "gate": "FAIL" if any(row.get("task_outcome") == "FAIL" for row in attempts) else "NEEDS_REVIEW",
        "action": session.data["action"], "audit_session_id": session.data["session_id"],
        "error_type": error_type,
        **{key: data.get(key) for key in ("environment_id", "owner_id", "dataset_digest", "suite_context")},
        "attempts": attempts,
        "network_audit_refs": [row["reference"] for row in index(directory)["sessions"] if row["status"] == "SEALED"],
    })


def validate_ready(directory: Path) -> dict:
    """Consumers must verify this receipt, not infer success from an isolated JSON/XML file."""
    journal = Journal(directory)  # Fixed commands hold this same lock while appending/publishing.
    try:
        return _validate_ready(directory)
    except FileNotFoundError as error:
        raise ValueError("Incomplete report publication") from error
    finally:
        journal.close()


def _validate_ready(directory: Path) -> dict:
    ready = json.loads((directory / READY).read_text(encoding="utf-8"))
    if type(ready.get("schema_version")) is not int or ready["schema_version"] != 1 or set(ready["files"]) != set(FILES):
        raise ValueError("Incomplete report receipt")
    for name, expected in ready["files"].items():
        if file_digest(artifact_path(directory, name)) != expected:
            raise ValueError("Partial or changed report publication")
    for reference in ready["network_audit_refs"]:
        load(directory, reference, "network-audit")
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    if (report["gate"] != ready["gate"] or report["environment_id"] != ready["environment_id"]
            or report["dataset_digest"] != ready["dataset_digest"]):
        raise ValueError("Report and final receipt differ")
    from .fixed_run import catalog, validate_offline_report

    data = {**report, **{key: ready[key] for key in ("owner_id", "target_digest", "case_definition_digest", "suite_context")},
            "network_audit_refs": ready["network_audit_refs"]}
    references = validate(directory, data)
    if (references != ready["network_audit_refs"]
            or any(attempt.get("network_audit_refs") != references for attempt in report["attempts"])):
        raise ValueError("Report receipt must cover the exact current audit index")
    sessions = index(directory)["sessions"]
    if not sessions or sessions[-1]["session_id"] != ready["session_id"]:
        raise ValueError("Report receipt is not the current command's export")
    session = load(directory, sessions[-1]["reference"], "network-audit")
    phases = {row["name"] for row in session["phases"] if row["status"] == "COMPLETE"}
    identity = session["identity"]
    if (session["status"] != "COMPLETE" or session["action"] not in {"init", "run", "cleanup", "report"}
            or not {"execution", "export"}.issubset(phases) or any(not row["allowed"] for row in session["events"])
            or any(type(identity.get(key)) is not type(value) or identity.get(key) != value
                   for key, value in export_identity(data).items())):
        raise ValueError("Report receipt lacks a complete identity-bound export")
    dataset = catalog(ready["suite_context"].get("profile_version", 1))
    specimen = next(row for row in dataset.cases if row.case_id == ready["suite_context"]["case_id"])
    validate_offline_report(data, specimen, dataset, directory=directory)
    if dataset.dataset_version == 2:
        from .profiles_v2.lifecycle import control_report
        expected = control_report(directory, data)
        if any(report.get(key) != value for key, value in expected.items()):
            raise ValueError("Sealed versioned control report differs from retained facts")
    return ready
