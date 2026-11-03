"""Remove only explicitly recorded, uniquely named account browser-test fixtures."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.config import Settings
from app.storage import Database


async def cleanup(path: Path) -> None:
    data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    database = Database(Settings.from_env().database_url)
    await database.pool.open(wait=True, timeout=10)
    try:
        async with database.pool.connection() as connection:
            users = [(UUID(row["id"]), row["username"]) for row in data if row["kind"] == "user"]
            for identifier, username in users:
                if not username.startswith("acct-test-"):
                    raise ValueError("Only named account-test fixtures may be removed")
                row = await (
                    await connection.execute("SELECT username FROM users WHERE id=%s", (identifier,))
                ).fetchone()
                if row and row["username"] != username:
                    raise ValueError("Test identity mismatch")
                cursor = await connection.execute(
                    "SELECT 1 FROM conversations WHERE owner_id=%s LIMIT 1", (identifier,)
                )
                if await cursor.fetchone():
                    raise ValueError("Test account has conversations; inspect before removing")
            for row in data:
                if row["kind"] == "invitation":
                    await connection.execute(
                        "DELETE FROM account_invitations WHERE id=%s AND name LIKE '角色验收-%%'", (UUID(row["id"]),)
                    )
            for identifier, username in users:
                await connection.execute("DELETE FROM auth_sessions WHERE owner_id=%s", (identifier,))
                await connection.execute("DELETE FROM audit_events WHERE actor_id=%s", (identifier,))
                await connection.execute("DELETE FROM users WHERE id=%s AND username=%s", (identifier, username))
            for row in data:
                if row["kind"] == "role":
                    await connection.execute(
                        "DELETE FROM platform_roles WHERE id=%s AND NOT built_in AND name LIKE '角色验收-%%'",
                        (str(UUID(row["id"])),),
                    )
        print(json.dumps({"recorded_test_users_removed": len(users)}, ensure_ascii=False))
    finally:
        await database.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(cleanup(parser.parse_args().metadata))
