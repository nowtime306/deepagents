"""Read-only identity proof for explicitly provisioned, isolated evaluation instances."""

import json
import os
import re
import stat
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.errors import fail
from app.security import identity

router = APIRouter(prefix="/api/v1/agent-evaluations", tags=["Agent评测"])


class FixtureProfile(BaseModel):
    """A versioned, registered fixture profile; no executable configuration."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: int = Field(ge=1, strict=True)


class InstanceMarker(BaseModel):
    """Operator-provisioned identity bound to this dedicated test instance."""

    model_config = ConfigDict(extra="forbid")
    environment_id: UUID
    owner_id: UUID
    database_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,100}_test$")
    platform_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    sandbox_namespace: UUID
    fixture_profile: FixtureProfile


class ProfileLimits(BaseModel):
    """Limits enforced by the reviewed controlled-model factory, not by the client."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["ask", "work", "workflow"]
    max_model_calls: int = Field(ge=0, le=1000, strict=True)
    max_tokens: int = Field(ge=0, le=10_000_000, strict=True)


class ExecutionLimits(BaseModel):
    """Optional capability proof registered by trusted target bootstrap code."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1, strict=True)
    enforcement: Literal["controlled-model-factory-v1"]
    scope: Literal["resource-run-root"]
    profiles: dict[str, ProfileLimits] = Field(min_length=1, max_length=100)

    @field_validator("profiles")
    @classmethod
    def profile_ids(cls, value: dict[str, ProfileLimits]) -> dict[str, ProfileLimits]:
        if any(not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", key) for key in value):
            raise ValueError("Invalid controlled profile ID")
        return value


def read_marker(data):
    """Read a bounded regular file without accepting directory aliases or link ancestors."""
    path = data / "evaluation-instance.json"
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("Aliased instance marker")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Nonregular instance marker")
    with path.open("rb") as stream:
        opened, current = os.fstat(stream.fileno()), path.lstat()
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Invalid instance marker")
        content = stream.read(4097)
    if len(content) > 4096:
        raise ValueError("Oversized instance marker")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("Aliased instance marker")
    return InstanceMarker.model_validate(json.loads(content))


@router.get("/environment")
async def environment(request: Request) -> dict[str, object]:
    """Return no paths or credentials; default production instances have no usable endpoint."""
    settings = request.app.state.settings
    if not settings.evaluation_instance_id or not settings.evaluation_platform_commit:
        fail(404, "NOT_FOUND", "此实例未开放Agent评测")
    user = await identity(request)
    data = settings.data_dir
    if (not data.name.endswith("_test")
            or not re.fullmatch(r"[a-f0-9]{40}", settings.evaluation_platform_commit)):
        fail(503, "EVALUATION_ISOLATION_UNVERIFIED", "评测实例隔离信息未核实")
    try:
        marker = read_marker(data)
        configured_id = UUID(settings.evaluation_instance_id)
    except (OSError, ValueError, ValidationError, RecursionError):
        fail(503, "EVALUATION_ISOLATION_UNVERIFIED", "评测实例隔离信息未核实")
    db = request.app.state.db
    database = (await db.fetch("SELECT current_database() AS name"))[0]["name"]
    namespace = (await db.fetch("SELECT id FROM work_sandbox_namespace"))[0]["id"]
    if (marker.environment_id != configured_id or marker.platform_commit != settings.evaluation_platform_commit
            or marker.database_name != database or not database.endswith("_test")
            or marker.sandbox_namespace != namespace):
        fail(503, "EVALUATION_ISOLATION_UNVERIFIED", "评测实例隔离信息不匹配")
    if marker.owner_id != user["id"]:
        fail(403, "EVALUATION_OWNER_MISMATCH", "评测账号与隔离实例不匹配")
    migrations = await db.fetch("SELECT version FROM schema_migrations ORDER BY version")
    result = {"schema_version": 1, "environment_id": marker.environment_id, "owner_id": user["id"],
            "platform_commit": marker.platform_commit, "database_is_test": True,
            "schema_migrations": [row["version"] for row in migrations],
            "sandbox_namespace": namespace, "permissions": user["permissions"],
            "fixture_profile": marker.fixture_profile.model_dump()}
    proof = getattr(request.app.state, "evaluation_execution_limits", None)
    if proof is not None:
        try:
            result["execution_limits"] = ExecutionLimits.model_validate(proof).model_dump()
        except ValidationError:
            fail(503, "EVALUATION_LIMITS_UNVERIFIED", "评测执行上限未核实")
    return result


@router.get("/workflow-runs/{run_id}/usage")
async def workflow_usage(run_id: UUID, request: Request) -> dict[str, object]:
    """Observe standalone workflow root usage; embedded work must use its parent scope."""
    proof = await environment(request)
    from app.resource_trace_api import owned

    db, owner = request.app.state.db, proof["owner_id"]
    run = await owned(db, owner, "workflow", run_id)
    parents = await db.fetch(
        "SELECT run_id FROM work_run_workflows WHERE workflow_run_id=%s "
        "UNION SELECT k.parent_work_run FROM external_workflow_jobs j "
        "JOIN api_credentials k ON k.id=j.credential_id WHERE j.run_id=%s AND k.parent_work_run IS NOT NULL",
        (run_id, run_id),
    )
    if parents or (run.get("business") or {}).get("parent_ask_run"):
        fail(422, "EVALUATION_PARENT_USAGE_REQUIRED", "嵌入工作流请核对父运行用量，不能伪称独立用量")
    row = (await db.fetch(
        "SELECT count(*) AS calls,count(*) FILTER(WHERE status='KNOWN') AS reported_calls,"
        "count(*) FILTER(WHERE status='UNKNOWN' OR (status='PENDING' AND %s)) AS unknown_calls,"
        "count(*) FILTER(WHERE status='PENDING' AND NOT %s) AS pending_calls,"
        "sum(input_tokens) FILTER(WHERE status='KNOWN') AS input_tokens,"
        "sum(output_tokens) FILTER(WHERE status='KNOWN') AS output_tokens "
        "FROM model_usage WHERE owner_id=%s AND mode='workflow' AND run_id=%s",
        (run["status"] not in {"PENDING", "RUNNING"}, run["status"] not in {"PENDING", "RUNNING"}, owner, run_id),
    ))[0]
    for key in ("input_tokens", "output_tokens"):
        row[key] = int(row[key]) if row[key] is not None else (0 if not row["calls"] else None)
    return {"total": row, "scope": {"mode": "workflow", "run_id": run_id},
            "complete": run["status"] not in {"PENDING", "RUNNING"}}
