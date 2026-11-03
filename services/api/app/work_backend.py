"""Deep Agents backend whose files and commands stay inside a task sandbox."""

import asyncio
import base64
import fnmatch
import json
import shlex
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, slice_read_response
from opensandbox.models.execd import RunCommandOpts
from opensandbox.sandbox import Sandbox

from app.workspace_rpc import parts

DROP_PRIVILEGES = """
import os
if os.getuid() == 0:
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
if os.getuid() != 1000 or os.getgid() != 1000:
    raise RuntimeError('Unprivileged execution required')
os.environ.clear()
os.environ.update({'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/workspace','LANG':'C.UTF-8'})
"""

RPC = Path(__file__).with_name("workspace_rpc.py").read_text(encoding="utf-8-sig")
COMMAND = """
import base64,json,os,signal,subprocess,sys,tempfile,resource
body=json.loads(base64.b64decode(sys.argv[1]))
resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024*1024,8*1024*1024))
with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
    p=subprocess.Popen(['/bin/sh','-c',body['command']],cwd='/workspace',stdout=out,stderr=err,start_new_session=True)
    timed_out=False
    try:
        p.wait(timeout=body['seconds'])
    except subprocess.TimeoutExpired:
        timed_out=True
    finally:
        try: os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        p.wait()
    out.seek(0); err.seek(0)
    a,b=out.read(32769),err.read(32769)
    print(json.dumps({'output':a[:32768].decode('utf-8','replace')+b[:32768].decode('utf-8','replace'),
        'exit_code':124 if timed_out else p.returncode,'truncated':len(a)>32768 or len(b)>32768,'timed_out':timed_out}))
"""


class WorkBackend(SandboxBackendProtocol):
    """Expose virtual absolute paths under /workspace and bounded sandbox execution."""

    def __init__(self, sandbox: Sandbox, guard: Callable[[], Awaitable[None]], *, journal=None,
                 repository_paths: list[str] | None = None) -> None:
        self.sandbox, self.guard = sandbox, guard
        self.journal = journal
        self.lock = asyncio.Lock()
        self.operations = 0
        self.repository_paths = repository_paths

    @property
    def id(self) -> str:
        return self.sandbox.id

    async def initialize(self) -> None:
        """Provision an unprivileged workspace without mounting host directories."""
        await self.guard()
        result = await self.sandbox.commands.run(
            "mkdir -p /workspace && chown 1000:1000 /workspace && chmod 700 /workspace",
            opts=RunCommandOpts(timeout=timedelta(seconds=10)),
        )
        if result.exit_code != 0 or result.error:
            raise RuntimeError("Workspace initialization failed")

    async def _call(self, script: str, body: dict, seconds: int = 15) -> dict:
        async with self.lock:
            self.operations += 1
            if self.operations > 500:
                raise ValueError("Work operation budget exceeded")
            await self.guard()
            payload = base64.b64encode(json.dumps(body).encode()).decode()
            result = await self.sandbox.commands.run(
                shlex.join(["python3", "-c", DROP_PRIVILEGES + script, payload]),
                opts=RunCommandOpts(uid=1000, gid=1000, timeout=timedelta(seconds=seconds + 5)),
            )
            if result.error or result.exit_code != 0 or len(result.text) > 2 * 1024 * 1024:
                raise RuntimeError("Sandbox operation failed or exceeded limits")
            return json.loads(result.text)

    async def _rpc(self, operation: str, **values) -> dict:
        if "path" in values:
            parts(values["path"])
        if operation == "scan" and self.repository_paths is not None and "exclude" not in values:
            from app.repository_workspace import rpc

            files = await rpc(self, "inventory", baseline=self.repository_paths)
            outside = await self._call(RPC, {"operation": "scan", "exclude": ["/repo"]})
            return {"entries": files["entries"] + outside.get("entries", []), "error": outside.get("error")}
        return await self._call(RPC, {"operation": operation, **values})

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109 - backend protocol uses this parameter for remote commands
    ) -> ExecuteResponse:
        return await self._execute(command, timeout, "execute", {"command": command, "timeout": timeout or 60})

    async def aexecute_workflow(self, command: str, *, seconds: int, arguments: dict) -> ExecuteResponse:
        """Execute only against the separately approved immutable workflow-code receipt."""
        if self.journal is None:
            raise PermissionError("Workflow code requires its single-use approval")
        return await self._execute(command, seconds, "workflow_code", arguments)

    async def _execute(self, command: str, seconds: int | None, name: str, arguments: dict) -> ExecuteResponse:
        if len(command) > 16000:
            return ExecuteResponse(output="命令超过长度限制", exit_code=1, truncated=False)
        seconds = min(max(seconds or 60, 1), 60)
        operation = (
            await self.journal.start(name, arguments) if self.journal else None
        )
        try:
            response = await self._call(COMMAND, {"command": command, "seconds": seconds}, seconds)
            if operation:
                await self.journal.finish(operation, response["exit_code"], response["output"])
        except BaseException:
            if operation:
                await self.journal.unknown(operation)
            raise
        return ExecuteResponse(
            output=response["output"],
            exit_code=response["exit_code"],
            truncated=response["truncated"],
        )

    async def als(self, path: str) -> LsResult:
        parts(path)
        response = await self._rpc("scan")
        prefix = path.rstrip("/") + "/"
        entries = [
            item
            for item in response.get("entries", [])
            if item["path"].startswith(prefix) and "/" not in item["path"][len(prefix) :].rstrip("/")
        ]
        return LsResult(error=response.get("error"), entries=entries)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        response = await self._rpc("read", path=file_path)
        if response.get("error"):
            return ReadResult(error=response["error"])
        data = base64.b64decode(response["data"])
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ReadResult(file_data=create_file_data(response["data"], encoding="base64"))
        return slice_read_response(create_file_data(text), offset, limit)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        response = await self._rpc("write", path=file_path, data=base64.b64encode(content.encode()).decode())
        return WriteResult(error=response.get("error"), path=None if response.get("error") else file_path)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        response = await self._rpc("edit", path=file_path, old=old_string, new=new_string, replace_all=replace_all)
        return EditResult(error=response.get("error"), path=file_path, occurrences=response.get("occurrences"))

    def delete(self, file_path: str) -> DeleteResult:
        raise RuntimeError("Work 文件操作仅支持异步接口")

    async def adelete(self, file_path: str) -> DeleteResult:
        operation = await self.journal.start("delete", {"file_path": file_path}) if self.journal else None
        try:
            response = await self._rpc("delete", path=file_path)
            if operation:
                await self.journal.finish(operation, 1 if response.get("error") else 0, response.get("error", "已删除"))
        except BaseException:
            if operation:
                await self.journal.unknown(operation)
            raise
        return DeleteResult(error=response.get("error"), path=None if response.get("error") else file_path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        root = path or "/"
        parts(root)
        response = await self._rpc("scan")
        prefix = root.rstrip("/") + "/"
        matches = [
            item
            for item in response.get("entries", [])
            if item["path"].startswith(prefix) and fnmatch.fnmatchcase(item["path"][len(prefix) :], pattern.lstrip("/"))
        ]
        return GlobResult(error=response.get("error"), matches=matches)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        listing = await self.aglob(glob or "*", path)
        if listing.error:
            return GrepResult(error=listing.error)
        matches = []
        cap = min(max_count or 100, 100)
        for item in listing.matches or []:
            if item["is_dir"]:
                continue
            response = await self._rpc("read", path=item["path"])
            if response.get("error"):
                continue
            text = base64.b64decode(response["data"]).decode("utf-8", "replace")
            for number, line in enumerate(text.splitlines(), 1):
                if pattern in line:
                    matches.append({"path": item["path"], "line": number, "text": line[:2000]})
                    if len(matches) >= cap:
                        return GrepResult(matches=matches, truncated=True)
        return GrepResult(matches=matches)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses = []
        for path, content in files:
            result = await self._rpc("write", path=path, data=base64.b64encode(content).decode())
            responses.append(FileUploadResponse(path=path, error=result.get("error")))
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path in paths:
            result = await self._rpc("read", path=path)
            content = None if result.get("error") else base64.b64decode(result["data"])
            responses.append(FileDownloadResponse(path=path, content=content, error=result.get("error")))
        return responses
