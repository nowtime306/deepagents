"""Restore a verified backup to a quarantined DB, then materialize only its named Git fixture in a new container."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from opensandbox.config import ConnectionConfig
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict

from app.backup import restore_check
from app.config import Settings
from app.durable_sandboxes import DurableSandboxes
from app.storage import Database
from app.work_artifacts import WorkArtifacts
from app.work_backend import WorkBackend
from app.work_queue import WorkQueue


async def verify(backup):
    settings = Settings.from_env()
    assert conninfo_to_dict(settings.database_url)["dbname"] == "owner_agent"
    fixture = json.loads((settings.data_dir / "repository-browser-fixture.json").read_text(encoding="utf-8"))
    assert fixture["name"].startswith("本机Git恢复验收-")
    result = await restore_check(settings, backup.resolve(), retain=True)
    name = result["database_name"]
    assert re.fullmatch("owner_agent_restored_[0-9a-f]{8}", name)
    configuration = dotenv_values(result["configuration_path"])
    url = configuration["DATABASE_URL"]
    assert conninfo_to_dict(url)["dbname"] == name
    copied = await asyncio.to_thread(Path(configuration["PLATFORM_DATA_DIR"]).resolve)
    restored_root = await asyncio.to_thread((settings.data_dir / "restored" / name).resolve)
    assert copied.is_relative_to(restored_root)
    database = Database(url)
    service = None
    session = sandbox = None
    await database.pool.open(wait=True)
    try:
        namespaces = await database.fetch("SELECT id FROM work_sandbox_namespace")
        async with await AsyncConnection.connect(settings.database_url) as primary:
            original_namespace = (await (await primary.execute("SELECT id FROM work_sandbox_namespace")).fetchone())[0]
        assert namespaces[0]["id"] != original_namespace
        roots = await database.fetch("SELECT r.* FROM work_queue_runs r JOIN work_run_repositories b ON b.run_id=r.id "
            "WHERE r.id=%s AND r.owner_id=%s AND b.repository_id=%s",
            (fixture["run"], fixture["owner"], fixture["repository"]))
        assert len(roots) == 1 and roots[0]["model_calls"] == 0 and roots[0]["title"] == fixture["name"] + " · 流程任务"
        lease = uuid4()
        await database.execute("UPDATE work_queue_runs SET status='RUNNING',cancel_requested=false,"
            "lease_token=%s,lease_until=now()+interval '120 seconds' WHERE id=%s", (lease, fixture["run"]))
        run = {**roots[0], "lease_token": lease}
        queue = WorkQueue(database)

        async def guard():
            assert await queue.renew(run["id"], lease)

        key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
        service = DurableSandboxes(database, ConnectionConfig(
            domain="127.0.0.1:18180", api_key=key, use_server_proxy=True))
        created = await service.create(run["id"], lease, "coding")
        assert created is not None
        session, sandbox = created
        backend = WorkBackend(sandbox, guard)
        await backend.initialize()
        await WorkArtifacts(database, copied / "work-artifacts").restore(run, backend)
        # Trusted read-only commands, no Agent loop, model, user script, remote Git or restored login is executed.
        check = await backend.aexecute("cd /workspace/repo && test ! -e delete.txt "
            "&& test ! -e added-then-deleted.txt && git log -1 --format=%s "
            "&& git branch --show-current && cat main.py /workspace/git-fixture-counter.txt")
        assert check.exit_code == 0, check.output
        assert "local fixture commit" in check.output and "git-workspace-ok" in check.output
        assert "owner-agent/" + fixture["run"] in check.output and check.output.endswith("1")
        assert not await database.fetch("SELECT 1 FROM model_usage WHERE run_id=%s", (fixture["run"],))
        return {**result, "native_git_restored": True, "deleted_files_remain_deleted": True,
                "local_commit_preserved": True, "model_calls": 0,
                "note": "隔离副本未启动平台服务；验证数据库在结束时删除，受保护文件副本保留"}
    finally:
        try:
            if session:
                assert await service.release(session["id"])
            if sandbox:
                await sandbox.close()
            if service:
                await service.close()
        finally:
            await database.pool.close()
        async with await AsyncConnection.connect(settings.database_url, autocommit=True) as connection:
            await connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        print(json.dumps(runner.run(verify(parser.parse_args().backup)), ensure_ascii=False))
