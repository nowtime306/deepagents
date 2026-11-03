"""Bounded recovery outbox: provider latency never affects account lookup responses."""

import asyncio
import contextlib
import json
import logging
import smtplib
from types import SimpleNamespace

import httpx
import psycopg

from app.errors import APIError
from app.identity_api import vault
from app.identity_recovery import send
from app.maintenance import request_slot

logger = logging.getLogger(__name__)


async def deliver_one(app):
    """Retain shared maintenance admission until external delivery has finished."""
    fence = getattr(app.state, "service_fence", None)
    if fence:
        await fence.check()
    async with request_slot(app.state.settings.database_url):
        return await deliver_admitted(app, fence)


async def deliver_admitted(app, fence):
    """Never replay a sending record whose external result could be unknown."""
    db = app.state.db
    async with db.pool.connection() as connection:
        await connection.execute("DELETE FROM identity_recovery_tokens WHERE expires_at<=now()")
        await connection.execute(
            "UPDATE identity_deliveries SET status='unknown',error_code='DELIVERY_INTERRUPTED',payload_ciphertext='' "
            "WHERE status='sending' AND available_at<=now()"
        )
        row = await (
            await connection.execute(
                "SELECT d.* FROM identity_deliveries d JOIN identity_recovery_tokens t ON t.token_hash=d.token_hash "
                "JOIN identity_contacts c ON c.owner_id=t.owner_id AND c.kind=t.kind AND c.address_hash=t.address_hash "
                "JOIN users u ON u.id=t.owner_id WHERE d.status='pending' AND d.attempts<3 "
                "AND d.available_at<=now() "
                "AND t.expires_at>now() AND c.verified AND u.enabled AND u.approval_status='approved' "
                "ORDER BY d.created_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED"
            )
        ).fetchone()
        if not row:
            return False
        await connection.execute(
            "UPDATE identity_deliveries SET status='sending',attempts=attempts+1,error_code=NULL,"
            "available_at=now()+interval '30 seconds' WHERE id=%s",
            (row["id"],),
        )
    request = SimpleNamespace(app=app)
    try:
        if fence:
            await fence.check()
        payload = json.loads(vault(request).decrypt(row["payload_ciphertext"]).get_secret_value())
    except asyncio.CancelledError:
        await asyncio.shield(
            db.execute(
                "UPDATE identity_deliveries SET status='unknown',error_code='DELIVERY_CANCELLED',payload_ciphertext='' "
                "WHERE id=%s",
                (row["id"],),
            )
        )
        raise
    except APIError:
        await db.execute(
            "UPDATE identity_deliveries SET status='pending',attempts=attempts-1 WHERE id=%s", (row["id"],)
        )
        raise
    except (ValueError, KeyError, TypeError):
        await db.execute(
            "UPDATE identity_deliveries SET status='failed',error_code='DELIVERY_PAYLOAD_INVALID',"
            "payload_ciphertext='' "
            "WHERE id=%s",
            (row["id"],),
        )
        return True
    try:
        await send(request, payload["kind"], payload["address"], payload["token"], "reset")
    except asyncio.CancelledError:
        await asyncio.shield(
            db.execute(
                "UPDATE identity_deliveries SET status='unknown',error_code='DELIVERY_CANCELLED',payload_ciphertext='' "
                "WHERE id=%s",
                (row["id"],),
            )
        )
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout, smtplib.SMTPConnectError):
        logger.warning("Identity recovery connection failed before delivery; retries are bounded")
        exhausted = row["attempts"] + 1 >= 3
        await db.execute(
            "UPDATE identity_deliveries SET status=%s,payload_ciphertext=CASE WHEN %s THEN '' "
            "ELSE payload_ciphertext END,error_code='DELIVERY_CONNECT_FAILED' WHERE id=%s",
            ("failed" if exhausted else "pending", exhausted, row["id"]),
        )
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        logger.warning("Identity recovery delivery result unknown; automatic replay disabled")
        await db.execute(
            "UPDATE identity_deliveries SET status='unknown',error_code='DELIVERY_RESULT_UNKNOWN',"
            "payload_ciphertext='' "
            "WHERE id=%s",
            (row["id"],),
        )
    except APIError:
        await db.execute(
            "UPDATE identity_deliveries SET status='failed',error_code='DELIVERY_CONFIG_UNAVAILABLE',"
            "payload_ciphertext='' "
            "WHERE id=%s",
            (row["id"],),
        )
    else:
        await db.execute(
            "UPDATE identity_deliveries SET status='sent',error_code=NULL,payload_ciphertext='' WHERE id=%s",
            (row["id"],),
        )
    return True


async def delivery_loop(app):
    while True:
        try:
            worked = await deliver_one(app)
        except (psycopg.Error, APIError):
            logger.warning("Identity recovery queue temporarily unavailable")
            worked = False
        if not worked:
            await asyncio.sleep(1)


def start_delivery(app):
    """Call once from API lifespan after migrations and key checks have completed."""
    app.state.identity_delivery_task = asyncio.create_task(delivery_loop(app))


async def stop_delivery(app):
    """Call from API lifespan before the database pool is closed."""
    task = getattr(app.state, "identity_delivery_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
