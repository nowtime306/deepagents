"""Agent evaluation metadata APIs. No worker, subprocess or report-upload endpoint is installed here."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import ValidationError

from app.agent_evaluation_platform_admission import THRESHOLDS, checked_report, service, validate_version_snapshot
from app.agent_evaluation_platform_queue import authorize
from app.agent_evaluation_platform_store import Store
from app.agent_evaluation_platform_types import (
    DatasetInput,
    Definition,
    PublishInput,
    ReviewInput,
    RevisionInput,
    RunInput,
    coverage,
    default_budget,
    digest,
    duplicates,
    redact,
)
from app.errors import APIError, fail
from app.permissions import require_account
from app.security import identity

router = APIRouter(prefix="/api/v1/agent-evaluations", tags=["Agent评测平台"])
DEFAULT_BUDGETS = {"max_seconds": 300, "max_model_calls": 20, "max_tokens": 20000, "max_sandboxes": 2}


async def access(request, permission, *, schema=True):
    current = await identity(request)
    user = await require_account(request.app.state.db, current["id"], permission)
    store = Store(request.app.state.db)
    if schema:
        await store.require_schema()
    return user, store


def project(row, names):
    return {key: row.get(key) for key in names.split()}


def dataset_summary(row):
    return project(row, "id name description revision case_count current_version mode_counts created_at updated_at")


def definition_readiness(definition, request=None, owner=None):
    value = definition if isinstance(definition, Definition) else Definition.model_validate(definition)
    declared = sorted({f"{case.fixture_profile.id}@{case.fixture_profile.version}" for case in value.cases})
    checked = False
    admission = service(request) if request is not None else None
    driver = admission.driver if admission is not None else None
    try:
        validator = getattr(driver, "validate_definition", None)
        checked = validator is not None and validator(owner, value.model_dump(mode="json"), 1) is True
    except Exception:  # noqa: BLE001 -- Unknown/unavailable profiles stay explicitly unverified, without path-bearing errors.
        checked = False
    blockers = admission.inspect(owner)["blockers"] if admission is not None else []
    ready = checked and not blockers
    return {
        "status": "READY" if ready else "BLOCKED",
        "parameters_schema_verified": checked,
        "missing_profiles": [] if checked else declared,
        "reasons": [] if ready else [item["message"] for item in blockers] or ["固定profile及参数尚未通过执行器校验。"],
    }


def dataset_detail(row, request=None, owner=None):
    definition = Definition.model_validate(row["definition"])
    quotes = {}
    for mode, repeats in (("engineering", 1), ("effect", 3)):
        try:
            quotes[mode] = default_budget(definition, repeats)
        except ValidationError:
            quotes[mode] = None
    return {
        **dataset_summary(row),
        **project(row, "definition definition_digest versions coverage"),
        "budget_quote": quotes,
        "schema_valid": True,
        "execution_readiness": definition_readiness(definition, request, owner),
    }


def run_summary(row):
    return project(
        row,
        (
            "id dataset_id version dataset_version_id execution_mode repeats "
            "acceptance_target status gate_verdict revision unique_task_count "
            "total_samples completed_samples counts created_at started_at finished_at"
        ),
    )


async def run_detail(store, row, owner):
    result = {
        **run_summary(row),
        **project(row, "budgets cleanup_status cancel_requested errors"),
        "version_set": row["snapshot"].get("version_set"),
        "admission_snapshot": row["snapshot"].get("admission"),
        "source_identity": row["snapshot"].get("version_set"),
        "scope": row["snapshot"].get("scope"),
        "report": {"available": False, "formats": [], "sha256": None},
    }
    try:
        report = await store.report(row["id"], owner)
        manifest, _ = checked_report(report, row["id"], owner)
        result["report"] = {
            "available": True,
            "formats": ["json", "junit", "markdown"],
            "sha256": manifest["files_sha256"]["json"],
        }
    except APIError as error:
        if error.status != 409:
            raise
    return result


def sample_summary(row):
    return project(
        row,
        (
            "id run_id case_id case_name mode repeat_index status task_outcome "
            "metric_status gate_verdict cleanup_status revision root_refs created_at "
            "started_at finished_at"
        ),
    )


@router.get("/capabilities")
async def capabilities(request: Request):
    user, store = await access(request, "evaluation.read", schema=False)
    admission = service(request).inspect(user["id"])
    installed = await store.installed()
    if not installed:
        admission["status"] = "BLOCKED"
        admission["blockers"].append({"code": "EVALUATION_SCHEMA_UNAVAILABLE", "message": "评测平台迁移尚未安装。"})
    return {
        "contract_version": 1,
        "permissions": {key: "evaluation." + key in user["permissions"] for key in ("read", "manage", "run")},
        "admission": admission,
        "schema_ready": installed,
        "thresholds": THRESHOLDS,
        "default_budgets": DEFAULT_BUDGETS,
        "supported_execution_modes": ["engineering"],
        "max_dataset_cases": 100,
    }


@router.get("/datasets")
async def datasets(
    request: Request, limit: int = Query(20, ge=1, le=100), cursor: str | None = Query(None, max_length=512)
):
    user, store = await access(request, "evaluation.read")
    rows, next_cursor = await store.listing("datasets", user["id"], limit, cursor)
    return {"items": [dataset_summary(row) for row in rows], "next_cursor": next_cursor}


@router.post("/datasets/preview")
async def preview(body: DatasetInput, request: Request):
    user, _ = await access(request, "evaluation.manage", schema=False)
    value = body.definition.model_dump(mode="json")
    return {
        "valid": True,
        "normalized_definition": value,
        "definition_digest": digest(value),
        "coverage": coverage(body.definition),
        "duplicate_groups": duplicates(body.definition),
        "warnings": ["不同case_id不代表不同业务任务，仍需人工语义去重。"],
        "schema_valid": True,
        "execution_readiness": definition_readiness(body.definition, request, user["id"]),
    }


@router.post("/datasets", status_code=201)
async def create_dataset(body: DatasetInput, request: Request):
    user, store = await access(request, "evaluation.manage")
    return dataset_detail(await store.create_dataset(user["id"], body), request, user["id"])


@router.get("/datasets/{identifier}")
async def dataset(identifier: UUID, request: Request):
    user, store = await access(request, "evaluation.read")
    return dataset_detail(await store.dataset_detail(identifier, user["id"]), request, user["id"])


@router.put("/datasets/{identifier}")
async def update_dataset(identifier: UUID, body: DatasetInput, request: Request):
    user, store = await access(request, "evaluation.manage")
    return dataset_detail(await store.update_dataset(identifier, user["id"], body), request, user["id"])


@router.post("/datasets/{identifier}/versions", status_code=201)
async def publish(identifier: UUID, body: PublishInput, request: Request):
    user, store = await access(request, "evaluation.manage")
    row = await store.publish(identifier, user["id"], body)
    readiness = definition_readiness(row["definition"], request, user["id"])
    return {**row, "execution_readiness": readiness, "executable": readiness["status"] == "READY"}


@router.get("/datasets/{identifier}/versions/{version}")
async def version(identifier: UUID, version: int, request: Request):
    user, store = await access(request, "evaluation.read")
    row = await store.version(identifier, version, user["id"])
    readiness = definition_readiness(row["definition"], request, user["id"])
    definition = Definition.model_validate(row["definition"])
    quotes = {}
    for mode, count in (("engineering", 1), ("effect", 3)):
        try:
            quotes[mode] = default_budget(definition, count)
        except ValidationError:
            quotes[mode] = None
    return {**row, "execution_readiness": readiness, "executable": readiness["status"] == "READY",
            "budget_quote": quotes}


@router.get("/runs")
async def runs(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    status: str | None = None,
    dataset_id: UUID | None = None,
):
    user, store = await access(request, "evaluation.read")
    rows, next_cursor = await store.listing("runs", user["id"], limit, cursor, status=status, dataset_id=dataset_id)
    return {"items": [run_summary(row) for row in rows], "next_cursor": next_cursor}


@router.post("/runs", status_code=202)
async def create_run(body: RunInput, request: Request):
    user, store = await access(request, "evaluation.run")
    version = await store.version(body.dataset_id, body.version, user["id"])
    definition = Definition.model_validate(version["definition"])
    await authorize(store.db, user["id"], definition.model_dump(mode="json"))
    budgets = body.budgets.model_dump() if body.budgets else DEFAULT_BUDGETS.copy()
    canonical = body.model_dump(mode="json")
    canonical["budgets"] = budgets
    prior = await store.prior_request(user["id"], body.request_id, digest(canonical))
    if prior:
        return await run_detail(store, prior, user["id"])
    admission = service(request).inspect(user["id"])
    if admission["status"] != "READY":
        first = admission["blockers"][0]
        fail(503, first["code"], first["message"])
    readiness = definition_readiness(definition, request, user["id"])
    if readiness["parameters_schema_verified"] is not True:
        fail(503, "EVALUATION_PROFILE_UNAVAILABLE", "题目profile、固定参数或版本尚未通过执行器校验，未创建批次")
    try:
        required = default_budget(definition, body.repeats)
    except ValidationError:
        fail(422, "EVALUATION_BUDGET_INSUFFICIENT", "题目聚合预算超过服务器上限，请缩小批次")
    if any(budgets[key] < value for key, value in required.items()):
        fail(422, "EVALUATION_BUDGET_INSUFFICIENT", "批次硬上限不足以覆盖题目预算聚合，请核对预算及重复数")
    if body.execution_mode != "engineering":
        fail(503, "EVALUATION_EFFECT_MODE_DISABLED", "真实模型效果执行尚未单独授权和准入")
    if body.acceptance_target == "b100" and (
        not coverage(definition)["b100_count_complete"] or version["semantic_review_status"] != "CONFIRMED"
    ):
        fail(409, "EVALUATION_TASK_REVIEW_REQUIRED", "B100任务规模和语义去重尚未确认")
    if body.version_set_id:
        selected = (await store.one("version_sets", body.version_set_id, user["id"]))["snapshot"]
    else:
        selected = service(request).driver.default_version_set(user["id"])
    snapshot = validate_version_snapshot(selected)
    row = await store.enqueue(user["id"], version, body, budgets, admission, snapshot)
    return await run_detail(store, row, user["id"])


@router.get("/runs/{identifier}")
async def run(identifier: UUID, request: Request):
    user, store = await access(request, "evaluation.read")
    return await run_detail(store, await store.one("runs", identifier, user["id"]), user["id"])


@router.post("/runs/{identifier}/cancel")
async def cancel(identifier: UUID, body: RevisionInput, request: Request):
    user, store = await access(request, "evaluation.run")
    return await run_detail(store, await store.cancel(identifier, user["id"], body.revision), user["id"])


@router.get("/runs/{identifier}/samples")
async def samples(
    identifier: UUID,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
):
    user, store = await access(request, "evaluation.read")
    rows, next_cursor = await store.samples(identifier, user["id"], limit, cursor)
    return {"items": [sample_summary(row) for row in rows], "next_cursor": next_cursor}


@router.get("/runs/{identifier}/samples/{sample_id}")
async def sample(identifier: UUID, sample_id: UUID, request: Request):
    user, store = await access(request, "evaluation.read")
    row = await store.sample(identifier, sample_id, user["id"])
    return {
        **sample_summary(row),
        **project(row, "facts metrics evidence_refs timings errors configuration_refs"),
        "usage": row["usage"]
        if row["usage"] is not None
        else {
            "calls": None,
            "input_tokens": None,
            "output_tokens": None,
            "completeness": "NOT_RUN" if row["task_outcome"] == "NOT_RUN" else "UNKNOWN",
        },
        "scope": "实际执行证据尚未导入" if row["task_outcome"] == "NOT_RUN" else "已登记执行证据",
        "review": await store.latest_review("sample", sample_id, user["id"]),
    }


@router.post("/runs/{identifier}/samples/{sample_id}/reviews", status_code=201)
async def review_sample(identifier: UUID, sample_id: UUID, body: ReviewInput, request: Request):
    user, store = await access(request, "evaluation.manage")
    await store.sample(identifier, sample_id, user["id"])
    reason, _ = redact(body.reason)
    row = await store.review(user["id"], "sample", sample_id, body.revision, body.decision, reason, body.rules_version)
    return {**row, "sample_id": sample_id}


@router.get("/runs/{identifier}/report")
async def report(identifier: UUID, request: Request, format: Literal["json", "junit", "markdown"] = "json"):
    user, store = await access(request, "evaluation.read")
    row = await store.report(identifier, user["id"])
    manifest, files = checked_report(row, identifier, user["id"])
    mime, extension = {
        "json": ("application/json", "json"),
        "junit": ("application/xml", "xml"),
        "markdown": ("text/markdown", "md"),
    }[format]
    return Response(
        files[format].encode(),
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="agent-evaluation-{identifier}.{extension}"',
            "ETag": manifest["files_sha256"][format],
        },
    )
