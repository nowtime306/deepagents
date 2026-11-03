"""Review saved bytes, not a mutable live worktree or model-provided patch."""

import base64
import difflib
import hashlib
import json
from pathlib import Path
from uuid import UUID

from app.errors import fail
from app.permissions import require_account
from app.repository_runs import binding
from app.security import identity


def digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def run_owned(request, identifier: UUID) -> dict:
    user = await identity(request)
    await require_account(request.app.state.db, user["id"], "work.read")
    rows = await request.app.state.db.fetch("SELECT * FROM work_queue_runs WHERE id=%s AND owner_id=%s",
                                           (identifier, user["id"]))
    if not rows:
        fail(404, "NOT_FOUND", "仓库任务不存在或无权访问")
    return rows[0]


def read_artifact(directory: Path, row: dict) -> bytes:
    file = directory / str(row["id"])
    if file.is_symlink() or not file.is_file() or file.stat().st_size != row["size"] or row["size"] > 1024 * 1024:
        fail(503, "REPOSITORY_ARTIFACT", "源码成果缺失或大小异常，请核对恢复备份")
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["digest"]:
        fail(503, "REPOSITORY_ARTIFACT", "源码成果摘要不一致，不能发布")
    return data


async def review(request, identifier: UUID, *, publishing: bool = False) -> dict | None:
    from app.repository_provider import object_sha, source_path

    run = await run_owned(request, identifier)
    row = await binding(request.app.state.db, run, authorize=publishing)
    if not row:
        return None
    db = request.app.state.db
    snapshots = await db.fetch("SELECT s.id,g.metadata FROM work_snapshots s JOIN work_git_snapshots g "
        "ON g.snapshot_id=s.id WHERE s.run_id=%s ORDER BY s.created_at DESC,s.id DESC LIMIT 1", (identifier,))
    info = {"repository_id": str(row["repository_id"]), "revision": row["revision"],
            "slug": row["source"]["slug"], "ref": row["source"]["ref"], "sha": row["source"]["sha"],
            "tree": row["source"]["tree"], "subdirectory": row["source"]["subdirectory"], "branch": row["branch"],
            "status": run["status"], "snapshot_id": None, "changes": [], "commands": []}
    if not snapshots:
        return info
    snapshot = snapshots[0]
    info["snapshot_id"] = str(snapshot["id"])
    current = await db.fetch("SELECT * FROM work_artifacts WHERE snapshot_id=%s AND owner_id=%s "
                            "AND path LIKE '/repo/%%' ORDER BY path", (snapshot["id"], run["owner_id"]))
    files = {item["path"][6:]: item for item in current}
    baseline = {item["path"]: item for item in row["source"]["files"]}
    modes = snapshot["metadata"]["modes"]
    for name in sorted(set(baseline) | set(files)):
        source_path(name)
        old, new = baseline.get(name), files.get(name)
        before = base64.b64decode(old["data"], validate=True) if old else b""
        after = read_artifact(request.app.state.settings.data_dir / "work-artifacts", new) if new else b""
        mode = modes.get(name)
        if new and mode not in {"100644", "100755"}:
            fail(503, "REPOSITORY_ARTIFACT", "源码文件模式缺失")
        if old and new and before == after and old["mode"] == mode:
            continue
        try:
            a, b = before.decode("utf-8"), after.decode("utf-8")
            if "\0" in a or "\0" in b:
                raise UnicodeError
            # Bound input and output; no repository-defined diff/textconv command is executed.
            if len(before) + len(after) > 256 * 1024:
                diff, binary = "文本过长，请下载源码文件核对", False
            else:
                diff = "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                                  fromfile="a/" + name, tofile="b/" + name))[:20000]
                binary = False
        except UnicodeError:
            diff, binary = "二进制文件，请下载后核对", True
        info["changes"].append({"path": name,
            "kind": "deleted" if new is None else "added" if old is None else "modified",
            "mode": mode if new else old["mode"], "before_sha": object_sha(before) if old else None,
            "sha": object_sha(after) if new else None, "size": len(after),
            "artifact_id": str(new["id"]) if new else None,
            "diff": diff, "binary": binary})
    info["commands"] = await db.fetch("SELECT name,args,status,exit_code,output,finished_at FROM operation_executions "
        "WHERE run_id=%s AND name IN ('execute','workflow_code') ORDER BY created_at LIMIT 50", (identifier,))
    return info
