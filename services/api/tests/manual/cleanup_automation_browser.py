"""Remove only explicitly recorded automation page fixtures, preserving user records."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.config import Settings
from app.storage import Database


async def cleanup(path: Path):
    data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    name = data["name"]
    if not name.startswith("主动任务页面验收-") or not name.removeprefix("主动任务页面验收-").isdigit():
        raise ValueError("Only named automation browser fixtures may be removed")
    database = Database(Settings.from_env().database_url)
    await database.pool.open(wait=True, timeout=10)
    try:
        async with database.pool.connection() as connection:
            for key, table, field in [
                ("schedule", "automations", "name"),
                ("batch", "agent_batches", "name"),
                ("agent", "agents", "name"),
                ("model", "model_configs", "name"),
                ("channel", "notification_channels", "name"),
            ]:
                if data.get(key):
                    row = await (
                        await connection.execute(f"SELECT {field} FROM {table} WHERE id=%s", (data[key],))
                    ).fetchone()
                    if row and row[field] not in {name, name + "-批量"}:
                        raise ValueError("Fixture name mismatch")
            model = data.get("model")
            active = await (
                await connection.execute(
                    "SELECT 1 FROM task_runs WHERE model_id=%s AND status IN ('PENDING','RUNNING') LIMIT 1",
                    (model,),
                )
            ).fetchone()
            if active:
                raise ValueError("Test run is still active; cancel and wait before cleanup")
            notices = await (
                await connection.execute(
                    "SELECT id FROM notifications WHERE title=ANY(%s::text[])", ([name, name + "-批量"],)
                )
            ).fetchall()
            ids = [row["id"] for row in notices]
            await connection.execute(
                "DELETE FROM notification_deliveries WHERE notification_id=ANY(%s::uuid[])", (ids,)
            )
            await connection.execute("DELETE FROM notifications WHERE id=ANY(%s::uuid[])", (ids,))
            conversations = {UUID(item) for item in data.get("conversations", [])}
            rows = await (
                await connection.execute("SELECT id FROM conversations WHERE agent_id=%s", (data.get("agent") or None,))
            ).fetchall()
            conversations.update(row["id"] for row in rows)
            if conversations:
                await connection.execute(
                    "DELETE FROM conversations WHERE id=ANY(%s::uuid[]) AND agent_id=%s",
                    (list(conversations), data["agent"]),
                )
            if data.get("schedule"):
                await connection.execute(
                    "DELETE FROM automation_occurrences WHERE automation_id=%s", (data["schedule"],)
                )
                await connection.execute("DELETE FROM automations WHERE id=%s", (data["schedule"],))
            if data.get("batch"):
                await connection.execute("DELETE FROM batch_items WHERE batch_id=%s", (data["batch"],))
                await connection.execute("DELETE FROM agent_batches WHERE id=%s", (data["batch"],))
            if data.get("agent"):
                await connection.execute("DELETE FROM agents WHERE id=%s", (data["agent"],))
            if data.get("channel"):
                await connection.execute("DELETE FROM notification_channels WHERE id=%s", (data["channel"],))
            if model:
                await connection.execute("DELETE FROM model_usage WHERE model_id=%s", (model,))
                await connection.execute("DELETE FROM model_configs WHERE id=%s", (model,))
        print("Recorded automation browser fixtures removed")
    finally:
        await database.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(cleanup(parser.parse_args().metadata))
