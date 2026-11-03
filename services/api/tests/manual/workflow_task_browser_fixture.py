"""Inspect and remove only the recorded, zero-model local workflow task acceptance fixture."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from opensandbox.config import ConnectionConfig
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import Settings
from app.durable_sandboxes import DurableSandboxes
from app.storage import Database
from app.work_artifacts import WorkArtifacts

META = Path(__file__).parents[2] / ".local" / "workflow-task-browser.json"


async def execute(action):
    settings = Settings.from_env()
    fixture = json.loads(META.read_text(encoding="utf-8"))
    assert fixture["name"].startswith("固定流程重启验收-")
    async with await AsyncConnection.connect(settings.database_url, row_factory=dict_row) as connection:
        assert connection.info.dbname == "owner_agent"
        source = await (await connection.execute("SELECT w.name,w.owner_id,u.is_admin,u.username "
            "FROM workflows w JOIN users u ON u.id=w.owner_id WHERE w.id=%s", (fixture["id"],))).fetchone()
        assert source and source["name"] == fixture["name"] and source["is_admin"] and source["username"] == "admin"
        roots = await (await connection.execute(
            "SELECT r.id,r.task_id,w.workflow_run_id FROM work_run_workflows w "
            "JOIN work_queue_runs r ON r.id=w.run_id JOIN workflow_runs f ON f.id=w.workflow_run_id "
            "WHERE f.workflow_id=%s AND r.owner_id=%s", (fixture["id"], source["owner_id"]),
        )).fetchall()
        assert len(roots) <= 1, "Do not guess which of multiple runs belongs to the browser test"
        if roots and not fixture.get("run"):
            fixture.update({"run": str(roots[0]["id"]), "task": str(roots[0]["task_id"]),
                            "workflow_run": str(roots[0]["workflow_run_id"])})
            META.write_text(json.dumps(fixture), encoding="utf-8")
        if fixture.get("run"):
            root = await (await connection.execute(
                "SELECT id,owner_id,task_id,status,model_calls,result FROM work_queue_runs WHERE id=%s",
                (fixture["run"],),
            )).fetchone()
            assert root and root["owner_id"] == source["owner_id"] and str(root["task_id"]) == fixture["task"]
            assert root["model_calls"] == 0
            assert not await (await connection.execute(
                "SELECT 1 FROM model_usage WHERE run_id=%s", (root["id"],)
            )).fetchone()
            if action == "check":
                if fixture["stage"] == "waiting_restart":
                    assert root["status"] == "WAITING_APPROVAL"
                    flow = await (await connection.execute("SELECT status FROM workflow_runs WHERE id=%s",
                                                          (fixture["workflow_run"],))).fetchone()
                    assert flow["status"] == "RUNNING"
                elif fixture["stage"] == "completed":
                    assert root["status"] == "COMPLETED"
                    assert root["result"]["text"].strip() == "HELLO-code"
                    assert root["result"]["verification"]["successful_code_nodes"] == 1
                    counter = await (await connection.execute(
                        "SELECT id,digest,size FROM work_artifacts WHERE run_id=%s AND path='/workflow-ui-counter.txt' "
                        "AND snapshot_id=(SELECT id FROM work_snapshots WHERE run_id=%s "
                        "ORDER BY created_at DESC,id DESC LIMIT 1)", (root["id"], root["id"]),
                    )).fetchone()
                    assert counter
                    raw = WorkArtifacts(None, settings.data_dir / "work-artifacts").path(counter["id"]).read_bytes()
                    assert raw == b"1" and counter["size"] == 1
                    assert hashlib.sha256(raw).hexdigest() == counter["digest"]
                    count = await (await connection.execute(
                        "SELECT count(*) AS count FROM workflow_node_events WHERE run_id=%s "
                        "AND node_id='upper' AND event='node.completed'", (fixture["workflow_run"],),
                    )).fetchone()
                    assert count["count"] == 1
                print(json.dumps({"recorded_workflow_task": True, "stage": fixture["stage"], "model_calls": 0}))
                return
            assert root["status"] != "RUNNING", "Do not delete an executing task"
        elif action == "check":
            print(json.dumps({"recorded_draft": True}))
            return
    database = Database(settings.database_url)
    await database.pool.open()
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    service = DurableSandboxes(database, ConnectionConfig(
        domain="127.0.0.1:18180", api_key=key, use_server_proxy=True
    ))
    try:
        if fixture.get("run"):
            sessions = await database.fetch("SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (fixture["run"],))
            for session in sessions:
                released = await service.release(session["id"])
                assert released, "Remote absence must be confirmed before deleting the fixture"
            files = await database.fetch("SELECT id FROM work_artifacts WHERE run_id=%s", (fixture["run"],))
            storage = WorkArtifacts(database, settings.data_dir / "work-artifacts")
            for file in files:
                storage.path(file["id"]).unlink(missing_ok=True)
        async with database.pool.connection() as connection:
            if fixture.get("run"):
                await connection.execute("DELETE FROM work_sandbox_sessions WHERE run_id=%s", (fixture["run"],))
                await connection.execute("DELETE FROM work_queue_runs WHERE id=%s AND owner_id=%s",
                                         (fixture["run"], source["owner_id"]))
                await connection.execute("DELETE FROM work_tasks WHERE id=%s AND owner_id=%s",
                                         (fixture["task"], source["owner_id"]))
            await connection.execute("DELETE FROM workflows WHERE id=%s AND owner_id=%s",
                                     (fixture["id"], source["owner_id"]))
        META.unlink()
        print("Removed only the recorded zero-model workflow task and its artifacts")
    finally:
        await service.close()
        await database.pool.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "cleanup"])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(execute(parser.parse_args().action))


if __name__ == "__main__":
    main()
