"""Owner-scoped comparisons and reviewed redacted candidates; no execution/result upload."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.agent_evaluation_platform_admission import checked_report, compare_reports
from app.agent_evaluation_platform_api import access, project
from app.agent_evaluation_platform_types import (
    CandidateInput,
    CandidateReview,
    ComparisonInput,
    ComparisonMaterial,
    candidate_preview,
    redact,
)
from app.errors import fail

router = APIRouter(prefix="/api/v1/agent-evaluations", tags=["Agent评测复核"])


def comparison_view(row):
    return {
        **project(
            row, "id baseline_run_id candidate_run_id changed_axis comparable reasons summary case_changes created_at"
        ),
        "status": "COMPARABLE" if row["comparable"] else "INCOMPARABLE",
    }


def candidate_summary(row):
    return project(row, "id name status revision created_at")


async def candidate_detail(store, row, owner):
    return {
        **candidate_summary(row),
        **project(row, "source_sample_id material_digest redacted_material authorization_note"),
        "review": await store.latest_review("failure_candidate", row["id"], owner),
    }


def comparison_material(manifest):
    try:
        return ComparisonMaterial.model_validate(manifest.get("comparison")).model_dump(mode="json")
    except ValidationError:
        fail(409, "EVALUATION_COMPARISON_NOT_READY", "报告尚缺有效实际身份、完整独立重复或类型化任务事实")


@router.get("/comparisons")
async def comparisons(
    request: Request, limit: int = Query(20, ge=1, le=100), cursor: str | None = Query(None, max_length=512)
):
    user, store = await access(request, "evaluation.read")
    rows, next_cursor = await store.listing("comparisons", user["id"], limit, cursor)
    return {"items": [comparison_view(row) for row in rows], "next_cursor": next_cursor}


@router.get("/comparisons/{identifier}")
async def comparison(identifier: UUID, request: Request):
    user, store = await access(request, "evaluation.read")
    return comparison_view(await store.one("comparisons", identifier, user["id"]))


@router.post("/comparisons", status_code=201)
async def create_comparison(body: ComparisonInput, request: Request):
    user, store = await access(request, "evaluation.manage")
    owner = user["id"]
    # Validate both owner scopes before consulting report availability.
    await store.one("runs", body.baseline_run_id, owner)
    await store.one("runs", body.candidate_run_id, owner)
    materials = []
    for identifier in (body.baseline_run_id, body.candidate_run_id):
        manifest, _ = checked_report(await store.report(identifier, owner), identifier, owner)
        materials.append(comparison_material(manifest))
    comparable, reasons, summary, changes = compare_reports(*materials, body.changed_axis)
    identifier = uuid4()
    note, _ = redact(body.notes)
    async with store.db.pool.connection() as conn:
        row = await (
            await conn.execute(
                "INSERT INTO agent_evaluation_comparisons(id,owner_id,baseline_run_id,candidate_run_id,changed_axis,"
                "comparable,reasons,summary,case_changes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    identifier,
                    owner,
                    body.baseline_run_id,
                    body.candidate_run_id,
                    body.changed_axis,
                    comparable,
                    Jsonb(reasons),
                    Jsonb(summary),
                    Jsonb(changes),
                ),
            )
        ).fetchone()
        await conn.execute(
            "INSERT INTO agent_evaluation_events(owner_id,run_id,event,data) VALUES(%s,%s,'comparison.created',%s)",
            (owner, body.candidate_run_id, Jsonb({"comparison_id": str(identifier), "notes": note})),
        )
    return comparison_view(row)


@router.get("/failure-candidates")
async def candidates(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    status: Literal["PENDING_REVIEW", "APPROVED", "REJECTED"] | None = None,
):
    user, store = await access(request, "evaluation.read")
    rows, next_cursor = await store.listing("failure_candidates", user["id"], limit, cursor, status=status)
    return {"items": [candidate_summary(row) for row in rows], "next_cursor": next_cursor}


@router.post("/failure-candidates/preview")
async def preview_candidate(body: CandidateInput, request: Request):
    await access(request, "evaluation.manage", schema=False)
    return candidate_preview(body)


@router.post("/failure-candidates", status_code=201)
async def create_candidate(body: CandidateInput, request: Request):
    user, store = await access(request, "evaluation.manage")
    if body.redaction_confirmed is not True or not body.authorization_note.strip():
        fail(422, "EVALUATION_REDACTION_CONFIRMATION_REQUIRED", "请确认材料授权范围和人工脱敏结果")
    name, _ = redact(body.name)
    note, _ = redact(body.authorization_note)
    row = await store.candidate_create(user["id"], body, candidate_preview(body), name, note)
    return await candidate_detail(store, row, user["id"])


@router.get("/failure-candidates/{identifier}")
async def candidate(identifier: UUID, request: Request):
    user, store = await access(request, "evaluation.read")
    return await candidate_detail(store, await store.one("failure_candidates", identifier, user["id"]), user["id"])


@router.post("/failure-candidates/{identifier}/review", status_code=201)
async def review_candidate(identifier: UUID, body: CandidateReview, request: Request):
    user, store = await access(request, "evaluation.manage")
    reason, _ = redact(body.reason)
    await store.review(user["id"], "failure_candidate", identifier, body.revision, body.decision, reason)
    return await candidate_detail(store, await store.one("failure_candidates", identifier, user["id"]), user["id"])
