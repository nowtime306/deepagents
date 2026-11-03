"""Single-use approval receipts and conservative recording of uncertain effects."""

import hashlib
import json
from uuid import uuid4

from psycopg.types.json import Jsonb


def action_digest(name: str, args: dict, *, semantic_digest: str | None = None) -> str:
    if name == "execute":
        args = {"command": args["command"], "timeout": min(max(int(args.get("timeout") or 60), 1), 60)}
    return hashlib.sha256(
        json.dumps({"name": name, "args": args, **({"semantic_digest": semantic_digest} if semantic_digest else {})},
                   sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def receipts(connection, approval: dict, actions: list[dict]) -> None:
    for index, action in enumerate(actions):
        semantic = None
        if action["name"] == "run_browser_skill":
            from app.resource_tools import digest

            row = await (await connection.execute(
                "SELECT v.trajectory FROM browser_skill_versions v JOIN work_queue_runs r ON r.owner_id=v.owner_id "
                "WHERE r.id=%s AND v.skill_id=%s AND v.version=%s",
                (approval["run_id"], action["args"].get("skill_id"), action["args"].get("version")))).fetchone()
            if not row:
                raise PermissionError("Browser Skill approval requires the published trajectory")
            semantic = digest(row["trajectory"])
        await connection.execute(
            "INSERT INTO operation_executions(id,run_id,approval_id,action_index,name,args,digest) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(approval_id,action_index) DO UPDATE "
            "SET digest=excluded.digest WHERE operation_executions.status='APPROVED'",
            (
                uuid4(),
                approval["run_id"],
                approval["id"],
                index,
                action["name"],
                Jsonb(action["args"]),
                action_digest(action["name"], action["args"], semantic_digest=semantic),
            ),
        )


class OperationJournal:
    def __init__(self, database, run: dict) -> None:
        self.database, self.run = database, run

    async def start(self, name: str, args: dict, *, semantic_digest: str | None = None):
        async with self.database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT o.id FROM operation_executions o JOIN work_approvals a ON a.id=o.approval_id "
                    "JOIN work_queue_runs r ON r.id=o.run_id WHERE o.run_id=%s AND o.digest=%s AND o.status='APPROVED' "
                    "AND a.consumed_at IS NOT NULL AND a.expires_at>now() AND a.decision IN ('approve','edit') "
                    "AND r.owner_id=%s AND r.status='RUNNING' AND r.lease_token=%s AND r.lease_until>now() "
                    "AND NOT r.cancel_requested ORDER BY o.created_at,o.action_index "
                    "FOR UPDATE OF o SKIP LOCKED LIMIT 1",
                    (self.run["id"], action_digest(name, args, semantic_digest=semantic_digest),
                     self.run["owner_id"], self.run["lease_token"]),
                )
            ).fetchone()
            if not row:
                raise PermissionError("No current single-use approval for this operation")
            await connection.execute(
                "UPDATE operation_executions SET status='STARTED',lease_token=%s,started_at=now() WHERE id=%s",
                (self.run["lease_token"], row["id"]),
            )
            return row["id"]

    async def finish(self, identifier, exit_code: int, output: str) -> None:
        await self.database.execute(
            "UPDATE operation_executions SET status=%s,exit_code=%s,output=%s,finished_at=now() "
            "WHERE id=%s AND lease_token=%s AND status IN ('STARTED','UNKNOWN')",
            (
                "SUCCEEDED" if exit_code == 0 else "FAILED",
                exit_code,
                output[:8192],
                identifier,
                self.run["lease_token"],
            ),
        )

    async def unknown(self, identifier) -> None:
        await self.database.execute(
            "UPDATE operation_executions SET status='UNKNOWN' WHERE id=%s AND lease_token=%s AND status='STARTED'",
            (identifier, self.run["lease_token"]),
        )
