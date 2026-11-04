"""Owner-bound PostgreSQL persistence; no execution, filesystem paths or public report ingestion."""

import base64
import json
from datetime import datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.agent_evaluation_platform_types import Definition, coverage, digest
from app.errors import fail

TABLES = {
    "datasets": "agent_evaluation_datasets",
    "runs": "agent_evaluation_runs",
    "comparisons": "agent_evaluation_comparisons",
    "failure_candidates": "agent_evaluation_failure_candidates",
    "version_sets": "agent_evaluation_version_sets",
}


def cursor_encode(row):
    return base64.urlsafe_b64encode(json.dumps([str(row["created_at"]), str(row["id"])]).encode()).decode().rstrip("=")


def cursor_decode(value):
    try:
        if len(value) > 512:
            raise ValueError()
        stamp, identifier = json.loads(base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4)))
        instant = datetime.fromisoformat(stamp)
        if instant.tzinfo is None:
            raise ValueError()
        return instant, UUID(identifier)
    except (ValueError, TypeError, KeyError, UnicodeError):
        fail(422, "EVALUATION_CURSOR_INVALID", "分页标识无效，请从第一页重新读取")


class Store:
    def __init__(self, database):
        self.db = database

    async def installed(self):
        row = await self.db.fetch("SELECT to_regclass('agent_evaluation_datasets') IS NOT NULL AS ready")
        return bool(row and row[0]["ready"])

    async def require_schema(self):
        if not await self.installed():
            fail(503, "EVALUATION_SCHEMA_UNAVAILABLE", "评测平台迁移尚未安装，未执行任何评测")

    async def one(self, kind, identifier, owner):
        table = TABLES[kind]  # Fixed internal mapping, never supplied by an HTTP body.
        rows = await self.db.fetch(f"SELECT * FROM {table} WHERE id=%s AND owner_id=%s", (identifier, owner))
        if not rows:
            fail(404, "NOT_FOUND", "记录不存在或无权查看")
        return rows[0]

    async def listing(self, kind, owner, limit, cursor=None, *, status=None, dataset_id=None):
        table = TABLES[kind]
        query = f"SELECT * FROM {table} WHERE owner_id=%s"
        params = [owner]
        if cursor:
            stamp, identifier = cursor_decode(cursor)
            query += " AND (created_at,id)<(%s,%s)"
            params.extend([stamp, identifier])
        if status is not None:
            query += " AND status=%s"
            params.append(status)
        if dataset_id is not None:
            query += " AND dataset_id=%s"
            params.append(dataset_id)
        query += " ORDER BY created_at DESC,id DESC LIMIT %s"
        params.append(limit + 1)
        rows = await self.db.fetch(query, tuple(params))
        return rows[:limit], cursor_encode(rows[limit - 1]) if len(rows) > limit else None

    async def dataset_detail(self, identifier, owner):
        row = await self.one("datasets", identifier, owner)
        row["versions"] = await self.db.fetch(
            "SELECT "
            "id,dataset_id,version,definition_digest,case_count,mode_counts,no"
            "tes,semantic_review_status,created_by,created_at "
            "FROM agent_evaluation_dataset_versions WHERE dataset_id=%s AND owner_id=%s ORDER BY version DESC",
            (identifier, owner),
        )
        row["coverage"] = coverage(Definition.model_validate(row["definition"]))
        return row

    async def create_dataset(self, owner, body):
        if body.revision != 0:
            fail(409, "EVALUATION_DATASET_CONFLICT", "新评测集修订必须为0")
        definition = body.definition.model_dump(mode="json")
        counts = coverage(body.definition)["mode_counts"]
        identifier = uuid4()
        await self.db.execute(
            "INSERT INTO "
            "agent_evaluation_datasets(id,owner_id,name,description,definition"
            ",definition_digest,case_count,mode_counts) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                owner,
                body.name,
                body.description,
                Jsonb(definition),
                digest(definition),
                len(body.definition.cases),
                Jsonb(counts),
            ),
        )
        return await self.dataset_detail(identifier, owner)

    async def update_dataset(self, identifier, owner, body):
        await self.one("datasets", identifier, owner)
        definition = body.definition.model_dump(mode="json")
        rows = await self.db.fetch(
            "UPDATE agent_evaluation_datasets SET name=%s,description=%s,definition=%s,definition_digest=%s,"
            "case_count=%s,mode_counts=%s,revision=revision+1,updated_at=now() WHERE "
            "id=%s AND owner_id=%s AND revision=%s RETURNING id",
            (
                body.name,
                body.description,
                Jsonb(definition),
                digest(definition),
                len(body.definition.cases),
                Jsonb(coverage(body.definition)["mode_counts"]),
                identifier,
                owner,
                body.revision,
            ),
        )
        if not rows:
            fail(409, "EVALUATION_DATASET_CONFLICT", "评测集已变化，请刷新后保存")
        return await self.dataset_detail(identifier, owner)

    async def publish(self, identifier, owner, body):
        async with self.db.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM agent_evaluation_datasets WHERE id=%s AND owner_id=%s FOR UPDATE",
                    (identifier, owner),
                )
            ).fetchall()
            if not rows:
                fail(404, "NOT_FOUND", "评测集不存在或无权发布")
            row = rows[0]
            if row["revision"] != body.revision:
                fail(409, "EVALUATION_DATASET_CONFLICT", "草稿已变化，请刷新后发布")
            existing = await (
                await conn.execute(
                    "SELECT * FROM agent_evaluation_dataset_versions WHERE dataset_id=%s AND "
                    "owner_id=%s AND definition_digest=%s",
                    (identifier, owner, row["definition_digest"]),
                )
            ).fetchone()
            if existing:
                return {**existing, "created": False}
            version = (row["current_version"] or 0) + 1
            published = await (
                await conn.execute(
                    "INSERT INTO "
                    "agent_evaluation_dataset_versions(id,dataset_id,owner_id,version,"
                    "definition,definition_digest,case_count,mode_counts,notes,created"
                    "_by) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                    (
                        uuid4(),
                        identifier,
                        owner,
                        version,
                        Jsonb(row["definition"]),
                        row["definition_digest"],
                        row["case_count"],
                        Jsonb(row["mode_counts"]),
                        body.notes,
                        owner,
                    ),
                )
            ).fetchone()
            await conn.execute(
                "UPDATE agent_evaluation_datasets SET "
                "current_version=%s,revision=revision+1,updated_at=now() WHERE id=%s AND "
                "owner_id=%s",
                (version, identifier, owner),
            )
            return {**published, "created": True}

    async def version(self, identifier, version, owner):
        rows = await self.db.fetch(
            "SELECT * FROM agent_evaluation_dataset_versions WHERE dataset_id=%s AND version=%s AND owner_id=%s",
            (identifier, version, owner),
        )
        if not rows:
            fail(404, "NOT_FOUND", "发布版本不存在或无权查看")
        return rows[0]

    async def prior_request(self, owner, request_id, payload):
        rows = await self.db.fetch(
            "SELECT * FROM agent_evaluation_runs WHERE owner_id=%s AND request_id=%s", (owner, request_id)
        )
        if rows and rows[0]["payload_digest"] != payload:
            fail(409, "EVALUATION_REQUEST_CONFLICT", "同一提交标识对应的参数已改变")
        return rows[0] if rows else None

    async def enqueue(self, owner, version, body, budgets, admission, version_set):
        canonical = body.model_dump(mode="json")
        canonical["budgets"] = budgets
        payload = digest(canonical)
        async with self.db.pool.connection() as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(710050,hashtext(%s))", (str(owner) + str(body.request_id),)
            )
            old = await (
                await conn.execute(
                    "SELECT * FROM agent_evaluation_runs WHERE owner_id=%s AND request_id=%s", (owner, body.request_id)
                )
            ).fetchone()
            if old:
                if old["payload_digest"] != payload:
                    fail(409, "EVALUATION_REQUEST_CONFLICT", "同一提交标识对应不同参数")
                return old
            identifier = uuid4()
            definition = Definition.model_validate(version["definition"])
            snapshot = {
                "definition": definition.model_dump(mode="json"),
                "dataset_digest": version["definition_digest"],
                "version_set": version_set,
                "admission": admission,
                "scope": "controlled engineering"
                if body.execution_mode == "engineering"
                else "explicit effect evaluation",
            }
            row = await (
                await conn.execute(
                    "INSERT INTO "
                    "agent_evaluation_runs(id,owner_id,dataset_id,dataset_version_id,v"
                    "ersion,request_id,payload_digest,execution_mode,repeats,acceptanc"
                    "e_target,snapshot,budgets,unique_task_count,total_samples) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                    (
                        identifier,
                        owner,
                        body.dataset_id,
                        version["id"],
                        body.version,
                        body.request_id,
                        payload,
                        body.execution_mode,
                        body.repeats,
                        body.acceptance_target,
                        Jsonb(snapshot),
                        Jsonb(budgets),
                        len(definition.cases),
                        len(definition.cases) * body.repeats,
                    ),
                )
            ).fetchone()
            row["counts"] = {"pass": 0, "fail": 0, "needs_review": 0, "not_run": len(definition.cases) * body.repeats}
            await conn.execute(
                "UPDATE agent_evaluation_runs SET counts=%s WHERE id=%s AND owner_id=%s",
                (Jsonb(row["counts"]), identifier, owner),
            )
            for case in definition.cases:
                for repeat in range(1, body.repeats + 1):
                    await conn.execute(
                        "INSERT INTO "
                        "agent_evaluation_samples(id,owner_id,run_id,case_id,case_name,mod"
                        "e,repeat_index,case_snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            uuid4(),
                            owner,
                            identifier,
                            case.case_id,
                            case.name,
                            case.mode,
                            repeat,
                            Jsonb(case.model_dump(mode="json")),
                        ),
                    )
            await conn.execute(
                "INSERT INTO agent_evaluation_events(owner_id,run_id,event) VALUES(%s,%s,'queued')", (owner, identifier)
            )
            return row

    async def cancel(self, identifier, owner, revision):
        async with self.db.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM agent_evaluation_runs WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, owner)
                )
            ).fetchone()
            if not row:
                fail(404, "NOT_FOUND", "批次不存在或无权取消")
            if row["status"] in {"COMPLETED", "CANCELLED", "FAILED", "NEEDS_REVIEW"}:
                return row
            if row["revision"] != revision:
                fail(409, "EVALUATION_RUN_CONFLICT", "批次状态已改变，请刷新后取消")
            queued = row["status"] == "QUEUED" and not row["dispatch_started"]
            if queued:
                await conn.execute(
                    "UPDATE agent_evaluation_samples SET "
                    "status='CANCELLED',gate_verdict='NEEDS_REVIEW',finished_at=now() WHERE "
                    "run_id=%s AND owner_id=%s AND status='PENDING'",
                    (identifier, owner),
                )
            updated = await (
                await conn.execute(
                    "UPDATE agent_evaluation_runs SET "
                    "cancel_requested=true,status=%s,gate_verdict=%s,revision=revision"
                    "+1,finished_at=CASE WHEN %s THEN now() ELSE finished_at END,compl"
                    "eted_samples=CASE WHEN %s THEN total_samples ELSE completed_sampl"
                    "es END,counts=CASE WHEN %s THEN jsonb_build_object('pass',0,'fail"
                    "',0,'needs_review',0,'not_run',total_samples) ELSE counts END WHE"
                    "RE id=%s AND owner_id=%s RETURNING *",
                    (
                        "CANCELLED" if queued else "CANCEL_REQUESTED",
                        "NEEDS_REVIEW" if queued else row["gate_verdict"],
                        queued,
                        queued,
                        queued,
                        identifier,
                        owner,
                    ),
                )
            ).fetchone()
            await conn.execute(
                "INSERT INTO agent_evaluation_events(owner_id,run_id,event) VALUES(%s,%s,'cancel.requested')",
                (owner, identifier),
            )
            return updated

    async def samples(self, run, owner, limit, cursor):
        await self.one("runs", run, owner)
        query = "SELECT * FROM agent_evaluation_samples WHERE run_id=%s AND owner_id=%s"
        params = [run, owner]
        if cursor:
            stamp, identifier = cursor_decode(cursor)
            query += " AND (created_at,id)<(%s,%s)"
            params.extend([stamp, identifier])
        rows = await self.db.fetch(query + " ORDER BY created_at DESC,id DESC LIMIT %s", tuple([*params, limit + 1]))
        return rows[:limit], cursor_encode(rows[limit - 1]) if len(rows) > limit else None

    async def sample(self, run, identifier, owner):
        await self.one("runs", run, owner)
        rows = await self.db.fetch(
            "SELECT * FROM agent_evaluation_samples WHERE id=%s AND run_id=%s AND owner_id=%s", (identifier, run, owner)
        )
        if not rows:
            fail(404, "NOT_FOUND", "样本不存在或无权查看")
        return rows[0]

    async def latest_review(self, kind, identifier, owner):
        rows = await self.db.fetch(
            "SELECT * FROM agent_evaluation_reviews WHERE owner_id=%s AND "
            "target_kind=%s AND target_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (owner, kind, identifier),
        )
        return rows[0] if rows else None

    async def candidate_create(self, owner, body, preview, name, note):
        if body.source_sample_id:
            rows = await self.db.fetch(
                "SELECT id FROM agent_evaluation_samples WHERE id=%s AND owner_id=%s", (body.source_sample_id, owner)
            )
            if not rows:
                fail(404, "NOT_FOUND", "来源样本不存在或无权引用")
        identifier = uuid4()
        await self.db.execute(
            "INSERT INTO "
            "agent_evaluation_failure_candidates(id,owner_id,source_sample_id,"
            "name,redacted_material,material_digest,authorization_note) VALUES"
            "(%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                owner,
                body.source_sample_id,
                name,
                Jsonb(preview["redacted_material"]),
                preview["material_digest"],
                note,
            ),
        )
        return await self.one("failure_candidates", identifier, owner)

    async def review(self, owner, kind, identifier, revision, decision, reason, rules_version=""):
        table = "agent_evaluation_samples" if kind == "sample" else "agent_evaluation_failure_candidates"
        async with self.db.pool.connection() as conn:
            row = await (
                await conn.execute(f"SELECT * FROM {table} WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, owner))
            ).fetchone()
            if not row:
                fail(404, "NOT_FOUND", "复核目标不存在或无权查看")
            if row["revision"] != revision:
                fail(409, "EVALUATION_REVIEW_CONFLICT", "目标已复核或改变，请刷新")
            original = row.get("gate_verdict")
            result = await (
                await conn.execute(
                    "INSERT INTO "
                    "agent_evaluation_reviews(id,owner_id,target_kind,target_id,review"
                    "er_id,decision,reason,rules_version,original_gate) VALUES(%s,%s,%"
                    "s,%s,%s,%s,%s,%s,%s) RETURNING *",
                    (uuid4(), owner, kind, identifier, owner, decision, reason, rules_version, original),
                )
            ).fetchone()
            if kind == "sample":
                await conn.execute(
                    "UPDATE agent_evaluation_samples SET revision=revision+1 WHERE id=%s AND owner_id=%s",
                    (identifier, owner),
                )
            else:
                await conn.execute(
                    "UPDATE agent_evaluation_failure_candidates SET "
                    "status=%s,revision=revision+1 WHERE id=%s AND owner_id=%s",
                    (decision, identifier, owner),
                )
            return result

    async def report(self, run, owner):
        await self.one("runs", run, owner)
        rows = await self.db.fetch(
            "SELECT * FROM agent_evaluation_reports WHERE run_id=%s AND owner_id=%s AND verified AND redacted",
            (run, owner),
        )
        if not rows:
            fail(409, "EVALUATION_REPORT_NOT_READY", "尚无完整验证的授权报告，不能导出或比较")
        return rows[0]
