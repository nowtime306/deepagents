"""Explicit, digest-bound human approval for branch and PR writes; unknown writes never replay."""

import asyncio
import base64
import re
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.permissions import require_account
from app.repository_api import owned
from app.repository_provider import object_sha, provider, sha
from app.repository_review import digest, read_artifact, review
from app.repository_runs import same_identity
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["代码审阅与发布"])


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: str = Field(pattern="^(push|pr)$")
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=16000)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    approve: bool = Field(strict=True)


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue: int | None = Field(default=None, ge=1, le=100000000, strict=True)


async def action_owned(request, identifier: UUID) -> dict:
    user = await identity(request)
    await require_account(request.app.state.db, user["id"], "work.read")
    rows = await request.app.state.db.fetch("SELECT * FROM repository_actions WHERE id=%s AND owner_id=%s",
                                           (identifier, user["id"]))
    if not rows:
        fail(404, "NOT_FOUND", "仓库审批不存在或无权访问")
    return rows[0]


@router.get("/work-runs/{identifier}/repository")
async def detail(identifier: UUID, request: Request) -> dict:
    current = await review(request, identifier)
    actions = await request.app.state.db.fetch("SELECT id,kind,status,payload,digest,result,error_code,expires_at "
        "FROM repository_actions WHERE run_id=%s ORDER BY created_at DESC LIMIT 30", (identifier,))
    saved = await request.app.state.db.fetch("SELECT id,data,created_at FROM repository_evidence WHERE run_id=%s "
                                             "ORDER BY created_at DESC,id DESC LIMIT 50", (identifier,))
    return {"repository": current, "actions": actions, "evidence": saved}


@router.post("/work-runs/{identifier}/repository/prepare", status_code=201)
async def prepare(identifier: UUID, body: Prepare, request: Request) -> dict:
    user = await identity(request)
    current = await review(request, identifier, publishing=True)
    if not current or current["status"] != "COMPLETED" or not current["snapshot_id"]:
        fail(409, "REPOSITORY_NOT_COMPLETE", "仅对已完成且保存成果的仓库任务开放发布审阅")
    if not current["changes"]:
        fail(422, "REPOSITORY_NO_CHANGES", "没有源码变化需要发布")
    row = await owned(request, UUID(current["repository_id"]), enabled=True)
    remote = provider(request, row)
    if (await remote.commit())["sha"] != current["sha"]:
        fail(409, "REPOSITORY_BASE_MOVED", "远端基准已前移，请重新检出后处理冲突，不覆盖原分支")
    payload = {k: current[k] for k in ("repository_id", "revision", "slug", "ref", "sha", "tree", "subdirectory",
                                     "branch", "snapshot_id", "changes")}
    payload["commands"] = [{"name": c["name"], "status": c["status"], "exit_code": c["exit_code"],
                            "args": c["args"], "output": c["output"]} for c in current["commands"]]
    db = request.app.state.db
    pushed = await db.fetch("SELECT result FROM repository_actions WHERE run_id=%s AND kind='push' "
                           "AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1", (identifier,))
    if body.kind == "push" and pushed:
        fail(409, "REPOSITORY_ALREADY_PUSHED", "本次运行已发布固定分支，后续修改请创建新的任务运行")
    if body.kind == "pr":
        if await db.fetch("SELECT 1 FROM repository_actions WHERE run_id=%s AND kind='pr' AND status='SUCCEEDED'",
                          (identifier,)):
            fail(409, "REPOSITORY_ALREADY_PR", "本次运行已创建草稿PR，请到远端查看")
        if not pushed or not body.title:
            fail(422, "REPOSITORY_PR_REQUIRES_PUSH", "请先完成分支发布并填写PR标题")
        if re.fullmatch("[0-9a-f]{40}", current["ref"]):
            fail(422, "REPOSITORY_PR_BASE", "PR目标必须为分支；当前连接使用固定SHA，请配置基准分支后新建任务")
        payload.update(title=body.title, body=body.body, commit=pushed[0]["result"]["commit"])
    identifier_action = uuid4()
    try:
        await db.execute("INSERT INTO repository_actions(id,run_id,owner_id,kind,payload,digest) "
                         "VALUES(%s,%s,%s,%s,%s,%s)",
                         (identifier_action, identifier, user["id"], body.kind, Jsonb(payload), digest(payload)))
    except UniqueViolation:
        fail(409, "REPOSITORY_APPROVAL_EXISTS", "已有待审阅或结果未知的同类操作，请先核对")
    return {"id": identifier_action, "digest": digest(payload), "payload": payload}


async def checkpoint(request, action: dict, result: dict) -> None:
    changed = await request.app.state.db.fetch("UPDATE repository_actions SET result=%s,updated_at=now() "
        "WHERE id=%s AND status='STARTED' RETURNING id", (Jsonb(result), action["id"]))
    if not changed:
        fail(409, "REPOSITORY_WRITE_FENCE", "仓库操作状态已改变，停止后续请求并核查")


async def authority(request, action: dict) -> dict:
    await require_account(request.app.state.db, action["owner_id"], "repositories.publish")
    current = await review(request, action["run_id"], publishing=True)
    payload = action["payload"]
    if (not current or current["status"] != "COMPLETED" or any(current[k] != payload[k] for k in
        ("repository_id", "revision", "slug", "ref", "sha", "tree", "subdirectory", "branch",
         "snapshot_id", "changes"))):
        fail(409, "REPOSITORY_REVIEW_CHANGED", "源码成果或仓库配置已改变，请重新审阅")
    return await owned(request, UUID(payload["repository_id"]), enabled=True)


async def push(request, action: dict, remote) -> dict:
    payload, result = action["payload"], {}
    tree = []
    for change in payload["changes"]:
        await authority(request, action)
        fullpath = payload["subdirectory"] + "/" + change["path"] if payload["subdirectory"] else change["path"]
        blob_sha = None
        if change["artifact_id"]:
            rows = await request.app.state.db.fetch("SELECT id,digest,size FROM work_artifacts WHERE id=%s "
                "AND snapshot_id=%s AND owner_id=%s",
                (change["artifact_id"], payload["snapshot_id"], action["owner_id"]))
            if not rows:
                fail(503, "REPOSITORY_ARTIFACT", "已审阅源码文件缺失")
            content = read_artifact(request.app.state.settings.data_dir / "work-artifacts", rows[0])
            if object_sha(content) != change["sha"]:
                fail(409, "REPOSITORY_REVIEW_CHANGED", "源码摘要与审批不一致")
            blob = await remote.request("POST", "/git/blobs", {"content": base64.b64encode(content).decode(),
                                                              "encoding": "base64"})
            blob_sha = sha(blob["sha"])
            if blob_sha != change["sha"]:
                fail(502, "REPOSITORY_INTEGRITY", "远端写入对象摘要不一致")
        tree.append({"path": fullpath, "mode": change["mode"], "type": "blob", "sha": blob_sha})
    await authority(request, action)
    built = await remote.request("POST", "/git/trees", {"base_tree": payload["tree"], "tree": tree})
    result["tree"] = sha(built["sha"])
    await checkpoint(request, action, result)
    await authority(request, action)
    commit = await remote.request("POST", "/git/commits", {"message": "Owner Agent task " + str(action["run_id"]),
        "tree": result["tree"], "parents": [payload["sha"]]})
    result["commit"] = sha(commit["sha"])
    await checkpoint(request, action, result)
    await authority(request, action)
    if (await remote.commit())["sha"] != payload["sha"]:
        fail(409, "REPOSITORY_BASE_MOVED", "远端基准在审阅后前移，任务分支未发布，请核查")
    await remote.request("POST", "/git/refs", {"ref": "refs/heads/" + payload["branch"], "sha": result["commit"]})
    result["url"] = "https://github.com/" + payload["slug"] + "/tree/" + payload["branch"]
    return result


def pr_result(item: dict, payload: dict) -> dict:
    expected = "https://github.com/" + payload["slug"] + "/pull/"
    number = item.get("number")
    if not isinstance(number, int) or number < 1 or item.get("html_url") != expected + str(number):
        fail(502, "REPOSITORY_PROTOCOL", "PR地址无法核验，请查看远端仓库")
    return {"number": number, "url": item["html_url"], "commit": payload["commit"]}


async def create_pr(request, action: dict, remote) -> dict:
    payload = action["payload"]
    head = await remote.request("GET", "/git/ref/heads/" + quote(payload["branch"], safe=""))
    if head["object"]["sha"] != payload["commit"] or (await remote.commit())["sha"] != payload["sha"]:
        fail(409, "REPOSITORY_BASE_MOVED", "PR基准或任务分支已经改变，请核查")
    await authority(request, action)
    item = await remote.request("POST", "/pulls", {"title": payload["title"], "body": payload["body"],
        "head": payload["branch"], "base": payload["ref"], "draft": True})
    return pr_result(item, payload)


@router.post("/repository-actions/{identifier}/decision")
async def decide(identifier: UUID, body: Decision, request: Request) -> dict:
    action = await action_owned(request, identifier)
    if action["digest"] != body.digest or digest(action["payload"]) != body.digest:
        fail(409, "REPOSITORY_DIGEST", "审批内容摘要不匹配")
    if action["status"] != "PREPARED" or (body.approve and action["expires_at"] <= datetime.now(UTC)):
        fail(409, "REPOSITORY_APPROVAL_EXPIRED", "审批已使用、过期或需要核查，不能再次发送")
    db = request.app.state.db
    if not body.approve:
        changed = await db.fetch("UPDATE repository_actions SET status='REJECTED',updated_at=now() "
            "WHERE id=%s AND status='PREPARED' RETURNING id", (identifier,))
        if not changed:
            fail(409, "REPOSITORY_APPROVAL_USED", "审批已处理")
        return {"status": "REJECTED"}
    row = await authority(request, action)
    remote = provider(request, row)
    if (await remote.commit())["sha"] != action["payload"]["sha"]:
        fail(409, "REPOSITORY_BASE_MOVED", "远端基准已前移，未写入远端，请重新审阅")
    changed = await db.fetch("UPDATE repository_actions SET status='STARTED',updated_at=now() WHERE id=%s "
        "AND status='PREPARED' AND expires_at>now() RETURNING id", (identifier,))
    if not changed:
        fail(409, "REPOSITORY_APPROVAL_USED", "审批已经被其他请求处理")
    try:
        async with asyncio.timeout(180):
            result = (await push(request, action, remote) if action["kind"] == "push"
                      else await create_pr(request, action, remote))
        await checkpoint(request, action, result)
        await db.execute("UPDATE repository_actions SET status='SUCCEEDED',updated_at=now() "
                         "WHERE id=%s AND status='STARTED'", (identifier,))
    except BaseException:
        await db.execute("UPDATE repository_actions SET status='UNKNOWN',error_code='REMOTE_REVIEW_REQUIRED',"
                         "updated_at=now() WHERE id=%s AND status='STARTED'", (identifier,))
        raise
    return {"status": "SUCCEEDED", "result": result}


@router.post("/repository-actions/{identifier}/check")
async def check(identifier: UUID, request: Request) -> dict:
    action = await action_owned(request, identifier)
    if action["status"] not in {"UNKNOWN", "STARTED"}:
        fail(409, "REPOSITORY_NO_CHECK", "只有结果未知的操作需要核查")
    # STARTED can be a live request: a read-only check never changes its write fence.
    row = await authority(request, action)
    remote, payload = provider(request, row), action["payload"]
    result = None
    if action["kind"] == "push" and action["result"].get("commit"):
        ref = await remote.request("GET", "/git/ref/heads/" + quote(payload["branch"], safe=""))
        if ref["object"]["sha"] == action["result"]["commit"]:
            result = {**action["result"], "url": "https://github.com/" + payload["slug"] + "/tree/" + payload["branch"]}
    elif action["kind"] == "pr":
        items = await remote.request("GET", "/pulls?state=all&head="
            + quote(payload["slug"].split("/")[0] + ":" + payload["branch"], safe="")
            + "&base=" + quote(payload["ref"], safe=""))
        matches = [p for p in items if p.get("head", {}).get("sha") == payload["commit"]
                   and p.get("title") == payload["title"] and (p.get("body") or "") == payload["body"]]
        if len(matches) == 1:
            result = pr_result(matches[0], payload)
    if result and action["status"] == "UNKNOWN":
        await request.app.state.db.execute("UPDATE repository_actions SET status='SUCCEEDED',result=%s,"
            "error_code=NULL,updated_at=now() WHERE id=%s AND status='UNKNOWN'", (Jsonb(result), identifier))
        return {"status": "SUCCEEDED", "result": result}
    return {"status": action["status"], "message": "尚不能确认；没有重发任何写入请求"}


@router.post("/work-runs/{identifier}/repository/evidence")
async def evidence(identifier: UUID, body: EvidenceInput, request: Request) -> dict:
    current = await review(request, identifier)
    if not current:
        fail(404, "NOT_FOUND", "此任务未绑定仓库")
    row = await owned(request, UUID(current["repository_id"]), enabled=True)
    if not same_identity(row, current):
        fail(409, "REPOSITORY_CONFLICT", "仓库配置已改变，不使用新凭证读取旧任务证据")
    remote = provider(request, row)
    async def read(path):
        live = await owned(request, UUID(current["repository_id"]), enabled=True)
        await require_account(request.app.state.db, live["owner_id"], "repositories.read")
        if not same_identity(live, current):
            fail(409, "REPOSITORY_CONFLICT", "仓库配置已变化，请停止本次证据读取")
        return await remote.request("GET", path)

    result = {"source": "https://github.com/" + row["slug"], "source_sha": current["sha"], "untrusted": True}
    issue = body.issue
    if issue is not None:
        item = await read("/issues/" + str(issue))
        result["issue"] = {"number": issue, "title": str(item.get("title", ""))[:500],
            "body": str(item.get("body") or "")[:16000], "state": item.get("state"),
            "url": result["source"] + "/issues/" + str(issue)}
    pushed = await request.app.state.db.fetch("SELECT result FROM repository_actions WHERE run_id=%s "
        "AND kind='push' AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1", (identifier,))
    if pushed:
        commit = sha(pushed[0]["result"]["commit"])
        checks = await read("/commits/" + commit + "/check-runs?per_page=100")
        result["checks"] = [{"name": str(c.get("name", ""))[:200], "status": c.get("status"),
                             "conclusion": c.get("conclusion")} for c in checks.get("check_runs", [])[:100]]
        result["checks_truncated"] = checks.get("total_count", 0) > len(result["checks"])
        result["commit"] = commit
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT id FROM work_queue_runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                                 (identifier, user["id"]))
        count = await (await connection.execute("SELECT count(*) AS count FROM repository_evidence WHERE run_id=%s",
                                                (identifier,))).fetchone()
        if count["count"] >= 50:
            fail(422, "REPOSITORY_EVIDENCE_LIMIT", "本次任务已保存50份外部证据，请保留历史，不再重复采集")
        await connection.execute("INSERT INTO repository_evidence(id,run_id,owner_id,data) VALUES(%s,%s,%s,%s)",
                                 (uuid4(), identifier, user["id"], Jsonb(result)))
    return result
