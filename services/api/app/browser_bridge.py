"""Connect an approved task operation to the private browser and encrypted state."""

import asyncio
import base64
from pathlib import PurePosixPath

from app.browser_state import load_state, public_result, save_state
from app.browser_transport import BrowserTransport
from app.errors import fail


class BrowserBridge:
    def __init__(self, database, vault, backend, run: dict, session: dict) -> None:
        self.database, self.vault, self.backend = database, vault, backend
        self.run, self.session = run, session
        self.transport = BrowserTransport(backend.sandbox, backend.guard)
        self.started = False
        self.revision = 0
        self.lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self.started:
            return
        await self.backend.guard()
        rows = await self.database.fetch(
            "SELECT * FROM work_browser_transports WHERE session_id=%s AND owner_id=%s",
            (self.session["id"], self.run["owner_id"]),
        )
        if rows and rows[0]["status"] in {"UNKNOWN", "CLOSED"}:
            fail(409, "BROWSER_RESULT_UNKNOWN", "浏览器上次结果未知或已关闭，未重放操作")
        if rows and rows[0]["status"] == "READY":
            self.transport.key = self.vault.decrypt(rows[0]["token_ciphertext"]).get_secret_value()
            self.revision, self.started = rows[0]["state_revision"], True
            return
        await self.database.execute(
            "INSERT INTO work_browser_transports(session_id,task_id,owner_id) VALUES(%s,%s,%s) "
            "ON CONFLICT(session_id) DO NOTHING",
            (self.session["id"], self.run["task_id"], self.run["owner_id"]),
        )
        state, revision = await load_state(
            self.database,
            self.vault,
            self.run["task_id"],
            self.run["owner_id"],
            self.run["network_domains"],
        )
        key = await self.transport.start(self.run["network_domains"])
        if state:
            await self.transport.request({"op": "restore", "state": state})
        await self.backend.guard()
        await self.database.execute(
            "UPDATE work_browser_transports SET token_ciphertext=%s,status='READY',state_revision=%s,updated_at=now() "
            "WHERE session_id=%s AND owner_id=%s AND status='INITIALIZING'",
            (self.vault.encrypt(key), revision, self.session["id"], self.run["owner_id"]),
        )
        self.revision, self.started = revision, True

    async def save(self) -> None:
        if not self.started:
            return
        state = await self.transport.request({"op": "export"})
        self.revision = await save_state(
            self.database,
            self.vault,
            self.run["task_id"],
            self.run["owner_id"],
            self.run["network_domains"],
            state,
            self.revision,
            self.backend.guard,
        )
        await self.database.execute(
            "UPDATE work_browser_transports SET state_revision=%s,updated_at=now() WHERE session_id=%s AND owner_id=%s",
            (self.revision, self.session["id"], self.run["owner_id"]),
        )

    async def review(self) -> None:
        await self.database.execute(
            "UPDATE work_browser_transports SET status='UNKNOWN',updated_at=now() "
            "WHERE session_id=%s AND status<>'CLOSED'",
            (self.session["id"],),
        )
        await self.database.execute(
            "UPDATE work_browser_states SET status='REVIEW',updated_at=now() WHERE task_id=%s AND owner_id=%s",
            (self.run["task_id"], self.run["owner_id"]),
        )

    async def closed(self) -> None:
        await self.database.execute(
            "UPDATE work_browser_transports t SET status='CLOSED',token_ciphertext='',updated_at=now() "
            "FROM work_sandbox_sessions s WHERE t.session_id=s.id AND t.session_id=%s AND s.state='RELEASED'",
            (self.session["id"],),
        )

    async def step(self, step: dict, operation, index: int) -> dict:
        await self.initialize()
        op, parameters = step["op"], dict(step["parameters"])
        path = parameters.pop("path", None)
        if op == "upload":
            source = (await self.backend.adownload_files([path]))[0]
            if source.error or source.content is None:
                fail(422, "BROWSER_UPLOAD_UNAVAILABLE", "上传文件不存在或不符合工作区路径限制")
            parameters.update(name=PurePosixPath(path).name, data=base64.b64encode(source.content).decode())
        receipt = await self.transport.request(
            {"op": op, "parameters": parameters, "request_id": f"{operation.hex}-{index:x}"}
        )
        await self.save()
        if not receipt.get("ok"):
            fail(409, "BROWSER_RESULT_UNKNOWN", "浏览器操作未完整返回，结果可能未知；已停止，不会重放")
        result = receipt["result"]
        if op in {"screenshot", "download"}:
            data = base64.b64decode(result["data"], validate=True)
            if len(data) > 1024 * 1024:
                fail(413, "BROWSER_FILE_LIMIT", "浏览器文件超过单文件 1 MB 限制")
            saved = (await self.backend.aupload_files([(path, data)]))[0]
            if saved.error:
                fail(422, "BROWSER_FILE_UNAVAILABLE", "目标路径不安全、已存在或无法保存浏览器文件")
        return {"ok": True, **public_result(result), **({"path": path} if path else {})}
