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
        if previous[0]["revision"] != row["revision"]:
            fail(409, "REPOSITORY_CONFLICT", "仓库配置已改变，历史成果不能自动套用新配置")
        source = previous[0]["source"]
    else:
        source = await provider(request, row).snapshot()
    return {"repository_id": repository, "revision": row["revision"], "source": source}


async def attach(connection, owner: UUID, run: UUID, frozen: dict | None) -> None:
    if frozen is None:
        return
    row = await (await connection.execute("SELECT revision,enabled FROM repositories WHERE id=%s AND owner_id=%s "
        "FOR SHARE", (frozen["repository_id"], owner))).fetchone()
    if not row or not row["enabled"] or row["revision"] != frozen["revision"]:
        fail(409, "REPOSITORY_CONFLICT", "仓库配置已改变，请重新创建任务")
    await connection.execute("INSERT INTO work_run_repositories(run_id,repository_id,revision,source,branch) "
        "VALUES(%s,%s,%s,%s,%s)", (run, frozen["repository_id"], frozen["revision"], Jsonb(frozen["source"]),
                                "owner-agent/" + str(run)))


async def binding(database, run: dict, *, authorize: bool = True) -> dict | None:
    rows = await database.fetch("SELECT b.*,p.owner_id,p.enabled,p.revision AS current_revision FROM "
        "work_run_repositories b JOIN repositories p ON p.id=b.repository_id WHERE b.run_id=%s", (run["id"],))
    if not rows:
        return None
    row = rows[0]
    if authorize:
        await require_account(database, run["owner_id"], "repositories.use")
        if row["owner_id"] != run["owner_id"] or not row["enabled"] or row["revision"] != row["current_revision"]:
            fail(403, "REPOSITORY_REVOKED", "仓库授权或配置已改变，任务不能继续调用")
    return row
