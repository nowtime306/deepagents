"""Remove only a recorded, isolated memory-page test account and its fixtures."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.config import Settings
from app.storage import Database


async def cleanup(path: Path) -> None:
    data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    username, name = data["username"], data["name"]
    stamp = username.removeprefix("acct-test-memory-")
    if not stamp.isdigit() or name != "记忆页面验收-" + stamp:
        raise ValueError("Only a uniquely named memory-page fixture may be removed")
    database = Database(Settings.from_env().database_url)
    await database.pool.open(wait=True, timeout=10)
    try:
        async with database.pool.connection() as connection:
            if data.get("user"):
                owner = UUID(data["user"])
                row = await (
                    await connection.execute("SELECT username FROM users WHERE id=%s FOR UPDATE", (owner,))
                ).fetchone()
                if row and row["username"] != username:
                    raise ValueError("Test account identity mismatch")
                active = await (
                    await connection.execute("SELECT 1 FROM conversations WHERE owner_id=%s LIMIT 1", (owner,))
                ).fetchone()
                if active:
                    raise ValueError("Remove test conversations through the API before removing its account")
                for table in [
                    "memory_jobs", "memory_settings", "resource_operations", "private_memories",
                    "model_usage", "conversation_projects", "capability_bindings", "auth_sessions",
                ]:
                    await connection.execute(f"DELETE FROM {table} WHERE owner_id=%s", (owner,))
                await connection.execute("DELETE FROM audit_events WHERE actor_id=%s", (owner,))
                await connection.execute(
                    "DELETE FROM account_invitations WHERE id=%s AND name=%s", (data["invitation"] or None, name)
                )
                await connection.execute("DELETE FROM users WHERE id=%s AND username=%s", (owner, username))
            for identifier in data["models"]:
                row = await (
                    await connection.execute("SELECT name FROM model_configs WHERE id=%s", (identifier,))
                ).fetchone()
                if row and row["name"] not in {name + "-chat", name + "-embedding"}:
                    raise ValueError("Test model identity mismatch")
                await connection.execute("DELETE FROM model_usage WHERE model_id=%s", (identifier,))
                await connection.execute("DELETE FROM model_configs WHERE id=%s", (identifier,))
        print("Recorded memory browser fixtures removed")
    finally:
        await database.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(cleanup(parser.parse_args().metadata))
