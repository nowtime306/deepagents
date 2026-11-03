"""Explicit human citation labels and source-pinned quality gates, never inferred from retrieval alone."""

from statistics import mean
from uuid import UUID

from fastapi import APIRouter, Request
from psycopg.errors import LockNotAvailable
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.knowledge_pins import snapshot
from app.security import identity

router = APIRouter(prefix="/api/v1/capabilities/knowledge", tags=["知识质量门禁"])
DEFAULTS = {"enforce_publish": False, "minimum_questions": 50, "recall_target": .9,
            "citation_target": .95, "no_answer_target": .9, "revision": 0}


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enforce_publish: bool = False
    minimum_questions: int = Field(default=50, ge=50, le=50)
    recall_target: float = Field(default=.9, ge=0, le=1)
    citation_target: float = Field(default=.95, ge=0, le=1)
    no_answer_target: float = Field(default=.9, ge=0, le=1)
    revision: int = Field(default=0, ge=0)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=12000)
    cited_documents: list[UUID] = Field(default_factory=list, max_length=20)
    citation_correct: bool | None = None
    refusal_correct: bool | None = None
    note: str = Field(default="", max_length=2000)
    revision: int = Field(default=0, ge=0)


async def policy(db, knowledge, owner):
    rows = await db.fetch("SELECT * FROM knowledge_quality_policies WHERE knowledge_id=%s AND owner_id=%s",
                          (knowledge, owner))
    return rows[0] if rows else DEFAULTS.copy()


@router.get("/{identifier}/quality/policy")
async def read_policy(identifier: UUID, request: Request):
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    return await policy(request.app.state.db, identifier, user["id"])


@router.put("/{identifier}/quality/policy")
async def save_policy(identifier: UUID, body: Policy, request: Request):
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    async with request.app.state.db.pool.connection() as c:
        await c.execute("SELECT id FROM knowledge_bases WHERE id=%s AND owner_id=%s FOR UPDATE",
                        (identifier, user["id"]))
        old = await (await c.execute("SELECT revision FROM knowledge_quality_policies WHERE knowledge_id=%s",
                                    (identifier,))).fetchone()
        if (old["revision"] if old else 0) != body.revision:
            fail(409, "QUALITY_POLICY_CONFLICT", "质量规则已更新，请刷新后重试")
        return await (await c.execute(
            "INSERT INTO knowledge_quality_policies(knowledge_id,owner_id,enforce_publish,minimum_questions,"
            "recall_target,citation_target,no_answer_target) VALUES(%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(knowledge_id) DO UPDATE SET enforce_publish=excluded.enforce_publish,"
            "minimum_questions=excluded.minimum_questions,recall_target=excluded.recall_target,"
            "citation_target=excluded.citation_target,no_answer_target=excluded.no_answer_target,"
            "revision=knowledge_quality_policies.revision+1,updated_at=now() RETURNING *",
            (identifier, user["id"], body.enforce_publish, body.minimum_questions,
             body.recall_target, body.citation_target, body.no_answer_target))).fetchone()


async def experiment(db, identifier, owner, exp):
    rows = await db.fetch(
        "SELECT * FROM knowledge_experiments WHERE id=%s AND knowledge_id=%s AND owner_id=%s",
        (exp, identifier, owner))
    if not rows:
        fail(404, "EXPERIMENT_NOT_FOUND", "实验不存在或无权访问")
    return rows[0]


@router.get("/{identifier}/quality/reviews/{exp}")
async def reviews(identifier: UUID, exp: UUID, request: Request):
    user = await identity(request)
    row = await experiment(request.app.state.db, identifier, user["id"], exp)
    return {"questions": row["dataset_snapshot"]["questions"], "results": row["results"],
            "items": await request.app.state.db.fetch(
                "SELECT * FROM knowledge_quality_reviews WHERE experiment_id=%s AND owner_id=%s ORDER BY question_id",
                (exp, user["id"]))}


@router.put("/{identifier}/quality/reviews/{exp}/{question}")
async def save_review(identifier: UUID, exp: UUID, question: str, body: Review, request: Request):
    user = await identity(request)
    db = request.app.state.db
    row = await experiment(db, identifier, user["id"], exp)
    item = next((q for q in row["dataset_snapshot"]["questions"] if q["id"] == question), None)
    if not item or row["status"] != "SUCCEEDED":
        fail(422, "QUALITY_REVIEW_UNAVAILABLE", "只能标注已完成实验中的实际题目")
    if item["unanswerable"]:
        if body.refusal_correct is None or body.citation_correct is not None or body.cited_documents:
            fail(422, "QUALITY_LABEL_INVALID", "无答案题应单独标注拒答，不填写引用指标")
    elif body.citation_correct is None or body.refusal_correct is not None:
        fail(422, "QUALITY_LABEL_INVALID", "有答案题应标注生成答案的引用正确性")
    if body.citation_correct and not body.cited_documents:
        fail(422, "QUALITY_CITATIONS_REQUIRED", "引用正确标注需要提供实际引用的文档")
    pinned = {d[0] for d in row["resource_snapshot"]["documents"]}
    if any(str(d) not in pinned for d in body.cited_documents):
        fail(422, "QUALITY_SOURCE_INVALID", "引用文档不在本次实验固定来源中")
    async with db.pool.connection() as c:
        await c.execute("SELECT id FROM knowledge_experiments WHERE id=%s FOR UPDATE", (exp,))
        old = await (await c.execute("SELECT revision FROM knowledge_quality_reviews WHERE experiment_id=%s "
                                    "AND question_id=%s", (exp, question))).fetchone()
        if (old["revision"] if old else 0) != body.revision:
            fail(409, "QUALITY_REVIEW_CONFLICT", "标注已被修改，请刷新后重试")
        return await (await c.execute(
            "INSERT INTO knowledge_quality_reviews(experiment_id,question_id,owner_id,answer,cited_documents,"
            "citation_correct,refusal_correct,note) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(experiment_id,question_id) DO UPDATE SET answer=excluded.answer,"
            "cited_documents=excluded.cited_documents,citation_correct=excluded.citation_correct,"
            "refusal_correct=excluded.refusal_correct,note=excluded.note,"
            "revision=knowledge_quality_reviews.revision+1,updated_at=now() RETURNING *",
            (exp, question, user["id"], body.answer, body.cited_documents,
             body.citation_correct, body.refusal_correct, body.note))).fetchone()


def verdict(config, row, annotations, source_matches):
    """Unknown or partial labels block the real-data gate instead of counting as passes."""
    questions = row["dataset_snapshot"]["questions"]
    answered = {q["id"] for q in questions if not q["unanswerable"]}
    unanswerable = {q["id"] for q in questions if q["unanswerable"]}
    recalls = [r["recall_at_10"] for r in row["results"] if r.get("recall_at_10") is not None]
    no_answers = [r["no_answer_correct"] for r in row["results"] if r.get("no_answer_correct") is not None]
    citations = [r["citation_correct"] for r in annotations if r["question_id"] in answered]
    refusals = [r["refusal_correct"] for r in annotations if r["question_id"] in unanswerable]
    metrics = {"recall_at_10": mean(recalls) if len(recalls) == len(answered) and recalls else None,
               "citation_correctness": mean(citations) if len(citations) == len(answered)
               and citations and all(v is not None for v in citations) else None,
               "no_answer_retrieval": mean(no_answers) if len(no_answers) == len(unanswerable) and no_answers else None,
               "answer_refusal_accuracy": mean(refusals) if len(refusals) == len(unanswerable)
               and refusals and all(v is not None for v in refusals) else None}
    reasons = []
    if row["status"] != "SUCCEEDED" or len(row["results"]) != len(questions):
        reasons.append("实验未完整成功")
    if {r["id"] for r in row["results"]} != {q["id"] for q in questions}:
        reasons.append("实验结果题号不匹配")
    if row["dataset_snapshot"]["dataset_type"] != "user":
        reasons.append("合成题仅作工程回归，不能通过真实业务门禁")
    if len(questions) < config["minimum_questions"]:
        reasons.append("题目数量不足")
    if not source_matches:
        reasons.append("文档、检索配置或模型行为或兼容信息已变化，需重新评测")
    targets = {"recall_at_10": "recall_target", "citation_correctness": "citation_target",
               "no_answer_retrieval": "no_answer_target", "answer_refusal_accuracy": "no_answer_target"}
    for name, target in targets.items():
        if metrics[name] is None:
            reasons.append(name+"未完整标注或缺少样本")
        elif metrics[name] < float(config[target]):
            reasons.append(name+"低于阈值")
    return {"passed": not reasons, "reasons": reasons, "metrics": metrics, "questions": len(questions),
            "citation_denominator": len(answered), "no_answer_denominator": len(unanswerable),
            "reviewed_answers": len(annotations), "dataset_type": row["dataset_snapshot"]["dataset_type"],
            "note": "引用与拒答为人工核对标签，不是检索命中或模型自评；不保证所有真实业务效果"}


async def check(db, knowledge, owner, row):
    annotations = await db.fetch("SELECT * FROM knowledge_quality_reviews WHERE experiment_id=%s AND owner_id=%s",
                                  (row["id"], owner))
    matches = await snapshot(db, knowledge) == row["resource_snapshot"]
    result = verdict(await policy(db, knowledge["id"], owner), row, annotations, matches)
    result["applicability"] = {"status": "applicable" if matches else "retest_recommended",
                               "reason": None if matches else "文档或效果配置已变化，旧结果保留，建议重新评测"}
    return result


@router.get("/{identifier}/quality/check/{exp}")
async def check_quality(identifier: UUID, exp: UUID, request: Request):
    owner = (await identity(request))["id"]
    knowledge = await request.app.state.knowledge.owned(identifier, owner)
    row = await experiment(request.app.state.db, identifier, owner, exp)
    return {**await check(request.app.state.db, knowledge, owner, row), "experiment_id": exp}


class PublicationReader:
    """Read gate inputs on the publication transaction, never through a second connection."""

    def __init__(self, connection):
        self.connection = connection

    async def fetch(self, query, params=()):
        return await (await self.connection.execute(query, params)).fetchall()


async def publication_guard(connection, owner, knowledge_ids):
    db = PublicationReader(connection)
    reports = []
    for identifier in sorted(knowledge_ids, key=str):
        try:
            bases = await db.fetch(
                "SELECT * FROM knowledge_bases WHERE id=%s AND owner_id=%s FOR UPDATE NOWAIT", (identifier, owner))
            if not bases:
                fail(409, "KNOWLEDGE_QUALITY_GATE_FAILED", "绑定知识库已不存在或无权使用")
            await db.fetch("SELECT knowledge_id FROM knowledge_quality_policies WHERE knowledge_id=%s "
                           "FOR UPDATE NOWAIT", (identifier,))
            await db.fetch("SELECT id FROM documents WHERE knowledge_id=%s ORDER BY id FOR UPDATE NOWAIT",
                           (identifier,))
            await db.fetch("SELECT id FROM model_configs WHERE id=ANY(%s::text[]) ORDER BY id FOR UPDATE NOWAIT",
                           ([bases[0][key] for key in ("embedding_model_id", "rerank_model_id") if bases[0][key]],))
            rows = await db.fetch("SELECT * FROM knowledge_experiments WHERE knowledge_id=%s AND owner_id=%s "
                                  "ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE NOWAIT", (identifier, owner))
        except LockNotAvailable:
            fail(409, "QUALITY_SOURCE_BUSY", "知识来源或标注正在更新，请稍后重新发布")
        rules = await policy(db, identifier, owner)
        if not rules["enforce_publish"]:
            continue
        if not bases or not rows or not (await check(db, bases[0], owner, rows[0]))["passed"]:
            fail(409, "KNOWLEDGE_QUALITY_GATE_FAILED", "绑定知识库的质量门禁未通过，请查看题集、标注和来源修订")
        reports.append({"knowledge_id": str(identifier), "experiment_id": str(rows[0]["id"]),
                        "policy_revision": rules["revision"], "source_snapshot": rows[0]["resource_snapshot"],
                        "review_revisions": await db.fetch(
                            "SELECT question_id,revision FROM knowledge_quality_reviews WHERE experiment_id=%s "
                            "ORDER BY question_id", (rows[0]["id"],))})
    return reports
