"""Private browser transport; attest the server before sending any credentials or state."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import shlex
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from opensandbox.models.execd import RunCommandOpts

from app.errors import fail

PORT = 8917
MAX_RESPONSE = 3 * 1024 * 1024
DAEMON = Path(__file__).with_name("browser_daemon.py").read_text(encoding="utf-8")
BOOTSTRAP = """
import base64,json,os,pathlib,stat,subprocess,sys,time
root=pathlib.Path('/var/lib/owner-agent-browser')
if not root.exists():
    root.mkdir(mode=0o700)
    os.chown(root,1001,1001)
info=root.lstat()
assert stat.S_ISDIR(info.st_mode) and info.st_uid==1001 and stat.S_IMODE(info.st_mode)==0o700
domains=json.loads(base64.b64decode(sys.argv[2]))
config=root/'domains.json'
if config.exists():
    assert json.loads(config.read_text())==domains
else:
    config.write_text(json.dumps(domains))
    os.chown(config,1001,1001)
pid=root/'pid'
running=False
if pid.exists():
    try:
        os.kill(int(pid.read_text()),0)
        process=pathlib.Path('/proc')/pid.read_text()
        running=process.stat().st_uid==1001 and str(root/'daemon.py').encode() in (process/'cmdline').read_bytes()
    except ProcessLookupError:
        pass
if not running:
    (root/'token').unlink(missing_ok=True)
    script=root/'daemon.py'
    script.write_bytes(base64.b64decode(sys.argv[1],validate=True))
    os.chown(script,1001,1001)
    with (root/'service.log').open('ab') as output:
        subprocess.Popen(['python3',str(script)],cwd=root,stdout=output,stderr=output,
            start_new_session=True,user=1001,group=1001,extra_groups=[],
            env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(root),'TMPDIR':str(root),'LANG':'C.UTF-8'})
for _ in range(100):
    if (root/'token').exists():
        print(json.dumps({'key':(root/'token').read_text()}))
        break
    time.sleep(.1)
else:
    raise RuntimeError('Browser did not start')
"""


class BrowserTransport:
    def __init__(self, sandbox, guard, key: str = "") -> None:
        self.sandbox, self.guard, self.key = sandbox, guard, key

    async def start(self, domains: list[str]) -> str:
        await self.guard()
        code = base64.b64encode(DAEMON.encode()).decode()
        config = base64.b64encode(json.dumps(domains).encode()).decode()
        result = await self.sandbox.commands.run(
            shlex.join(["python3", "-c", BOOTSTRAP, code, config]),
            opts=RunCommandOpts(uid=0, gid=0, timeout=timedelta(seconds=15)),
        )
        if result.error or result.exit_code != 0:
            fail(503, "BROWSER_START_FAILED", "浏览器进程未能启动，请检查任务运行环境")
        try:
            key = json.loads(result.text)["key"]
            if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
                raise ValueError("Invalid token")
            self.key = key
        except (ValueError, KeyError, TypeError):
            fail(503, "BROWSER_START_FAILED", "浏览器未返回有效的私有通信状态")
        for _ in range(30):
            try:
                async with httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False) as client:
                    await self.attest(client)
                return self.key
            except (httpx.TransportError, ConnectionError):
                await asyncio.sleep(0.1)
        fail(503, "BROWSER_START_FAILED", "浏览器连接尚未就绪，未发送登录状态")

    async def endpoint(self):
        endpoint = await self.sandbox.get_endpoint(PORT)
        config = self.sandbox.connection_config
        url = endpoint.endpoint
        if not url.startswith(("http://", "https://")):
            url = config.protocol + "://" + url
        target, manager = urlsplit(url), urlsplit(config.get_base_url())
        if (
            not config.use_server_proxy
            or target.scheme != manager.scheme
            or target.netloc != manager.netloc
            or target.query
            or target.fragment
            or target.username
            or not target.path.endswith(f"/sandboxes/{self.sandbox.id}/proxy/{PORT}")
        ):
            fail(503, "BROWSER_ENDPOINT_INVALID", "浏览器访问地址不是当前管理服务的私有代理")
        return url.rstrip("/"), endpoint.build_request_headers(config)

    async def attest(self, client):
        await self.guard()
        url, headers = await self.endpoint()
        nonce = secrets.token_hex(32)
        response = await client.get(url + "/proof", params={"nonce": nonce}, headers=headers)
        if response.status_code != 200:
            raise ConnectionError("Browser is not ready")
        expected = hmac.new(self.key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        try:
            proof = response.json().get("proof", "")
        except ValueError:
            proof = ""
        if not isinstance(proof, str) or not hmac.compare_digest(proof, expected):
            fail(503, "BROWSER_IDENTITY_FAILED", "浏览器服务身份核验失败，未发送私有信息")
        return url, {**headers, "X-Owner-Browser-Key": self.key}

    async def request(self, body: dict | None = None, *, frame: bool = False):
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            url, headers = await self.attest(client)
            await self.guard()
            async with client.stream(
                "GET" if frame else "POST",
                url + ("/frame" if frame else "/rpc"),
                headers=headers,
                **({} if frame else {"json": body}),
            ) as response:
                if response.status_code != 200:
                    fail(503, "BROWSER_REQUEST_FAILED", "浏览器请求失败，操作结果可能未知；未自动重试")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > (1024 * 1024 if frame else MAX_RESPONSE):
                        fail(413, "BROWSER_RESPONSE_LIMIT", "浏览器响应超过大小上限")
            await self.guard()
            if frame:
                if not data.startswith(b"\xff\xd8\xff"):
                    fail(503, "BROWSER_FRAME_INVALID", "浏览器没有返回有效截图")
                return bytes(data)
            try:
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError("Invalid response")
                return result
            except ValueError:
                fail(503, "BROWSER_RESPONSE_INVALID", "浏览器响应格式无效，未重复操作")
