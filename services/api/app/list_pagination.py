"""Scoped opaque keyset cursors; row ownership must still be enforced by every query."""

import base64
import json
from datetime import datetime
from uuid import UUID

from app.errors import fail


def decode_cursor(value: str | None, owner: UUID, scope: str) -> tuple[datetime | None, UUID | None]:
    if value is None:
        return None, None
    try:
        if not 1 <= len(value) <= 1024:
            raise ValueError
        data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if data["owner"] != str(owner) or data["scope"] != scope:
            raise ValueError
        timestamp = datetime.fromisoformat(data["time"])
        if not isinstance(data["id"], str):
            raise ValueError
        identifier = UUID(data["id"])
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp, identifier
    except (ValueError, TypeError, KeyError):
        fail(422, "INVALID_CURSOR", "分页位置无效，请刷新列表后重试")


def page(rows: list[dict], limit: int, owner: UUID, scope: str, *, time_key: str = "created_at") -> dict:
    items = rows[:limit]
    cursor = None
    if len(rows) > limit:
        last = items[-1]
        cursor = base64.urlsafe_b64encode(json.dumps({
            "owner": str(owner), "scope": scope, "time": last[time_key].isoformat(), "id": str(last["id"]),
        }).encode()).decode()
    return {"items": items, "next_cursor": cursor}
