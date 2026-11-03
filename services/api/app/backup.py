"""Offline, integrity-checked backup and isolated restore verification for local deployment."""

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.config import Settings
from app.manage import migrate
from app.models import CredentialVault
from app.storage import WORK_MAINTENANCE_LOCK

CONTAINER = "owner-agent-postgres-1"
ROOT = Path(__file__).parents[3]
EXPANSION_TABLES = [
    "monitor_samples", "monitor_rules", "monitor_alerts", "service_instance_metadata",
    "identity_mfa", "identity_challenges", "identity_providers", "identity_oidc_states",
    "identity_bindings", "identity_provider_sessions", "identity_channels", "identity_contacts",
    "identity_recovery_tokens", "identity_pending_contacts", "identity_deliveries",
    "mcp_oauth_clients", "mcp_oauth_flows", "mcp_oauth_grants",
    "extra_cost_prices", "extra_cost_usage", "extra_cost_bill_imports", "extra_cost_holds",
    "knowledge_video_jobs", "knowledge_video_frames",
    "knowledge_quality_policies", "knowledge_quality_reviews",
]
COUNTS = [
    "users",
    "model_configs",
    "conversations",
    "task_runs",
    "work_tasks",
    "work_queue_runs",
    "work_artifacts",
    "private_memories",
    "workflows",
    "workflow_versions",
    "api_credentials",
    "document_chunks",
    "organization_members",
    "model_usage",
    "audit_events",
    "operation_executions",
    "work_input_files",
    "work_recovery_jobs",
    "agent_versions",
    "conversation_projects",
    "platform_roles",
    "account_settings",
    "account_invitations",
    "team_calls",
    "automations",
    "automation_occurrences",
    "agent_batches",
    "batch_items",
    "notifications",
    "notification_channels",
    "notification_deliveries",
    "memory_jobs",
    "memory_candidates",
    "memory_settings",
    "resource_operations",
    "document_revisions",
    "knowledge_ocr_jobs",
    "knowledge_datasets",
    "knowledge_experiments",
    "message_feedback",
    "conversation_branches",
    "work_browser_states",
    "work_browser_transports",
    "work_browser_controls",
    "work_browser_actions",
    "conversation_reference_versions",
    "conversation_references",
    "conversation_reference_runs",
    "work_run_workflows",
    "work_task_workflows",
    "repositories",
    "work_run_repositories",
    "work_git_snapshots",
    "repository_actions",
    "repository_evidence",
    "service_instances",
    "service_events",
    "model_capability_declarations",
    "model_routes",
    "model_route_consents",
    "model_route_selections",
    "model_route_probes",
    "model_price_versions",
    "money_budgets",
    "model_money_holds",
    "backup_settings",
    "backup_jobs",
    "platform_maintenance",
    "run_resource_stages",
    "run_resource_snapshots",
    "embedding_compatibility_evidence",
] + EXPANSION_TABLES
DATA = [
    "model.key", "attachments", "work-artifacts", "work-inputs", "speech", "images",
    "knowledge-images", "knowledge-videos", "vectors",
]


def docker(arguments: list[str], **kwargs):
    return subprocess.run(["docker", *arguments], check=True, stderr=subprocess.PIPE, **kwargs)


def container_check() -> None:
    result = json.loads(
        docker(
            ["inspect", "--format", "{{json .Config.Labels}}", CONTAINER], capture_output=False, stdout=subprocess.PIPE
        ).stdout
    )
    if (
        result.get("com.docker.compose.project") != "owner-agent"
        or result.get("com.docker.compose.service") != "postgres"
    ):
        raise RuntimeError("备份仅支持当前项目独立数据库容器")


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    if sys.platform == "win32":
        account = subprocess.run(["whoami"], check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{account}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
        )
    else:
        path.chmod(0o700)


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


async def counts(connection, tables=None) -> dict:
    result = {}
    for table in COUNTS if tables is None else tables:
        optional = {
            "run_resource_stages",
            "run_resource_snapshots",
            "embedding_compatibility_evidence",
            "model_capability_declarations",
            "model_routes",
            "model_route_consents",
            "model_route_selections",
            "model_route_probes",
            "model_price_versions",
            "money_budgets",
            "model_money_holds",
            "backup_settings",
            "backup_jobs",
            "platform_maintenance",
            "service_instances",
            "service_events",
            "repository_evidence",
            "repositories",
            "work_run_repositories",
            "work_git_snapshots",
            "repository_actions",
            "conversation_projects",
            "platform_roles",
            "account_settings",
            "account_invitations",
            "team_calls",
            "automations",
            "automation_occurrences",
            "agent_batches",
            "batch_items",
            "notifications",
            "notification_channels",
            "notification_deliveries",
            "memory_jobs",
            "memory_candidates",
            "memory_settings",
            "resource_operations",
            "document_revisions",
            "knowledge_ocr_jobs",
            "knowledge_datasets",
            "knowledge_experiments",
            "message_feedback",
            "conversation_branches",
            "work_browser_states",
            "work_browser_transports",
            "work_browser_controls",
            "work_browser_actions",
            "conversation_reference_versions",
            "conversation_references",
            "conversation_reference_runs",
            "work_run_workflows",
            "work_task_workflows",
        }
        if (table in optional or table in EXPANSION_TABLES) and tables is None:
            exists = await (await connection.execute("SELECT to_regclass(%s)", (table,))).fetchone()
            if exists[0] is None:
                continue
        result[table] = (
            await (
                await connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            ).fetchone()
        )[0]
    return result


async def create(settings: Settings) -> Path:
    """Hold API/worker exclusion locks while copying a consistent offline deployment."""
    container_check()
    database = conninfo_to_dict(settings.database_url).get("dbname")
    if database != "owner_agent":
        raise RuntimeError("此备份命令只处理 owner_agent，测试数据库不混入生产备份")
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as connection:
        for lock_id in (710014, WORK_MAINTENANCE_LOCK):
            acquired = (await (await connection.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))).fetchone())[0]
            if not acquired:
                raise RuntimeError("请先停止本项目 API 和 Worker，再创建离线备份")
        active = await connection.execute(
            "SELECT 1 FROM work_worker_instances WHERE heartbeat_at>now()-interval '45 seconds' "
            "UNION ALL SELECT 1 FROM work_queue_runs WHERE status='RUNNING' LIMIT 1"
        )
        if await active.fetchone():
            raise RuntimeError("Worker 尚未停止或仍有执行中任务，请等待安全退出后再备份")
        registry = await (await connection.execute("SELECT to_regclass('service_instances')")).fetchone()
        if registry[0]:
            live = await connection.execute(
                "SELECT 1 FROM service_instances WHERE kind<>'ops' AND heartbeat_at>now()-interval '45 seconds' LIMIT 1"
            )
            if await live.fetchone():
                raise RuntimeError("服务实例仍在退出或心跳尚未过期，请等待安全停止再备份")
        target = settings.data_dir / "backups" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
        private_directory(target)
        with (target / "database.dump").open("xb") as output:
            docker(
                ["exec", CONTAINER, "pg_dump", "-U", "agent", "-d", database, "-Fc", "--no-owner", "--no-acl"],
                stdout=output,
            )
        for name in DATA:
            source = settings.data_dir / name
            if source.is_symlink():
                raise RuntimeError("备份源不能使用符号链接")
            if source.is_dir():
                for file in source.rglob("*"):
                    if file.is_symlink():
                        raise RuntimeError("备份数据包含符号链接")
                shutil.copytree(source, target / "data" / name)
            elif source.is_file():
                (target / "data").mkdir(exist_ok=True)
                shutil.copy2(source, target / "data" / name)
        # Model credentials remain encrypted; this master key and local connection settings are private backup material.
        for name, source in {
            "api.env": ROOT / "services/api/.env",
            "admin.env": ROOT / "services/api/.env.admin",
            "compose.env": ROOT / "infra/compose/.env",
        }.items():
            if source.is_file():
                shutil.copy2(source, target / name)
        sandbox_root = ROOT / "services/sandbox"
        (target / "sandbox").mkdir()
        for source, name in [
            (sandbox_root / "windows.toml", "windows.toml"),
            (sandbox_root / ".local/manager.key", "manager.key"),
        ]:
            if source.is_file():
                shutil.copy2(source, target / "sandbox" / name)
        manager_db = sandbox_root / ".local/opensandbox.db"
        if manager_db.is_file():
            with closing(sqlite3.connect(manager_db.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(target / "sandbox/opensandbox.db")) as copied:
                    source.backup(copied)
        from app.source_archive import create as archive_source

        archive_source(ROOT, target)
        manifest = {
            "format": 1,
            "database": database,
            "counts": await counts(connection),
            "files": {
                str(file.relative_to(target).as_posix()): file_digest(file)
                for file in target.rglob("*")
                if file.is_file()
            },
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return target


def verify_files(target: Path) -> dict:
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("备份格式无效")
    root = target.resolve()
    for name, digest in manifest["files"].items():
        path = target / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
            or file_digest(path) != digest
        ):
            raise ValueError("备份文件校验失败")
    if "database.dump" not in manifest["files"] or "data/model.key" not in manifest["files"]:
        raise ValueError("备份缺少数据库或加密主密钥")
    return manifest


async def quarantine_operations(connection: AsyncConnection) -> None:
    """A restored copy cannot start an inherited maintenance window or daily schedule."""
    exists = await (await connection.execute("SELECT to_regclass('backup_settings')")).fetchone()
    if exists[0]:
        await connection.execute("UPDATE backup_settings SET enabled=false,next_due_at=NULL")
        await connection.execute("UPDATE platform_maintenance SET active=false,job_id=NULL,lease_token=NULL")
        await connection.execute(
            "UPDATE backup_jobs SET status='FAILED',phase='RESTORED_REVIEW',"
            "reason='RESTORED_REVIEW',lease_token=NULL WHERE status IN ('PENDING','RUNNING')"
        )


async def quarantine_browsers(connection: AsyncConnection) -> None:
    """Preserve saved login state but invalidate every restored live browser connection."""
    await connection.execute(
        "UPDATE work_browser_controls SET status='CLOSED',error_code='RESTORED_REVIEW',updated_at=now()"
    )
    await connection.execute("UPDATE work_browser_transports SET status='CLOSED',token_ciphertext='',updated_at=now()")
    await connection.execute("UPDATE work_browser_states SET status='REVIEW'")


async def quarantine_ask_accounts(connection: AsyncConnection) -> None:
    """Prevent restored invitations or queued conversations from taking effect automatically."""
    await connection.execute(
        "WITH ended AS (UPDATE task_runs SET status='FAILED',error_code='RESTORED_REVIEW',"
        "finished_at=now(),cancel_requested=true,lease_token=NULL,lease_until=NULL,"
        "next_event_id=next_event_id+1 WHERE status IN ('PENDING','RUNNING') "
        "RETURNING id,next_event_id) INSERT INTO run_events(run_id,event_id,event,data) "
        "SELECT id,next_event_id,'run.failed',"
        '\'{"message":"恢复副本需要核对，不自动执行旧请求"}\'::jsonb FROM ended'
    )
    await connection.execute("DELETE FROM ask_worker_instances")
    await connection.execute("UPDATE account_settings SET registration_mode='closed' WHERE id=1")
    await connection.execute("UPDATE account_invitations SET revoked_at=COALESCE(revoked_at,now())")
    await connection.execute(
        "UPDATE team_calls SET status='UNKNOWN',error_code='RESTORED_REVIEW',finished_at=now() WHERE status='RUNNING'"
    )
    await connection.execute("UPDATE automations SET enabled=false,paused_at=now(),last_error='RESTORED_REVIEW'")
    await connection.execute(
        "UPDATE agent_batches SET status='PAUSED',last_error='RESTORED_REVIEW' WHERE status='ACTIVE'"
    )
    await connection.execute(
        "UPDATE notification_deliveries SET status='UNKNOWN',error_code='RESTORED_REVIEW',finished_at=now(),"
        "lease_token=NULL,lease_until=NULL WHERE status IN ('PENDING','SENDING')"
    )
    await connection.execute(
        "UPDATE memory_jobs SET status='UNKNOWN',error_code='RESTORED_REVIEW',finished_at=now() WHERE status='PENDING'"
    )
    await connection.execute(
        "UPDATE knowledge_ocr_jobs SET status='UNKNOWN',error_code='RESTORED_REVIEW',finished_at=now() "
        "WHERE status IN ('PREPARING','PENDING')"
    )
    await connection.execute(
        "UPDATE knowledge_experiments SET status='UNKNOWN',error_code='RESTORED_REVIEW',finished_at=now(),"
        "cancel_requested=true,lease_until=now() WHERE status IN ('PENDING','RUNNING')"
    )
    await connection.execute(
        "UPDATE resource_operations SET status='UNKNOWN',error_code='RESTORED_REVIEW',finished_at=now(),"
        "lease_until=now(),cancel_requested=true WHERE status='RUNNING'"
    )


async def quarantine_expansion(connection: AsyncConnection) -> None:
    """Keep restored credentials and history, without replaying external work or live container IDs."""
    await connection.execute(
        "UPDATE identity_deliveries SET status='unknown',error_code='RESTORED_REVIEW',payload_ciphertext='' "
        "WHERE status IN ('pending','sending')"
    )
    for table in ("identity_challenges", "identity_oidc_states", "identity_recovery_tokens", "mcp_oauth_flows"):
        await connection.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
    await connection.execute("UPDATE identity_providers SET enabled=false")
    await connection.execute("UPDATE identity_channels SET enabled=false")
    await connection.execute("UPDATE monitor_rules SET enabled=false")
    await connection.execute(
        "UPDATE knowledge_video_jobs SET status='CANCELLED',cancel_requested=true,holds_memory=false,"
        "container_id=NULL,image_id=NULL,create_attempted=false,error_code='RESTORED_REVIEW',updated_at=now() "
        "WHERE status IN ('PENDING','RUNNING','UNKNOWN') OR holds_memory"
    )


async def restore_check(settings: Settings, target: Path, *, retain: bool = False) -> dict:
    """Restore only to a fresh named test DB; production is never overwritten."""
    container_check()
    manifest = verify_files(target)
    name = ("owner_agent_restored_" if retain else "owner_agent_restore_test_") + uuid4().hex[:8]
    if not re.fullmatch(r"owner_agent_(?:restored|restore_test)_[a-f0-9]{8}", name):
        raise ValueError("恢复目标无效")
    params = conninfo_to_dict(settings.database_url)
    params["dbname"] = name
    retained_success = False
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        try:
            with (target / "database.dump").open("rb") as source:
                docker(
                    [
                        "exec",
                        "-i",
                        CONTAINER,
                        "pg_restore",
                        "-U",
                        "agent",
                        "-d",
                        name,
                        "--no-owner",
                        "--no-acl",
                        "--exit-on-error",
                    ],
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                )
            async with await AsyncConnection.connect(make_conninfo(**params)) as restored:
                if await counts(restored, manifest["counts"].keys()) != manifest["counts"]:
                    raise RuntimeError("恢复后的记录数量不一致")
            # New migrations may add truthful rollout baselines. Compare the original dump before upgrading it.
            await migrate(make_conninfo(**params))
            async with await AsyncConnection.connect(make_conninfo(**params)) as restored:
                vault = CredentialVault(target / "data/model.key", create=False)
                cursor = await restored.execute(
                    "SELECT api_key_ciphertext FROM model_configs WHERE api_key_ciphertext<>'' "
                    "UNION ALL SELECT token_ciphertext FROM mcp_servers WHERE token_ciphertext<>'' "
                    "UNION ALL SELECT api_key_ciphertext FROM search_settings WHERE api_key_ciphertext<>'' "
                    "UNION ALL SELECT webhook_key_ciphertext FROM automations WHERE webhook_key_ciphertext<>'' "
                    "UNION ALL SELECT endpoint_ciphertext FROM notification_channels WHERE endpoint_ciphertext<>'' "
                    "UNION ALL SELECT secret_ciphertext FROM notification_channels WHERE secret_ciphertext<>'' "
                    "UNION ALL SELECT state_ciphertext FROM work_browser_states WHERE state_ciphertext<>'' "
                    "UNION ALL SELECT token_ciphertext FROM work_browser_transports WHERE token_ciphertext<>'' "
                    "UNION ALL SELECT token_ciphertext FROM repositories WHERE token_ciphertext<>''"
                )
                encrypted = await cursor.fetchall()
                for table, column in (
                    ("identity_mfa", "secret_ciphertext"),
                    ("identity_providers", "secret_ciphertext"),
                    ("identity_channels", "config_ciphertext"),
                    ("identity_contacts", "address_ciphertext"),
                    ("identity_pending_contacts", "address_ciphertext"),
                    ("identity_oidc_states", "verifier_ciphertext"),
                    ("identity_deliveries", "payload_ciphertext"),
                    ("mcp_oauth_clients", "secret_ciphertext"),
                    ("mcp_oauth_flows", "verifier_ciphertext"),
                    ("mcp_oauth_grants", "access_ciphertext"),
                    ("mcp_oauth_grants", "refresh_ciphertext"),
                ):
                    cursor = await restored.execute(
                        sql.SQL("SELECT {} FROM {} WHERE {}<>''").format(
                            sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
                        )
                    )
                    encrypted.extend(await cursor.fetchall())
                for row in encrypted:
                    vault.decrypt(row[0])
                sealed = await (await restored.execute(
                    "SELECT execution_config FROM run_resource_snapshots"
                )).fetchall()
                for row in sealed:
                    json.loads(vault.decrypt(row[0]).get_secret_value())
                verified_artifacts = 0
                for table, directory in [
                    ("work_artifacts", "work-artifacts"),
                    ("conversation_attachments", "attachments"),
                    ("work_input_files", "work-inputs"),
                    ("work_git_snapshots", "work-artifacts"),
                ]:
                    cursor = await restored.execute(
                        sql.SQL("SELECT id,digest,size FROM {}").format(sql.Identifier(table))
                    )
                    for identifier, checksum, size in await cursor.fetchall():
                        file = target / "data" / directory / str(identifier)
                        if not file.is_file() or file.stat().st_size != size or file_digest(file) != checksum:
                            raise RuntimeError("恢复后的成果或附件与数据库校验摘要不一致")
                        verified_artifacts += 1
                # Authentication is intentionally disabled in a restored copy until deployment review.
                await restored.execute("DELETE FROM auth_sessions")
                if retain:
                    await restored.execute("DELETE FROM service_instances")
                    await quarantine_operations(restored)
                    await restored.execute(
                        "UPDATE repository_actions SET status='UNKNOWN',"
                        "error_code='RESTORED_REVIEW' WHERE status IN ('PREPARED','STARTED')"
                    )
                    await quarantine_browsers(restored)
                    await quarantine_ask_accounts(restored)
                    await quarantine_expansion(restored)
                    await restored.execute("UPDATE api_credentials SET revoked_at=COALESCE(revoked_at,now())")
                    await restored.execute("UPDATE workflow_versions SET active=false")
                    await restored.execute(
                        "UPDATE workflow_runs SET status='NEEDS_REVIEW' WHERE status IN ('PENDING','RUNNING')"
                    )
                    await restored.execute(
                        "UPDATE work_queue_runs SET status='NEEDS_REVIEW',lease_token=NULL,lease_until=NULL,"
                        "resume_decision=NULL,error_code='RESTORED_REVIEW' "
                        "WHERE status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED')"
                    )
                    await restored.execute(
                        "UPDATE operation_executions SET status='UNKNOWN' WHERE status IN ('STARTED','APPROVED')"
                    )
                    await restored.execute("UPDATE model_usage SET status='UNKNOWN' WHERE status='PENDING'")
                    await restored.execute(
                        "UPDATE work_recovery_jobs SET status='FAILED',error_code='RESTORED_REVIEW' "
                        "WHERE status IN ('PENDING','RUNNING')"
                    )
                    await restored.execute("DELETE FROM work_worker_instances")
                    await restored.execute("UPDATE work_sandbox_namespace SET id=gen_random_uuid()")
                    await restored.execute(
                        "UPDATE work_sandbox_sessions SET state='RELEASED',data_lost=retention_required,"
                        "retention_required=false"
                    )
            configuration = None
            if retain:
                directory = settings.data_dir / "restored" / name
                private_directory(directory)
                shutil.copytree(target / "data", directory / "data")
                configuration = directory / "restore.env"
                authority = (
                    quote(params.get("user", "agent"), safe="") + ":" + quote(params.get("password", ""), safe="")
                )
                hostname = params.get("host", "127.0.0.1")
                if ":" in hostname:
                    hostname = "[" + hostname + "]"
                query = urlencode(
                    {
                        key: value
                        for key, value in params.items()
                        if key not in {"user", "password", "host", "port", "dbname"}
                    }
                )
                url = (
                    "postgresql://"
                    + authority
                    + "@"
                    + hostname
                    + ":"
                    + params.get("port", "5432")
                    + "/"
                    + name
                    + ("?" + query if query else "")
                )
                configuration.write_text(
                    "DATABASE_URL="
                    + url
                    + "\nPLATFORM_DATA_DIR="
                    + str(directory / "data")
                    + "\nWORK_ENABLED=false\nMEDIA_WORKER_ENABLED=false\nWEB_ORIGIN="
                    + settings.web_origin
                    + "\n",
                    encoding="utf-8",
                )
                retained_success = True
            return {
                **(
                    {"database_name": name, "configuration_path": str(configuration), "services_started": False}
                    if retain
                    else {}
                ),
                "records_match": True,
                "files_verified": len(manifest["files"]),
                "credentials_decryptable": len(encrypted),
                "referenced_files_verified": verified_artifacts,
            }
        finally:
            # Only the random database created above can be dropped here.
            if not retained_success:
                await admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def main() -> None:
    parser = argparse.ArgumentParser(description="平台离线备份与隔离恢复验证；不覆盖生产数据库")
    parser.add_argument("command", choices=["create", "verify", "restore"])
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    ) as runner:
        if args.command == "create":
            print(runner.run(create(settings)))
        elif args.backup:
            print(
                json.dumps(
                    runner.run(restore_check(settings, args.backup, retain=args.command == "restore")),
                    ensure_ascii=False,
                )
            )
        else:
            parser.error("verify/restore 需要 --backup 目录")


if __name__ == "__main__":
    main()
