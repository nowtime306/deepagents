"""W01 formal observations and deterministic facts; callers must seal them before grading."""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from .check_schemas import Artifact
from .client import Platform
from .contracts import Result, StrictModel

Count = Annotated[int, Field(ge=0)]
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ArtifactMetadata(StrictModel):
    id: UUID
    path_sha256: Sha
    digest: Sha
    size: Count


class EventMetadata(StrictModel):
    event_id: int = Field(ge=1)
    event: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")


class OperationMetadata(StrictModel):
    id: UUID
    name: str = Field(max_length=128)
    status: Literal["APPROVED", "STARTED", "SUCCEEDED", "FAILED", "UNKNOWN"]


class NativeUsage(StrictModel):
    calls: Count
    reported_calls: Count
    pending_calls: Count
    unknown_calls: Count
    input_tokens: Count | None
    output_tokens: Count | None


class ProviderObservation(StrictModel):
    recorded: bool
    complete: bool
    case_id: Literal["AET-W01"]
    provider_calls: Count
    tokens: Count | None


class WorkObservation(StrictModel):
    schema_version: Literal[1] = 1
    case_id: Literal["AET-W01"] = "AET-W01"
    mode: Literal["work"] = "work"
    environment_id: UUID
    owner_id: UUID
    namespace: UUID
    run_id: UUID
    task_id: UUID
    environment: Literal["coding"]
    status: str = Field(max_length=32)
    snapshot_id: UUID | None
    artifacts_complete: bool
    artifacts: list[ArtifactMetadata] = Field(max_length=100)
    events_complete: bool
    events: list[EventMetadata] = Field(max_length=1000)
    operations_complete: bool
    operations: list[OperationMetadata] = Field(max_length=1000)
    usage: NativeUsage
    provider: ProviderObservation
    sandbox_complete: bool
    sandbox_states: dict[Annotated[str, Field(max_length=32)], Count] = Field(max_length=16)


def parse(value: dict) -> WorkObservation:
    """Validate strict JSON types, including UUIDs without accepting numeric booleans."""
    return WorkObservation.model_validate_json(json.dumps(value, allow_nan=False))


def complete_events(platform: Platform, path: str) -> list[dict]:
    """Read the native cursor until an explicit empty final page, without retaining event payloads."""
    cursor, events = 0, []
    for _ in range(11):
        page = platform.get(path + "/events", params={"after": cursor})
        rows, following = page["items"], page["next_after"]
        if not isinstance(rows, list) or len(rows) > 100 or type(following) is not int:
            raise ValueError("Invalid Work event page")
        if not rows:
            if following != cursor:
                raise ValueError("Unproven final Work event cursor")
            return events
        for row in rows:
            event = EventMetadata.model_validate({key: row[key] for key in ("event_id", "event")})
            if event.event_id != cursor + 1 or len(events) >= 1000:
                raise ValueError("Incomplete or oversized Work event history")
            cursor = event.event_id
            events.append(event.model_dump())
        if following != cursor:
            raise ValueError("Work event cursor differs from the last observed event")
    raise ValueError("Work event history was not completed")


def collect(platform: Platform, run_id: str, task_id: str) -> dict:
    """Project only owned W01 identifiers, digests, lifecycle markers and counters from formal GETs."""
    run_id, task_id = str(UUID(run_id)), str(UUID(task_id))
    proof = platform.attest()
    path = "/api/v1/work-runs/" + run_id
    run = platform.get(path)
    if (str(run["id"]) != run_id or str(run["task_id"]) != task_id
            or str(run["owner_id"]) != platform.target.owner_id):
        raise ValueError("Work root does not belong to this fixed target and task")
    artifacts, operations = platform.get(path + "/artifacts"), platform.get(path + "/operations")
    for page in (artifacts, operations):
        if not isinstance(page.get("items"), list) or page.get("next_after") or page.get("next_before"):
            raise ValueError("Complete official Work observations are required")
    projected = []
    for row in artifacts["items"]:
        virtual = Artifact(path=row["path"]).path
        projected.append({"id": row["id"], "path_sha256": hashlib.sha256(virtual.encode()).hexdigest(),
                          "digest": row["digest"], "size": row["size"]})
    observer = platform.get("/api/v1/agent-evaluations/profiles/runs/work/" + run_id + "/observations")
    if observer["mode"] != "work" or str(observer["run_id"]) != run_id or observer["status"] != run["status"]:
        raise ValueError("Work observation scope changed")
    usage = platform.get(path + "/usage")["total"]
    body = {"environment_id": proof["environment_id"], "owner_id": proof["owner_id"],
            "namespace": proof["sandbox_namespace"], "run_id": run_id, "task_id": task_id,
            "environment": run["environment"], "status": run["status"],
            "snapshot_id": (run.get("result") or {}).get("snapshot_id"),
            "artifacts_complete": True, "artifacts": projected,
            "events_complete": True, "events": complete_events(platform, path),
            "operations_complete": True,
            "operations": [{key: row[key] for key in ("id", "name", "status")} for row in operations["items"]],
            "usage": {key: usage[key] for key in NativeUsage.model_fields},
            "provider": {key: observer["controlled_model"][key] for key in ProviderObservation.model_fields},
            "sandbox_complete": observer["sandbox_sessions"]["complete"],
            "sandbox_states": observer["sandbox_sessions"]["states"]}
    return parse(body).model_dump(mode="json")


def evaluate(value: dict) -> list[Result]:
    """Recompute the negative control from sealed bytes; a completed run never proves a file exists."""
    observation = parse(value)
    completed = observation.status == "COMPLETED" and observation.snapshot_id is not None
    status = "PASS" if completed else "FAIL" if observation.status in {"FAILED", "CANCELLED"} else "BLOCKED"
    results = [Result(registered_id="work_completed_snapshot", status=status)]
    available = observation.artifacts_complete and completed
    expected = hashlib.sha256(b"/report.txt").hexdigest()
    present = any(row.path_sha256 == expected for row in observation.artifacts)
    results.append(Result(registered_id="artifact_exists", status=("PASS" if present else "FAIL") if available else "BLOCKED",
                          reason="Latest captured artifact metadata; Agent self-reports are not file evidence"))
    if present:
        results.append(Result(registered_id="w01_control", status="BLOCKED",
                              reason="Missing-file control did not occur; file content has not been evaluated"))
    names = [row.event for row in observation.events]
    ids = [row.event_id for row in observation.events]
    queue = (observation.events_complete and ids == list(range(1, len(ids) + 1))
             and names.count("run.started") == names.count("run.completed") == 1
             and names.index("run.started") < names.index("run.completed")
             and not {"run.failed", "run.cancelled"}.intersection(names))
    results.append(Result(registered_id="work_queue_lifecycle", status="PASS" if queue else "BLOCKED"))
    usage, provider = observation.usage, observation.provider
    known = (usage.calls > 0 and usage.calls == usage.reported_calls == provider.provider_calls
             and not usage.pending_calls and not usage.unknown_calls
             and usage.input_tokens is not None and usage.output_tokens is not None
             and provider.recorded and provider.complete
             and usage.input_tokens + usage.output_tokens == provider.tokens)
    results.append(Result(registered_id="work_known_usage", status="PASS" if known else "BLOCKED"))
    released = (observation.sandbox_complete and observation.sandbox_states.get("RELEASED") == 1
                and sum(observation.sandbox_states.values()) == 1)
    settled = observation.operations_complete and all(row.status in {"SUCCEEDED", "FAILED"}
                                                     for row in observation.operations)
    results.append(Result(registered_id="work_resources_released", status="PASS" if released and settled else "BLOCKED"))
    return results
