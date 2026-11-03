"""Explicit database migrations and local user provisioning."""

import argparse
import asyncio
import getpass
import secrets
import sys
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection

from app.config import Settings
from app.security import hash_password


async def migrate(url: str) -> None:
    """Apply each SQL migration once, transactionally, then prepare checkpoints."""
    async with await AsyncConnection.connect(url) as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710013)")
        await connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY)")
        for path in sorted((Path(__file__).parents[1] / "migrations").glob("*.sql")):
            cursor = await connection.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.name,))
            if not await cursor.fetchone():
                await connection.execute(path.read_text(encoding="utf-8"))
                await connection.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()


async def add_user(url: str, username: str, password: str, *, is_admin: bool = False) -> None:
    """Create a local user explicitly; never reset an existing account implicitly."""
    if len(password) < 12:
        msg = "密码至少需要 12 个字符"
        raise ValueError(msg)
    async with await AsyncConnection.connect(url) as connection:
        await connection.execute(
            "INSERT INTO users(id, username, password_hash,is_admin,role_id) VALUES (%s,%s,%s,%s,%s)",
            (uuid4(), username, hash_password(password), is_admin, "admin" if is_admin else "member"),
        )


async def bootstrap_admin(url: str) -> None:
    """Provision a random local admin password once, in an ignored local file."""
    async with await AsyncConnection.connect(url) as connection:
        cursor = await connection.execute("SELECT 1 FROM users WHERE username='admin'")
        if await cursor.fetchone():
            return
    password = secrets.token_urlsafe(24)
    path = Path(__file__).parents[1] / ".env.admin"
    with path.open("x", encoding="utf-8") as output:
        output.write(f"ADMIN_USERNAME=admin\nADMIN_PASSWORD={password}\n")
    try:
        await add_user(url, "admin", password, is_admin=True)
    except Exception:
        path.unlink()
        raise


def main() -> None:
    """Run migration or prompt for a local account password."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(description="平台数据库管理")
    parser.add_argument("command", choices=["migrate", "create-user", "bootstrap-admin"])
    parser.add_argument("--username", default="admin")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    if arguments.command == "migrate":
        asyncio.run(migrate(settings.database_url))
    elif arguments.command == "bootstrap-admin":
        asyncio.run(bootstrap_admin(settings.database_url))
    else:
        password = getpass.getpass("请输入密码（至少 12 字符）：")
        asyncio.run(add_user(settings.database_url, arguments.username, password))


if __name__ == "__main__":
    main()
