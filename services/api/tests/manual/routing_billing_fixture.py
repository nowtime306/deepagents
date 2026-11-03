"""Prepare and remove only the separately recorded phase-eight browser identity."""

import argparse
import asyncio
import json
import secrets
from pathlib import Path
from uuid import UUID, uuid4

from app.config import Settings
from app.manage import add_user
from app.storage import Database

META = Path(__file__).parents[2] / ".local/routing-billing-fixture.json"


async def main(command):
    settings = Settings.from_env()
    assert "owner_agent" == settings.database_url.split("/")[-1].split("?")[0]
    if command == "create":
        if META.exists():
            raise RuntimeError("Existing fixture must be inspected and cleaned first")
        username = "phase8-ui-" + uuid4().hex
        password = secrets.token_urlsafe(24)
        await add_user(settings.database_url, username, password, is_admin=True)
        META.write_text(json.dumps({"username": username, "password": password, "prefix": username}), encoding="utf-8")
        print("Prepared one private phase-eight browser account; no provider called")
        return
    data = json.loads(META.read_text(encoding="utf-8"))
    assert data["username"].startswith("phase8-ui-") and len(data["username"]) == 42
    UUID(data["username"].removeprefix("phase8-ui-"))
    database = Database(settings.database_url)
    await database.pool.open(wait=True)
    try:
        async with database.pool.connection() as connection:
            row = await (
                await connection.execute("SELECT id FROM users WHERE username=%s", (data["username"],))
            ).fetchone()
            if not row:
                raise RuntimeError("Recorded fixture account missing")
            owner = row["id"]
            assert not await (
                await connection.execute("SELECT 1 FROM conversations WHERE owner_id=%s", (owner,))
            ).fetchone()
            assert not await (
                await connection.execute("SELECT 1 FROM model_usage WHERE owner_id=%s", (owner,))
            ).fetchone()
            models = await (
                await connection.execute(
                    "SELECT id FROM model_configs WHERE name=ANY(%s)",
                    ([data["prefix"] + "-primary", data["prefix"] + "-backup"],),
                )
            ).fetchall()
            for model in models:
                assert not await (
                    await connection.execute("SELECT 1 FROM model_usage WHERE model_id=%s", (model["id"],))
                ).fetchone()
                await connection.execute("DELETE FROM model_price_versions WHERE model_id=%s", (model["id"],))
                await connection.execute("DELETE FROM model_configs WHERE id=%s", (model["id"],))
            await connection.execute("DELETE FROM agents WHERE owner_id=%s", (owner,))
            await connection.execute("DELETE FROM audit_events WHERE actor_id=%s", (owner,))
            await connection.execute("DELETE FROM auth_sessions WHERE owner_id=%s", (owner,))
            await connection.execute("DELETE FROM users WHERE id=%s AND username=%s", (owner, data["username"]))
        META.unlink()
        print("Removed only the recorded phase-eight account, models, routes, budgets and test prices")
    finally:
        await database.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "cleanup"])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(parser.parse_args().command))
