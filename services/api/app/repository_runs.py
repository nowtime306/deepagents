"""Freeze private repository identity and recheck it at task execution boundaries."""

from uuid import UUID

from psycopg.types.json import Jsonb

from app.errors import fail
from app.permissions import require_account
from app.repository_api import owned
from app.repository_provider import provider


async def freeze(request, owner: UUID, repository: UUID | None, restore: UUID | None = None) -> dict | None:
    previous = []
    if restore:
        previous = await request.app.state.db.fetch(
            "SELECT b.* FROM work_run_repositories b JOIN work_queue_runs r ON r.id=b.run_id "
            "WHERE r.id=%s AND r.owner_id=%s", (restore, owner))
    if previous and repository != previous[0]["repository_id"]:
        fail(422, "REPOSITORY_RECOVERY", "历史仓库成果只能恢复到相同仓库配置")
    if restore and repository and not previous:
        fail(422, "REPOSITORY_RECOVERY", "普通历史成果不能自动套用到新的仓库，请新建任务运行")
    if repository is None:
        return None
    await require_account(request.app.state.db, owner, "repositories.use")
    row = await owned(request, repository, enabled=True)
    if previous:
        if not same_identity(row, previous[0]["source"]):
            fail(409, "REPOSITORY_RECOVERY_REVIEW_REQUIRED", "仓库身份、目标分支或源码范围已变化，请核对后恢复")
        source = previous[0]["source"]
    else:
        source = await provider(request, row).snapshot()
    return {"repository_id": repository, "revision": row["revision"], "source": source}


async def attach(connection, owner: UUID, run: UUID, frozen: dict | None) -> None:
    if frozen is None:
        return
    row = await (await connection.execute("SELECT * FROM repositories WHERE id=%s AND owner_id=%s "
        "FOR SHARE", (frozen["repository_id"], owner))).fetchone()
    if not row or not row["enabled"] or not same_identity(row, frozen["source"]):
        fail(409, "REPOSITORY_CONFLICT", "仓库配置已改变，请重新创建任务")
    await connection.execute("INSERT INTO work_run_repositories(run_id,repository_id,revision,source,branch) "
        "VALUES(%s,%s,%s,%s,%s)", (run, frozen["repository_id"], frozen["revision"], Jsonb(frozen["source"]),
                                "owner-agent/" + str(run)))


async def binding(database, run: dict, *, authorize: bool = True) -> dict | None:
    rows = await database.fetch("SELECT b.*,p.owner_id,p.enabled,p.revision AS current_revision,"
        "p.slug AS current_slug,p.ref AS current_ref,p.subdirectory AS current_subdirectory FROM "
        "work_run_repositories b JOIN repositories p ON p.id=b.repository_id WHERE b.run_id=%s", (run["id"],))
    if not rows:
        return None
    row = rows[0]
    if authorize:
        await require_account(database, run["owner_id"], "repositories.use")
        if row["owner_id"] != run["owner_id"] or not row["enabled"]:
            fail(403, "REPOSITORY_REVOKED", "仓库已停用或授权已撤销")
        current = {key: row["current_" + key] for key in ("slug", "ref", "subdirectory")}
        if not same_identity(current, row["source"]):
            fail(409, "REPOSITORY_RECOVERY_REVIEW_REQUIRED", "仓库身份、目标分支或源码范围变化，请核对后恢复")
    return row


def same_identity(current: dict, source: dict) -> bool:
    """Credentials and display names do not change the approved repository identity."""
    return all(current[key] == source[key] for key in ("slug", "ref", "subdirectory"))
