"""Upgrade real 081 data only in fresh, guarded test databases; never touch deployment data."""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.backup import counts, file_digest, restore_check
from app.config import Settings
from app.main import create_app
from app.models import CredentialVault
from app.notification_channels import dispatch
from app.resource_runtime import resource_run
from app.security import COOKIE, hash_password, token_digest
from app.storage import Database

MIGRATIONS = Path(__file__).parents[2] / "migrations"
ORIGIN = {"Origin": "http://localhost:3100"}


def run_async(coroutine):
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(coroutine)


def apply(connection, low, high):
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY)")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if low <= int(path.name[:3]) <= high:
            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO schema_migrations VALUES(%s)", (path.name,))


async def checkpoints(url, runs):
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        graph = StateGraph(dict)
        graph.add_node("model", lambda state: state)
        graph.add_edge(START, "model")
        graph.add_edge("model", END)
        pending = graph.compile(checkpointer=saver, interrupt_before=["model"])
        for run in runs:
            await pending.ainvoke({"original": "old-checkpoint"}, {"configurable": {"thread_id": "work:" + str(run)}})


def seed(connection, vault):
    ids = {
        key: uuid4()
        for key in (
            "owner",
            "agent",
            "agent_version",
            "workflow",
            "workflow_version",
            "knowledge",
            "document",
            "memory",
            "video",
            "skill",
            "mcp",
            "channel",
            "notice",
            "delivery",
            "task",
            "waiting_mcp",
            "paused_skill",
            "approved_pending",
            "called_pending",
            "checkpoint_pending",
            "clean_pending",
            "completed",
            "cancelled",
            "receipt",
            "bound_workflow",
            "api_workflow",
        )
    }
    ids["model"] = "upgrade-" + uuid4().hex
    connection.execute(
        "INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)",
        (ids["owner"], "upgrade-" + uuid4().hex, hash_password("upgrade-test-password")),
    )
    connection.execute(
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext) VALUES(%s,'legacy','offline',%s,%s)",
        (ids["model"], "http://old-model.invalid/v1", vault.encrypt("upgrade-fake-model-key")),
    )
    connection.execute("INSERT INTO model_capability_declarations VALUES(%s,true)", (ids["model"],))
    connection.execute(
        "INSERT INTO skills(id,owner_id,name,description,files) VALUES(%s,%s,'legacy','legacy',%s)",
        (ids["skill"], ids["owner"], Jsonb({"SKILL.md": "old instructions", "run.py": "print('old')"})),
    )
    connection.execute(
        "INSERT INTO mcp_servers(id,name,url,token_ciphertext,tools) VALUES(%s,'legacy',%s,%s,%s)",
        (
            ids["mcp"],
            "http://old-tool.invalid/mcp",
            vault.encrypt("upgrade-fake-tool-key"),
            Jsonb([{"name": "write", "enabled": True, "read_only": False, "schema": {"type": "object"}}]),
        ),
    )
    snapshot = {
        "id": str(ids["agent"]),
        "name": "legacy",
        "model_id": ids["model"],
        "model_revision": 1,
        "revision": 1,
        "instructions": "unchanged v1",
        "skill_ids": [str(ids["skill"])],
        "skill_pins": {str(ids["skill"]): 1},
        "mcp_ids": [str(ids["mcp"])],
        "mcp_pins": {str(ids["mcp"]): "old-pin"},
    }
    connection.execute(
        "INSERT INTO agents(id,owner_id,name,model_id) VALUES(%s,%s,'legacy',%s)",
        (ids["agent"], ids["owner"], ids["model"]),
    )
    connection.execute(
        "INSERT INTO agent_versions(id,agent_id,owner_id,version,revision,snapshot) VALUES(%s,%s,%s,1,1,%s)",
        (ids["agent_version"], ids["agent"], ids["owner"], Jsonb(snapshot)),
    )
    connection.execute(
        "INSERT INTO workflows(id,owner_id,name,model_id) VALUES(%s,%s,'legacy',%s)",
        (ids["workflow"], ids["owner"], ids["model"]),
    )
    connection.execute(
        "INSERT INTO workflow_versions(id,workflow_id,version,draft_revision,model_id,model_revision,name,instructions,"
        "resource_pins,digest) VALUES(%s,%s,1,1,%s,1,'legacy','unchanged workflow v1',%s,'old-digest')",
        (ids["workflow_version"], ids["workflow"], ids["model"], Jsonb({"models": {ids["model"]: 1}})),
    )
    connection.execute("UPDATE workflows SET current_published_version=1 WHERE id=%s", (ids["workflow"],))
    connection.execute(
        "INSERT INTO knowledge_bases(id,owner_id,name) VALUES(%s,%s,'legacy')", (ids["knowledge"], ids["owner"])
    )
    connection.execute(
        "INSERT INTO documents(id,knowledge_id,name,size,status,vector_status,vector_model_id,vector_model_revision) "
        "VALUES(%s,%s,'legacy.txt',8,'READY','READY',%s,1)",
        (ids["document"], ids["knowledge"], ids["model"]),
    )
    connection.execute(
        "INSERT INTO private_memories(id,owner_id,title,content,vector,vector_model_id,vector_model_revision,"
        "vector_status) "
        "VALUES(%s,%s,'legacy','old memory','[1,0]',%s,1,'READY')",
        (ids["memory"], ids["owner"], ids["model"]),
    )
    connection.execute(
        "INSERT INTO knowledge_image_indexes(knowledge_id,model_id,model_name,base_url,dimensions) "
        "VALUES(%s,%s,'offline','http://old-image.invalid',2)",
        (ids["knowledge"], ids["model"]),
    )
    connection.execute(
        "INSERT INTO knowledge_video_jobs(id,knowledge_id,owner_id,model_id,model_revision,name,size,interval_seconds,"
        "max_frames,status) VALUES(%s,%s,%s,%s,1,'old.mp4',8,1,1,'COMPLETED')",
        (ids["video"], ids["knowledge"], ids["owner"], ids["model"]),
    )
    connection.execute(
        "INSERT INTO notification_channels(id,owner_id,name,kind,endpoint_ciphertext,secret_ciphertext,host_hint) "
        "VALUES(%s,%s,'legacy','webhook',%s,%s,'old-notice.invalid')",
        (ids["channel"], ids["owner"], vault.encrypt("https://old-notice.invalid/hook"), vault.encrypt("old-signing")),
    )
    connection.execute(
        "INSERT INTO notifications(id,owner_id,title,message,target_kind,dedupe_key) "
        "VALUES(%s,%s,'legacy','old message','work','legacy')",
        (ids["notice"], ids["owner"]),
    )
    connection.execute(
        "INSERT INTO notification_deliveries(id,notification_id,channel_id,channel_revision) VALUES(%s,%s,%s,1)",
        (ids["delivery"], ids["notice"], ids["channel"]),
    )
    connection.execute(
        "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,'legacy','legacy goal')",
        (ids["task"], ids["owner"]),
    )
    for key, status in (
        ("waiting_mcp", "WAITING_APPROVAL"),
        ("paused_skill", "PAUSED"),
        ("approved_pending", "PENDING"),
        ("called_pending", "PENDING"),
        ("checkpoint_pending", "PENDING"),
        ("clean_pending", "PENDING"),
        ("completed", "COMPLETED"),
        ("cancelled", "CANCELLED"),
    ):
        task = uuid4()
        connection.execute(
            "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,%s,'legacy goal')",
            (task, ids["owner"], key),
        )
        connection.execute(
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,status,agent_snapshot) "
            "VALUES(%s,%s,%s,%s,'old goal',1,%s,%s,%s)",
            (ids[key], task, ids["owner"], key, ids["model"], status, Jsonb(snapshot)),
        )
    connection.execute(
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,input_tokens,"
        "output_tokens) "
        "VALUES(%s,%s,%s,'work',%s,1,'KNOWN',2,1)",
        (uuid4(), ids["called_pending"], ids["owner"], ids["model"]),
    )
    for key, name in (("waiting_mcp", "mcpw_old"), ("paused_skill", "execute"), ("approved_pending", "execute")):
        approval = uuid4()
        payload = {"action_requests": [{"name": name, "args": {"command": "python /skills/legacy/run.py"}}]}
        connection.execute(
            "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,expires_at,decision) "
            "VALUES(%s,%s,%s,%s,%s,'old-approval',now()+interval '1 day',%s)",
            (approval, ids[key], ids["owner"], key, Jsonb(payload), "approve" if key == "approved_pending" else None),
        )
        if key == "approved_pending":
            connection.execute(
                "INSERT INTO operation_executions(id,run_id,approval_id,action_index,name,args,digest,status) "
                "VALUES(%s,%s,%s,0,'execute',%s,'old-receipt','APPROVED')",
                (ids["receipt"], ids[key], approval, Jsonb(payload["action_requests"][0]["args"])),
            )
    for run, kind, state in (
        (ids["waiting_mcp"], "coding", "READY"),
        (ids["paused_skill"], "browser", "UNKNOWN"),
        (ids["approved_pending"], "coding", "RELEASED"),
    ):
        connection.execute(
            "INSERT INTO work_sandbox_sessions(id,run_id,owner_id,kind,state) VALUES(%s,%s,%s,%s,%s)",
            (uuid4(), run, ids["owner"], kind, state),
        )
    for key in ("bound_workflow", "api_workflow"):
        connection.execute(
            "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) VALUES(%s,%s,'RUNNING','old input','{}')",
            (ids[key], ids["workflow"]),
        )
    connection.execute(
        "INSERT INTO work_run_workflows(run_id,workflow_run_id,version_id) VALUES(%s,%s,%s)",
        (ids["waiting_mcp"], ids["bound_workflow"], ids["workflow_version"]),
    )
    key = uuid4()
    connection.execute(
        "INSERT INTO api_credentials(id,owner_id,name,version_id,token_hash,token_hint,expires_at,rpm,concurrency,"
        "internal_only,parent_work_run) VALUES(%s,%s,'old',%s,%s,'old',now()+interval '1 day',1,1,true,%s)",
        (key, ids["owner"], ids["workflow_version"], uuid4().hex, ids["paused_skill"]),
    )
    connection.execute(
        "INSERT INTO external_workflow_jobs(run_id,credential_id,idempotency_key,request_hash) "
        "VALUES(%s,%s,'old','old')",
        (ids["api_workflow"], key),
    )
    return ids


@pytest.fixture
def old_database(tmp_path):
    base = os.getenv("TEST_DATABASE_URL", "")
    if not base:
        pytest.skip("Set TEST_DATABASE_URL to an isolated database ending in _test")
    params = conninfo_to_dict(base)
    assert params["dbname"].endswith("_test") and params["dbname"] != "owner_agent"
    name = "owner_agent_rdc_upgrade_" + uuid4().hex[:12] + "_test"
    url = make_conninfo(**{**params, "dbname": name})
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        vault = CredentialVault(tmp_path / "model.key")
        with psycopg.connect(url, row_factory=dict_row) as connection:
            apply(connection, 1, 81)
            ids = seed(connection, vault)
        checkpoint_runs = [
            ids[key] for key in ("waiting_mcp", "paused_skill", "approved_pending", "checkpoint_pending")
        ]
        run_async(checkpoints(url, checkpoint_runs))
        tables = (
            "users",
            "model_configs",
            "model_usage",
            "model_capability_declarations",
            "agents",
            "agent_versions",
            "workflows",
            "workflow_versions",
            "work_approvals",
            "operation_executions",
            "checkpoints",
            "checkpoint_blobs",
            "skills",
            "mcp_servers",
            "private_memories",
            "knowledge_bases",
            "documents",
            "knowledge_image_indexes",
            "knowledge_video_jobs",
            "notifications",
            "notification_channels",
        )
        with psycopg.connect(url, row_factory=dict_row) as connection:
            before = {
                table: connection.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchall()
                for table in tables
            }
            key_digest = (tmp_path / "model.key").read_bytes()
        yield SimpleNamespace(url=url, ids=ids, vault=vault, before=before, data=tmp_path, key=key_digest)
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            assert name.startswith("owner_agent_rdc_upgrade_") and name.endswith("_test")
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def upgraded(old_database):
    with psycopg.connect(old_database.url) as connection:
        apply(connection, 82, 84)
    return old_database


def test_upgrade_preserves_published_logic_credentials_approvals_and_checkpoints(upgraded):
    case = upgraded
    with psycopg.connect(case.url, row_factory=dict_row) as connection:
        for table, before in case.before.items():
            after = connection.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchall()
            if table in {"mcp_servers", "knowledge_bases"}:
                for row in after:
                    row.pop("configuration_revision")
            if table in {"private_memories", "documents"}:
                for row in after:
                    assert row.pop("vector_fingerprint") is None and row.pop("vector_dimensions") is None
            if table in {"knowledge_image_indexes", "knowledge_video_jobs"}:
                for row in after:
                    assert row.pop("vector_fingerprint") is None
            if table == "model_usage":
                for row in after:
                    assert all(row.pop(key) is None for key in
                               ("resource_snapshot_id", "resource_phase", "effective_parameters"))
            if table == "notification_channels":
                for row in after:
                    assert row.pop("target_digest")
            assert after == before, table
        assert (
            connection.execute("SELECT current_published_version FROM workflows").fetchone()[
                "current_published_version"
            ]
            == 1
        )
        encrypted = connection.execute("SELECT api_key_ciphertext FROM model_configs").fetchone()["api_key_ciphertext"]
        assert case.vault.decrypt(encrypted).get_secret_value() == "upgrade-fake-model-key"
        assert (case.data / "model.key").read_bytes() == case.key
        baselines = connection.execute("SELECT * FROM audit_events WHERE baseline").fetchall()
        assert baselines and all(
            row["operation"] == "BASELINE" and row["source"] == "system:migration082" and row["changes"] == {}
            for row in baselines
        )
        assert not connection.execute("SELECT 1 FROM run_resource_snapshots").fetchone()
        assert not connection.execute("SELECT 1 FROM run_resource_stages").fetchone()


def test_legacy_vectors_stay_unknown_without_invented_compatibility(upgraded):
    with psycopg.connect(upgraded.url, row_factory=dict_row) as connection:
        for table in ("documents", "private_memories", "knowledge_image_indexes", "knowledge_video_jobs"):
            rows = connection.execute(
                sql.SQL("SELECT vector_fingerprint FROM {}").format(sql.Identifier(table))
            ).fetchall()
            assert rows and all(row["vector_fingerprint"] is None for row in rows)
        for table in ("documents", "private_memories"):
            assert (
                connection.execute(
                    sql.SQL("SELECT vector_dimensions FROM {}").format(sql.Identifier(table))
                ).fetchone()["vector_dimensions"]
                is None
            )
        assert not connection.execute("SELECT 1 FROM embedding_compatibility_evidence").fetchone()


def test_legacy_approval_runs_are_held_and_sandboxes_retained(upgraded):
    case = upgraded
    with psycopg.connect(case.url, row_factory=dict_row) as connection:
        rows = {str(row["id"]): row for row in connection.execute("SELECT * FROM work_queue_runs").fetchall()}
        for key in ("waiting_mcp", "paused_skill", "approved_pending", "called_pending", "checkpoint_pending"):
            row = rows[str(case.ids[key])]
            assert row["status"] == "NEEDS_REVIEW" and row["error_code"] == "RESOURCE_HISTORY_MISSING"
            assert row["lease_token"] is None and row["lease_until"] is None
        for key, status in (("clean_pending", "PENDING"), ("completed", "COMPLETED"), ("cancelled", "CANCELLED")):
            assert rows[str(case.ids[key])]["status"] == status and rows[str(case.ids[key])]["error_code"] is None
        assert all(
            row["retention_required"]
            for row in connection.execute(
                "SELECT retention_required FROM work_sandbox_sessions WHERE state<>'RELEASED'"
            ).fetchall()
        )
        assert not connection.execute(
            "SELECT retention_required FROM work_sandbox_sessions WHERE state='RELEASED'"
        ).fetchone()["retention_required"]
        workflows = connection.execute("SELECT status,finished_at FROM workflow_runs").fetchall()
        assert all(row["status"] == "NEEDS_REVIEW" and row["finished_at"] for row in workflows)


def test_old_approvals_cannot_resume_using_current_tool_or_skill_content(upgraded):
    case = upgraded
    token = uuid4().hex
    with psycopg.connect(case.url) as connection:
        connection.execute("UPDATE mcp_servers SET url='http://new-unapproved.invalid/mcp'")
        connection.execute("UPDATE skills SET files=%s,version=version+1", (Jsonb({"run.py": "print('new')"}),))
        connection.execute(
            "INSERT INTO auth_sessions(token_hash,owner_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",
            (token_digest(token), case.ids["owner"]),
        )
    app = create_app(Settings(database_url=case.url, data_dir=case.data))
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with TestClient(app, backend_options={"loop_factory": factory}) as client:
        app.state.settings.work_enabled = True
        client.cookies.set(COOKIE, token)
        for key in ("waiting_mcp", "paused_skill", "approved_pending", "called_pending", "checkpoint_pending"):
            response = client.post(f"/api/v1/work-runs/{case.ids[key]}/resume", headers=ORIGIN)
            assert response.status_code == 409, response.text
        recorded = client.portal.call(app.state.db.fetch, "SELECT count(*) AS n FROM model_usage")
        assert recorded[0]["n"] == len(case.before["model_usage"])
    with psycopg.connect(case.url, row_factory=dict_row) as connection:
        assert connection.execute("SELECT status FROM operation_executions").fetchone()["status"] == "APPROVED"
        assert not connection.execute("SELECT 1 FROM run_resource_snapshots").fetchone()


def test_existing_backup_inventory_includes_new_evidence_tables(upgraded):
    async def inventory():
        database = Database(upgraded.url)
        await database.pool.open()
        try:
            from app.models import ModelRegistry

            models = ModelRegistry(database, upgraded.vault)
            run = uuid4()
            async with resource_run(database, upgraded.ids["owner"], "resource", run):
                await models.get(upgraded.ids["model"])
            await database.execute(
                "INSERT INTO run_resource_stages(owner_id,mode,run_id,phase,confirmed_by,reason) "
                "VALUES(%s,'resource',%s,2,%s,'test-confirmed')",
                (upgraded.ids["owner"], run, upgraded.ids["owner"]),
            )
            async with resource_run(database, upgraded.ids["owner"], "resource", run):
                await models.get(upgraded.ids["model"])
            snapshots = await database.fetch("SELECT execution_config FROM run_resource_snapshots")
            plaintext = [upgraded.vault.decrypt(row["execution_config"]).get_secret_value() for row in snapshots]
            assert all("upgrade-fake-model-key" not in config for config in plaintext)
            await database.execute(
                "INSERT INTO embedding_compatibility_evidence(model_id,target_key,source_key,dimensions,"
                "evidence_digest,verified_by) VALUES(%s,'test-target','test-source',2,'test-evidence',%s)",
                (upgraded.ids["model"], upgraded.ids["owner"]),
            )
        finally:
            await database.pool.close()
        async with await psycopg.AsyncConnection.connect(upgraded.url) as connection:
            return await counts(connection)

    inventory = run_async(inventory())
    assert inventory["run_resource_stages"] == 2 and inventory["run_resource_snapshots"] == 2
    assert inventory["embedding_compatibility_evidence"] == 1


def test_legacy_pending_notification_never_uses_a_new_channel_target(upgraded, monkeypatch):
    def forbidden_delivery(*_):
        raise AssertionError("A delivery with missing historical target/message must never reach transport")

    monkeypatch.setattr("app.notification_channels.deliver", forbidden_delivery)
    with psycopg.connect(upgraded.url) as connection:
        connection.execute(
            "UPDATE notification_channels SET endpoint_ciphertext=%s,target_digest='new-target',"
            "host_hint='new-unapproved.invalid',revision=revision+1",
            (upgraded.vault.encrypt("https://new-unapproved.invalid/hook"),),
        )

    async def deliver_once():
        database = Database(upgraded.url)
        await database.pool.open()
        try:
            await dispatch(SimpleNamespace(db=database, models=SimpleNamespace(vault=upgraded.vault)))
        finally:
            await database.pool.close()

    run_async(deliver_once())
    with psycopg.connect(upgraded.url, row_factory=dict_row) as connection:
        row = connection.execute("SELECT * FROM notification_deliveries").fetchone()
        assert row["status"] == "PAUSED" and row["error_code"] == "DELIVERY_HISTORY_MISSING"
        assert row["target_digest"] is None and row["message_snapshot"] is None
        assert connection.execute("SELECT message FROM notifications").fetchone()["message"] == "old message"


def test_failed_upgrade_rolls_back_schema_and_legacy_hold_as_one_transaction(old_database):
    with pytest.raises(psycopg.errors.DivisionByZero), psycopg.connect(old_database.url) as connection:
        apply(connection, 82, 84)
        connection.execute("SELECT 1/0")
    with psycopg.connect(old_database.url, row_factory=dict_row) as connection:
        assert connection.execute("SELECT to_regclass('run_resource_snapshots') AS name").fetchone()["name"] is None
        assert not connection.execute(
            "SELECT 1 FROM schema_migrations WHERE left(version,3) IN ('082','083','084')"
        ).fetchone()
        statuses = {row["id"]: row["status"] for row in connection.execute("SELECT id,status FROM work_queue_runs")}
        assert statuses[old_database.ids["waiting_mcp"]] == "WAITING_APPROVAL"
        assert statuses[old_database.ids["paused_skill"]] == "PAUSED"
        assert statuses[old_database.ids["approved_pending"]] == "PENDING"
        assert statuses[old_database.ids["called_pending"]] == "PENDING"
        assert statuses[old_database.ids["checkpoint_pending"]] == "PENDING"
        for table in ("work_approvals", "operation_executions", "checkpoints"):
            assert connection.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchall() == (
                old_database.before[table]
            )


def test_old_backup_manifest_is_checked_before_new_baseline_migration(old_database, monkeypatch):
    """Exercise restore orchestration with a PostgreSQL fixture clone in place of the pg_restore utility."""
    case = old_database
    target = case.data / "backup-fixture"
    (target / "data").mkdir(parents=True)
    (target / "database.dump").write_bytes(b"controlled pg_restore replacement")
    shutil.copyfile(case.data / "model.key", target / "data/model.key")

    async def original_counts():
        async with await psycopg.AsyncConnection.connect(case.url) as connection:
            return await counts(connection)

    source_counts = run_async(original_counts())
    assert source_counts["audit_events"] == 0
    (target / "manifest.json").write_text(json.dumps({
        "format": 1, "counts": source_counts,
        "files": {name: file_digest(target / name) for name in ("database.dump", "data/model.key")},
    }))

    def restore_fixture(arguments, **_):
        assert "pg_restore" in arguments
        name = arguments[arguments.index("-d") + 1]
        assert name.startswith("owner_agent_restore_test_")
        donor = conninfo_to_dict(case.url)["dbname"]
        assert donor.startswith("owner_agent_rdc_upgrade_") and donor.endswith("_test")
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(donor)))

    monkeypatch.setattr("app.backup.container_check", lambda: None)
    monkeypatch.setattr("app.backup.docker", restore_fixture)
    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"], data_dir=case.data)
    result = run_async(restore_check(settings, target))
    assert result["records_match"] and result["credentials_decryptable"] >= 4


def test_upgrade_accepts_a_schema_without_checkpoint_tables(old_database):
    with psycopg.connect(old_database.url) as connection:
        connection.execute("DROP TABLE checkpoints,checkpoint_blobs,checkpoint_writes,checkpoint_migrations")
        apply(connection, 82, 84)
        assert connection.execute("SELECT status FROM work_queue_runs WHERE id=%s",
                                  (old_database.ids["called_pending"],)).fetchone()[0] == "NEEDS_REVIEW"
        assert connection.execute("SELECT status FROM work_queue_runs WHERE id=%s",
                                  (old_database.ids["clean_pending"],)).fetchone()[0] == "PENDING"
        assert connection.execute("SELECT to_regprocedure('rdc_083_has_checkpoint(uuid)')").fetchone()[0] is None
