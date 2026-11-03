"""Backups accept pre-project schemas and count project rows after migration."""

from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.errors import UndefinedTable
from test_api import client as client

from app import backup
from app.security import token_digest


def test_project_backup_count_is_optional_only_before_migration(client, monkeypatch):
    _, app = client
    monkeypatch.setattr(backup, "COUNTS", ["users", "conversation_projects", "team_calls"])

    async def run():
        async with await AsyncConnection.connect(app.state.settings.database_url) as connection:
            schema = sql.Identifier("backup_test_" + uuid4().hex)
            await connection.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
            await connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(schema))
            await connection.execute("CREATE TABLE users(id int)")
            await connection.execute("INSERT INTO users VALUES (1)")
            assert await backup.counts(connection) == {"users": 1}
            await connection.execute("CREATE TABLE conversation_projects(id int)")
            await connection.execute("INSERT INTO conversation_projects VALUES (1)")
            assert await backup.counts(connection) == {"users": 1, "conversation_projects": 1}
            with pytest.raises(UndefinedTable):
                async with connection.transaction():
                    await backup.counts(connection, ["users", "team_calls"])
            await connection.execute("CREATE TABLE team_calls(id int)")
            await connection.execute("INSERT INTO team_calls VALUES (1)")
            assert await backup.counts(connection) == {"users": 1, "conversation_projects": 1, "team_calls": 1}
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))

    connection = client[0]
    connection.portal.call(run)


def test_expansion_backup_counts_allow_old_schema_but_require_manifest_tables(client, monkeypatch):
    c, app = client
    monkeypatch.setattr(backup, "COUNTS", ["users", *backup.EXPANSION_TABLES])
    async def run():
        async with await AsyncConnection.connect(app.state.settings.database_url) as database:
            schema = sql.Identifier("backup_test_" + uuid4().hex)
            await database.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
            await database.execute(sql.SQL("SET LOCAL search_path TO {}").format(schema))
            await database.execute("CREATE TABLE users(id int)")
            assert await backup.counts(database) == {"users": 0}
            with pytest.raises(UndefinedTable):
                async with database.transaction():
                    await backup.counts(database, ["users", "identity_providers"])
            await database.rollback()
    c.portal.call(run)


def test_restored_expansion_preserves_credentials_without_replaying_media_or_login(client):
    c, app = client
    owner = c.get("/api/v1/auth/me").json()["id"]
    async def run():
        async with app.state.db.pool.connection() as database:
            try:
                provider, video = uuid4(), uuid4()
                await database.execute(
                    "INSERT INTO identity_providers(id,name,issuer,client_id,secret_ciphertext,enabled) "
                    "VALUES(%s,'restore','https://invalid.example','client','encrypted-evidence',true)", (provider,))
                await database.execute(
                    "INSERT INTO knowledge_video_jobs(id,owner_id,model_id,model_revision,name,size,interval_seconds,"
                    "max_frames,status,holds_memory,create_attempted,container_id,image_id) "
                    "VALUES(%s,%s,'restore',1,'restore',1,1,1,'RUNNING',true,true,'original-container','original-image')",
                    (video, owner))
                await backup.quarantine_expansion(database)
                row = await (await database.execute(
                    "SELECT * FROM identity_providers WHERE id=%s", (provider,))).fetchone()
                assert not row["enabled"] and row["secret_ciphertext"] == "encrypted-evidence"
                job = await (await database.execute(
                    "SELECT * FROM knowledge_video_jobs WHERE id=%s", (video,))).fetchone()
                assert job["status"] == "CANCELLED" and not job["holds_memory"] and not job["create_attempted"]
                assert job["container_id"] is None and job["image_id"] is None
                assert job["error_code"] == "RESTORED_REVIEW"
                await backup.quarantine_expansion(database)
            finally:
                await database.rollback()
    c.portal.call(run)


def test_restored_ask_and_invitations_require_review_before_reuse(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]

    async def exercise():
        async with app.state.db.pool.connection() as database:
            try:
                conversation, run, invite = uuid4(), uuid4(), uuid4()
                await database.execute(
                    "INSERT INTO conversations(id,owner_id,title) VALUES(%s,%s,'恢复排队样例')", (conversation, owner)
                )
                await database.execute(
                    "INSERT INTO task_runs(id,conversation_id,model_id,status) VALUES(%s,%s,'test','PENDING')",
                    (run, conversation),
                )
                team = uuid4()
                await database.execute(
                    "INSERT INTO team_calls(id,owner_id,ask_run_id,role_name,version_id,model_id,"
                    "tool_call_id,input_digest) "
                    "VALUES(%s,%s,%s,'reviewer',%s,'test','active','fixture')",
                    (team, owner, run, uuid4()),
                )
                await database.execute(
                    "INSERT INTO account_invitations(id,name,token_hash,token_hint,role_id,created_by,expires_at) "
                    "VALUES(%s,'恢复邀请码',%s,'test','member',%s,now()+interval '1 day')",
                    (invite, token_digest(str(invite)), owner),
                )
                await backup.quarantine_ask_accounts(database)
                row = await (await database.execute("SELECT * FROM task_runs WHERE id=%s", (run,))).fetchone()
                assert row["status"] == "FAILED" and row["error_code"] == "RESTORED_REVIEW"
                assert row["cancel_requested"] and row["lease_token"] is None
                member = await (await database.execute("SELECT status FROM team_calls WHERE id=%s", (team,))).fetchone()
                assert member["status"] == "UNKNOWN"
                assert (
                    await (await database.execute("SELECT event FROM run_events WHERE run_id=%s", (run,))).fetchone()
                )["event"] == "run.failed"
                assert (
                    await (
                        await database.execute("SELECT registration_mode FROM account_settings WHERE id=1")
                    ).fetchone()
                )["registration_mode"] == "closed"
                assert (
                    await (
                        await database.execute("SELECT revoked_at FROM account_invitations WHERE id=%s", (invite,))
                    ).fetchone()
                )["revoked_at"]
                await backup.quarantine_ask_accounts(database)
                assert (
                    await (
                        await database.execute("SELECT count(*) AS n FROM run_events WHERE run_id=%s", (run,))
                    ).fetchone()
                )["n"] == 1
            finally:
                # The shared isolated test DB retains its prior records and settings.
                await database.rollback()

    connection.portal.call(exercise)
