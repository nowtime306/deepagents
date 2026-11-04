"""Target-only durable metadata for actual member reads, retrieval and denied writes."""

import base64
import hashlib
import json
import os
from threading import Lock
from uuid import UUID, uuid4

from app.errors import APIError
from app.resource_runtime import current_scope
from app.team_context import active_member

CONTRACT = "controlled-team-effects-v2"


class TeamAudit:
    def __init__(self, path, state):
        self.path, self.state, self.lock = path, state, Lock()
        self.failed = False
        self.rows = []
        if path.exists():
            if path.is_symlink() or path.stat().st_size > 512 * 1024:
                raise ValueError("Invalid controlled team audit")
            self.rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.rows, list) or len(self.rows) > 100:
                raise ValueError("Invalid controlled team audit")
            for row in self.rows:
                if row["status"] == "PENDING":
                    row["status"] = "UNKNOWN"

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(self.rows, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except BaseException:
            self.failed = True
            raise

    def healthy(self):
        scope = current_scope()
        if scope is None or scope.mode != "work":
            return
        with self.lock:
            if self.failed or any(
                row["owner_id"] == str(scope.owner)
                and row["run_id"] == str(scope.run)
                and row["status"] == "UNKNOWN"
                for row in self.rows
            ):
                raise RuntimeError(
                    "Controlled team effects require reconciliation; no replay"
                )

    def start(self, kind, *, knowledge_id=None):
        self.healthy()
        scope, member = current_scope(), active_member.get()
        if scope is None or scope.mode != "work":
            return None
        with self.state.lock:
            root = self.state.roots.get((str(scope.owner), "work", str(scope.run)))
            if not root or root["case_id"] not in {"AET-W05", "AET-W06"}:
                return None
            case = root["case_id"]
        if kind in {"member-read", "query"} and (
            not member or member.get("role_name") != "reviewer"
        ):
            raise RuntimeError(
                "Controlled member effect lost its actual team-call attribution"
            )
        row = {
            "id": str(uuid4()),
            "owner_id": str(scope.owner),
            "run_id": str(scope.run),
            "case_id": case,
            "kind": kind,
            "team_call_id": str(member["id"]) if member else None,
            "status": "PENDING",
            "error_code": None,
            "result": None,
            "knowledge_id": str(knowledge_id) if knowledge_id else None,
        }
        with self.lock:
            if len(self.rows) >= 100:
                raise RuntimeError("Controlled effect audit bound exceeded")
            self.rows.append(row)
            self.save()  # Intent is durable before original SDK/search is entered.
        return row["id"]

    def finish(self, identifier, status, result=None, code=None):
        if identifier is None:
            return
        with self.lock:
            row = next(row for row in self.rows if row["id"] == identifier)
            if row["status"] != "PENDING":
                raise RuntimeError("Controlled effect cannot be replaced or replayed")
            row.update(status=status, result=result, error_code=code)
            self.save()

    def projection(self, owner, run):
        with self.lock:
            rows = [
                dict(row)
                for row in self.rows
                if row["owner_id"] == str(owner) and row["run_id"] == str(run)
            ]
            return {
                "contract": CONTRACT,
                "complete": not self.failed
                and not any(row["status"] in {"PENDING", "UNKNOWN"} for row in rows),
                "records": rows,
            }

    def denied_tool(self, run, call, error):
        if (
            not isinstance(error, APIError)
            or error.code != "WORK_BUDGET_EXCEEDED"
            or call.get("name") != "write_file"
            or call.get("args")
            != {"file_path": "/budget-after.txt", "content": "MUST_NOT_EXIST"}
        ):
            return
        scope = current_scope()
        if (
            scope is None
            or str(scope.run) != str(run["id"])
            or str(scope.owner) != str(run["owner_id"])
        ):
            raise RuntimeError("Budget denial does not belong to this real Work root")
        identifier = self.start("budget-write")
        summary = self.state.root_summary(run["owner_id"], "work", run["id"])
        self.finish(
            identifier,
            "DENIED",
            {
                "tool_call_id": call["id"],
                "provider_calls_before": summary["provider_calls"],
                "tokens_before": summary["tokens"],
            },
            code=error.code,
        )


def install(app, state):
    """Wrap actual WorkBackend and knowledge service; no fabricated API result is returned."""
    import app.work_engine as engine

    audit = TeamAudit(app.state.settings.data_dir / "evaluation-team-audit.json", state)
    original_backend, original_search = engine.WorkBackend, app.state.knowledge._search

    class ObservedBackend(original_backend):
        async def _call(self, script, body, seconds=15):
            member = active_member.get() or {}
            kind = (
                "member-read"
                if body.get("operation") == "read"
                and body.get("path") == "/input.txt"
                and member.get("role_name") == "reviewer"
                else "budget-write"
                if body.get("operation") == "write"
                and body.get("path") == "/budget-after.txt"
                else None
            )
            identifier = audit.start(kind) if kind else None
            try:
                value = await super()._call(script, body, seconds)
            except BaseException as error:
                denied = (
                    isinstance(error, APIError) and error.code == "WORK_BUDGET_EXCEEDED"
                )
                audit.finish(
                    identifier,
                    "DENIED" if denied else "UNKNOWN",
                    code=error.code
                    if isinstance(error, APIError)
                    else type(error).__name__,
                )
                raise
            try:
                if identifier:
                    if kind == "member-read" and not value.get("error"):
                        raw = base64.b64decode(value["data"], validate=True)
                        result = {
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "size": len(raw),
                        }
                    else:
                        result = {"returned_error": bool(value.get("error"))}
                    audit.finish(identifier, "SUCCEEDED", result)
                return value
            except BaseException:
                audit.failed = True
                raise

    async def search(knowledge_id, owner_id, query, limit=5, *, usage_context=None):
        scope = current_scope()
        # Standalone/non-team searches retain the original service path.
        if scope is None or scope.mode != "work" or not active_member.get():
            return await original_search(
                knowledge_id, owner_id, query, limit, usage_context=usage_context
            )
        if query != "星桥 保修" or str(owner_id) != str(scope.owner):
            raise ValueError("Only the sealed fixed member query is observable")
        identifier = audit.start("query", knowledge_id=UUID(str(knowledge_id)))
        try:
            value = await original_search(
                knowledge_id, owner_id, query, limit, usage_context=usage_context
            )
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if len(payload.encode()) > 32768:
                raise ValueError("Controlled retrieval evidence exceeded its bound")
            audit.finish(identifier, "SUCCEEDED", {"query": query, "retrieval": value})
            return value
        except BaseException as error:
            audit.finish(identifier, "UNKNOWN", code=type(error).__name__)
            raise

    engine.WorkBackend = ObservedBackend
    app.state.knowledge._search = search
    app.state.evaluation_team_audit = audit
    state.team_audit = audit
    app.state.db.evaluation_tool_budget_denial = audit.denied_tool

    def restore():
        engine.WorkBackend = original_backend
        app.state.knowledge._search = original_search
        del app.state.db.evaluation_tool_budget_denial

    return restore
