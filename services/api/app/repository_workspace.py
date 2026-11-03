"""Git bundles and worktree deltas share the existing durable artifact transaction."""

import base64
import hashlib
from pathlib import Path
from uuid import uuid4

from opensandbox.models.execd import RunCommandOpts

from app.repository_runs import binding

RPC = Path(__file__).with_name("repository_rpc.py").read_text(encoding="utf-8-sig")
QUIESCE = """import os,signal
if os.getuid()!=0:raise RuntimeError('Container root required')
pid=os.fork()
if pid==0:
 os.setgroups([]);os.setgid(1000);os.setuid(1000)
 try:os.kill(-1,signal.SIGKILL)
 except ProcessLookupError:pass
 os._exit(0)
_,status=os.waitpid(pid,0)
if status!=0:raise RuntimeError('Task process stop failed')
print('task-processes-stopped')
"""


async def rpc(backend, operation: str, **body) -> dict:
    result = await backend._call(RPC, {"operation": operation, **body}, seconds=45)
    if result.get("error"):
        raise ValueError("Repository workspace rejected: " + result["error"])
    return result


async def stop(backend) -> None:
    """Stop UID1000 only inside this task's verified container; root parent keeps the RPC alive."""
    import shlex
    from datetime import timedelta

    await backend.guard()
    result = await backend.sandbox.commands.run(shlex.join(["python3", "-c", QUIESCE]),
        opts=RunCommandOpts(timeout=timedelta(seconds=10)))
    if result.error or result.exit_code != 0 or "task-processes-stopped" not in result.text:
        raise RuntimeError("Repository processes could not be stopped")


async def capture(database, run: dict, backend) -> dict | None:
    row = await binding(database, run, authorize=False)
    if not row:
        return None
    await stop(backend)
    token = uuid4().hex
    result = await rpc(backend, "capture", token=token, baseline=[f["path"] for f in row["source"]["files"]])
    size = result["size"]
    if not isinstance(size, int) or not 0 < size <= 20 * 1024 * 1024:
        raise ValueError("Repository bundle exceeds limits")
    content = bytearray()
    while len(content) < size:
        part = await rpc(backend, "read_chunk", token=token, offset=len(content))
        data = base64.b64decode(part["data"], validate=True)
        if not data or len(data) > 512 * 1024 or len(content) + len(data) > size:
            raise ValueError("Repository bundle transfer failed")
        content.extend(data)
    if hashlib.sha256(content).hexdigest() != result["digest"]:
        raise ValueError("Repository bundle integrity failed")
    return {**result, "content": bytes(content)}


async def restore(database, run: dict, backend, snapshot) -> dict | None:
    row = await binding(database, run)
    if not row:
        return None
    if not snapshot:
        for file in row["source"]["files"]:
            result = (await backend.aupload_files([("/repo/" + file["path"], base64.b64decode(file["data"]))]))[0]
            if result.error:
                raise RuntimeError("Repository source import failed")
        await rpc(backend, "init", branch=row["branch"], sha=row["source"]["sha"],
                  modes={f["path"]: f["mode"] for f in row["source"]["files"]})
        return None
    content, metadata = snapshot
    token = uuid4().hex
    for offset in range(0, len(content), 512 * 1024):
        await rpc(backend, "write_chunk", token=token, offset=offset,
                  data=base64.b64encode(content[offset:offset + 512 * 1024]).decode())
    await rpc(backend, "restore", token=token, digest=hashlib.sha256(content).hexdigest(), metadata=metadata)
    return metadata
