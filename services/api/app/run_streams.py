"""Owner-scoped Ask events and authoritative chat presentation snapshots."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID

import anyio
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.errors import APIError, fail

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


@asynccontextmanager
async def reading() -> AsyncIterator[None]:
    """Finish short SQL transactions before propagating an ASGI disconnect."""
    expired = False
    with anyio.CancelScope(shield=True):
        try:
            async with asyncio.timeout(5):
                yield
        except TimeoutError:
            expired = True
    await anyio.lowlevel.checkpoint_if_cancelled()
    if expired:
        fail(503, "STREAM_READ_TIMEOUT", "读取运行状态超时，请稍后重试")


def cursor_for(request: Request, after: int) -> int:
    """Validate browser reconnection cursors before response headers are sent."""
    try:
        value = int(request.headers.get("last-event-id", str(after)))
    except ValueError:
        fail(422, "INVALID_CURSOR", "事件序号必须是非负整数")
    if not 0 <= value <= 9223372036854775807:
        fail(422, "INVALID_CURSOR", "事件序号超出范围")
    return value


def frame(event: str, data: dict, row: dict | None = None) -> str:
    payload = row if row is not None else {"event": event, "data": data}
    identifier = f"id: {row['event_id']}\n" if row is not None else ""
    encoded = json.dumps(jsonable_encoder(payload, custom_encoder={Decimal: str}), ensure_ascii=False)
    return identifier + f"event: {event}\ndata: " + encoded + "\n\n"


async def run_state(request: Request, identifier: UUID) -> dict:
    """Reuse existing authenticated JSON projections without exposing worker leases."""
    from app.main import owned_run

    row = await owned_run(request, identifier)
    fields = ("id", "conversation_id", "status", "created_at", "claimed_at", "finished_at", "error_code")
    return {key: row.get(key) for key in fields}


async def snapshot(request: Request, identifier: UUID, run: dict) -> dict:
    """Build changed-only presentation data, never invoke execution or control actions."""
    from app.diagnostics_api import diagnostic
    from app.team_api import listing
    from app.usage_api import conversation_usage

    details = await diagnostic(request, identifier, "ask")
    details.get("execution", {}).pop("lease_until", None)
    return {"run": run, "diagnostics": details,
            "usage": await conversation_usage(run["conversation_id"], request),
            "team": await listing(request, identifier, "ask")}


async def events(request: Request, identifier: UUID, cursor: int):
    """Replay committed pages, then follow live writes without holding idle pool slots."""
    previous, next_snapshot, heartbeat = None, 0.0, 0.0
    while not await request.is_disconnected():
        try:
            async with reading():
                run = await run_state(request, identifier)
                rows = await request.app.state.db.fetch(
                    "SELECT * FROM run_events WHERE run_id=%s AND event_id>%s ORDER BY event_id LIMIT 100",
                    (identifier, cursor),
                )
            for row in rows:
                async with reading():
                    await run_state(request, identifier)
                cursor = row["event_id"]
                row = {"schema_version": 1, "thread_id": str(run["conversation_id"]), **row}
                yield frame(row["event"], row["data"], row)
            status = run["status"]
            done = status in TERMINAL and len(rows) < 100
            now = time.monotonic()
            if now >= next_snapshot or done:
                async with reading():
                    data = await snapshot(request, identifier, run)
                    # Authorization may change while the larger presentation reads are in flight.
                    await run_state(request, identifier)
                encoded = frame("stream.snapshot", data)
                if encoded != previous:
                    yield encoded
                    previous = encoded
                next_snapshot = time.monotonic() + 1
                if done:
                    async with reading():
                        tail = await request.app.state.db.fetch(
                            "SELECT event_id FROM run_events WHERE run_id=%s AND event_id>%s LIMIT 1",
                            (identifier, cursor),
                        )
                    if tail:
                        continue
            if done:
                yield frame("stream.end", {"status": status})
                return
            if now >= heartbeat:
                yield ": heartbeat\n\n"
                heartbeat = now + 10
            if len(rows) < 100:
                await asyncio.sleep(0.2)
        except APIError as error:
            yield frame("stream.error", {"status_code": error.status, "code": error.code, "message": error.message})
            return


async def response(request: Request, identifier: UUID, after: int) -> StreamingResponse:
    """Reject invalid cursors and inaccessible resources before beginning a feed."""
    cursor = cursor_for(request, after)
    async with reading():
        await run_state(request, identifier)
    return StreamingResponse(events(request, identifier, cursor), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
