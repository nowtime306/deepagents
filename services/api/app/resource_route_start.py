"""Refresh admission-only routing at the actual execution boundary, once per run."""

from uuid import UUID

from psycopg.types.json import Jsonb

from app.model_routing import resolve


async def at_start(models, owner, run, mode, selection, *, images=False, tools=False):
    """Admission validates consent; execution selects current configuration before first use."""
    identifier = selection.get("route_selection_id")
    if not identifier:
        return None
    db = models.database
    async with db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710031,hashtext(%s))", (str(run) + "lead",))
        row = await (await connection.execute(
            "SELECT s.*,r.primary_model FROM model_route_selections s JOIN model_routes r ON r.id=s.route_id "
            "WHERE s.id=%s AND s.owner_id=%s AND s.run_id=%s", (UUID(identifier), owner, run),
        )).fetchone()
        if not row:
            from app.errors import fail

            fail(403, "ROUTE_SELECTION_INVALID", "运行路由不存在或授权已撤销")
        recorded = await (await connection.execute(
            "SELECT 1 FROM run_resource_snapshots WHERE owner_id=%s AND mode=%s AND run_id=%s LIMIT 1",
            (owner, mode, run),
        )).fetchone()
        used = await (await connection.execute(
            "SELECT 1 FROM model_usage WHERE owner_id=%s AND run_id=%s LIMIT 1", (owner, run),
        )).fetchone()
        if recorded or used:
            return None
        await connection.execute("DELETE FROM model_route_probes WHERE selection_id=%s", (row["id"],))
        await connection.execute("DELETE FROM model_route_selections WHERE id=%s", (row["id"],))
        selected = await resolve(
            models, owner, run, row["primary_model"], enabled=True, scope=row["scope"], images=images,
            allow_vision=images,
            tools=tools,
            openai_native=bool(selection.get("web_enabled") and selection.get("web_mode") == "openai_native"),
            connection=connection,
        )
        selection["route_selection_id"] = str(selected.route_selection_id)
        table = "task_runs" if mode == "ask" else "work_queue_runs"
        await connection.execute(
            "UPDATE " + table + " SET model_id=%s,agent_snapshot=%s WHERE id=%s",
            (selected.id, Jsonb(selection), run),
        )
    return selected
