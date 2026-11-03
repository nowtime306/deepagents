"""Remove explicitly recorded local-provider test ledgers after API-owned source cleanup."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.config import Settings
from app.storage import Database


async def cleanup(path: Path) -> None:
    data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    name = data["name"]
    if not name.startswith("知识运营页面验收-") or not name.removeprefix("知识运营页面验收-").isdigit():
        raise ValueError("Only a uniquely named knowledge-page fixture may be removed")
    db = Database(Settings.from_env().database_url)
    await db.pool.open(wait=True, timeout=10)
    try:
        async with db.pool.connection() as connection:
            if (
                data.get("knowledge")
                and await (
                    await connection.execute("SELECT id FROM knowledge_bases WHERE id=%s", (UUID(data["knowledge"]),))
                ).fetchone()
            ):
                raise ValueError("Remove the recorded knowledge base through the API before ledger cleanup")
            if (
                data.get("conversation")
                and await (
                    await connection.execute("SELECT id FROM conversations WHERE id=%s", (UUID(data["conversation"]),))
                ).fetchone()
            ):
                raise ValueError("Remove the recorded conversation through the API before ledger cleanup")
            models, runs = data["models"], [UUID(value) for value in data["runs"]]
            for identifier in models:
                model = await (
                    await connection.execute("SELECT name,is_default FROM model_configs WHERE id=%s", (identifier,))
                ).fetchone()
                if model and (
                    model["name"] not in {name + "-embedding", name + "-rerank", name + "-vision"}
                    or model["is_default"]
                ):
                    raise ValueError("Test model identity mismatch")
            unrecorded = await (
                await connection.execute(
                    "SELECT 1 FROM model_usage WHERE model_id=ANY(%s::text[]) AND NOT run_id=ANY(%s::uuid[]) LIMIT 1",
                    (models, runs),
                )
            ).fetchone()
            if unrecorded:
                raise ValueError("A model ledger is outside the recorded fixture runs; inspect before cleanup")
            active = await (
                await connection.execute(
                    "SELECT 1 FROM task_runs WHERE model_id=ANY(%s::text[]) AND status IN ('PENDING','RUNNING') "
                    "UNION ALL SELECT 1 FROM resource_operations WHERE id=ANY(%s::uuid[]) AND status='RUNNING' LIMIT 1",
                    (models, runs),
                )
            ).fetchone()
            if active:
                raise ValueError("A recorded test run is still active")
            await connection.execute(
                "DELETE FROM model_usage WHERE model_id=ANY(%s::text[]) AND run_id=ANY(%s::uuid[])", (models, runs)
            )
            await connection.execute(
                "DELETE FROM resource_operations WHERE id=ANY(%s::uuid[]) AND resource_id=%s",
                (runs, data.get("knowledge") or None),
            )
            await connection.execute("DELETE FROM model_configs WHERE id=ANY(%s::text[])", (models,))
        print("Recorded knowledge browser fixtures removed")
    finally:
        await db.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(cleanup(parser.parse_args().metadata))
