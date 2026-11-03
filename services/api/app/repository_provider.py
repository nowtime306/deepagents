"""Bounded GitHub Git-data protocol; credentials never enter task workspaces."""

import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import quote

import httpx

from app.errors import fail

MAX_SOURCE = 20 * 1024 * 1024
MAX_FILES = 180


def source_path(value: str) -> str:
    """Reject ambiguous Git and platform paths before transferring bytes."""
    if (not isinstance(value, str) or not value or len(value) > 500 or any(ord(c) < 32 for c in value)
        or "\\" in value or ":" in value
        or any(p in {"", ".", ".."} or p.casefold() == ".git" for p in value.split("/"))):
        fail(422, "REPOSITORY_PATH", "仓库包含不安全或过长的文件路径")
    return value


def object_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{40}", value):
        fail(502, "REPOSITORY_PROTOCOL", "仓库服务返回的对象摘要无效")
    return value


class GitHub:
    """Only api.github.com is contacted, with no redirects or automatic retries."""

    def __init__(self, row: dict, token: str) -> None:
        self.row, self.token = row, token

    async def request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        url = "https://api.github.com/repos/" + self.row["slug"] + path
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
                async with client.stream(method, url, json=body, headers=headers) as response:
                    if not 200 <= response.status_code < 300:
                        fail(502, "REPOSITORY_REMOTE", "仓库服务拒绝请求，请检查连接权限或远端状态")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 32 * 1024 * 1024:
                            fail(422, "REPOSITORY_LIMIT", "仓库响应过大，请选择更小的代码子目录")
                    value = json.loads(raw)
                    if not isinstance(value, (dict, list)):
                        fail(502, "REPOSITORY_PROTOCOL", "仓库服务响应不是有效对象")
                    return value
        except (httpx.HTTPError, ValueError):
            fail(502, "REPOSITORY_UNKNOWN", "仓库请求未确认完成；写入结果需核查，不会自动重试")

    async def commit(self, ref: str | None = None) -> dict:
        value = await self.request("GET", "/commits/" + quote(ref or self.row["ref"], safe=""))
        try:
            return {"sha": sha(value["sha"]), "tree": sha(value["commit"]["tree"]["sha"])}
        except (KeyError, TypeError):
            fail(502, "REPOSITORY_PROTOCOL", "仓库提交响应不完整")

    async def snapshot(self) -> dict:
        """Resolve once, then read only immutable object IDs and verify each blob."""
        try:
            async with asyncio.timeout(60):
                return await self._snapshot()
        except TimeoutError:
            fail(504, "REPOSITORY_READ_TIMEOUT", "只读检出超过60秒，请检查网络或选择更小的代码子目录")

    async def _snapshot(self) -> dict:
        commit = await self.commit()
        root = commit["tree"]
        for component in self.row["subdirectory"].split("/") if self.row["subdirectory"] else []:
            tree = await self.request("GET", "/git/trees/" + root)
            entry = next((e for e in tree["tree"] if e["path"] == component and e["type"] == "tree"), None)
            if not entry or tree.get("truncated"):
                fail(422, "REPOSITORY_DIRECTORY", "代码子目录不存在或目录响应不完整")
            root = sha(entry["sha"])
        tree = await self.request("GET", "/git/trees/" + root + "?recursive=1")
        if tree.get("truncated"):
            fail(422, "REPOSITORY_LIMIT", "仓库目录被截断，请选择更小的代码子目录")
        entries, total, seen = [], 0, set()
        for entry in tree["tree"]:
            source_path(entry["path"])
            if entry["path"] in seen:
                fail(502, "REPOSITORY_PROTOCOL", "仓库服务返回重复的文件路径")
            seen.add(entry["path"])
            if entry["type"] == "tree":
                continue
            if entry["type"] != "blob" or entry["mode"] not in {"100644", "100755"}:
                fail(422, "REPOSITORY_ENTRY", "暂不自动检出符号链接或子模块，请选择其他代码子目录")
            size = entry.get("size", -1)
            if not isinstance(size, int) or not 0 <= size <= 1024 * 1024:
                fail(422, "REPOSITORY_LIMIT", "源码单文件上限1MB")
            total += size
            entries.append(entry)
            if len(entries) > MAX_FILES or total > MAX_SOURCE:
                fail(422, "REPOSITORY_LIMIT", "源码上限180文件/20MB，请选择更小的代码子目录")
        files = []
        for entry in entries:
            blob = await self.request("GET", "/git/blobs/" + sha(entry["sha"]))
            try:
                if blob.get("encoding") != "base64":
                    raise ValueError
                content = base64.b64decode("".join(blob["content"].split()), validate=True)
            except (ValueError, KeyError):
                fail(502, "REPOSITORY_PROTOCOL", "仓库文件编码无效")
            if len(content) != entry["size"] or object_sha(content) != entry["sha"]:
                fail(502, "REPOSITORY_INTEGRITY", "源码文件大小或Git对象摘要不一致")
            files.append({"path": entry["path"], "mode": entry["mode"], "sha": entry["sha"],
                          "data": base64.b64encode(content).decode()})
        return {**commit, "slug": self.row["slug"], "ref": self.row["ref"],
                "subdirectory": self.row["subdirectory"], "files": files}


def provider(request, row: dict) -> GitHub:
    token = request.app.state.models.vault.decrypt(row["token_ciphertext"]).get_secret_value()
    return getattr(request.app.state, "repository_factory", GitHub)(row, token)
