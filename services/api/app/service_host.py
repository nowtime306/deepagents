"""Local fixed-module supervisor with a kernel file lock and child heartbeat fencing."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from app.config import Settings

MODULES = {"api": "app.server", "ask": "app.ask_server", "work": "app.work_server", "ops": "app.operations_worker"}


def acquire(path: Path) -> int:
    """Kernel locks release on crashes; stale PID text never authorizes killing a process."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class ServiceHost:
    """Start only the requested platform module; never replay an individual task."""

    def __init__(self, kind: str, slot: int, port: int, directory: Path) -> None:
        self.kind, self.slot, self.port = kind, slot, port
        self.directory = directory
        self.token = uuid4().hex
        self.child = None
        self.child_exit = None
        self.state, self.restarts = "starting", 0
        self.publish_lock = threading.Lock()
        directory.mkdir(parents=True, exist_ok=True)
        self.manifest = directory / f"{kind}-{slot}.json"
        self.stop_file = directory / f"{kind}.stop"

    def publish(self) -> None:
        """An atomic nonsecret heartbeat lets the child detect a dead or replaced supervisor."""
        with self.publish_lock:
            self._publish()

    def _publish(self) -> None:
        body = {"kind": self.kind, "slot": self.slot, "token": self.token, "host_pid": os.getpid(),
                "child_pid": self.child.pid if self.child else None, "state": self.state,
                "heartbeat": time.monotonic(), "restarts": self.restarts}
        temporary = self.manifest.with_suffix("." + self.token + ".tmp")
        try:
            temporary.write_text(json.dumps(body), encoding="utf-8")
            os.replace(temporary, self.manifest)
        except OSError as error:
            print("服务心跳文件暂未更新：" + type(error).__name__, file=sys.stderr, flush=True)

    async def pulse(self) -> None:
        while True:
            await asyncio.to_thread(self.publish)
            await asyncio.sleep(0.5)

    async def launch(self) -> None:
        """Child environments contain only the existing local configuration and a new instance token."""
        environment = {**os.environ, "OWNER_AGENT_SERVICE_TOKEN": self.token,
                       "OWNER_AGENT_SERVICE_SLOT": str(self.slot), "PYTHONUNBUFFERED": "1"}
        arguments = [sys.executable, "-m", MODULES[self.kind]]
        if self.kind == "api":
            arguments += ["--port", str(self.port)]
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        with (self.directory / f"{self.kind}-{self.slot}.log").open("ab", buffering=0) as output:
            self.child = await asyncio.create_subprocess_exec(*arguments, cwd=Path(__file__).parents[1],
                env=environment, stdout=output, stderr=output, **flags)
        self.child_exit = asyncio.create_task(self.child.wait())
        self.state = "running"

    async def stop_child(self) -> None:
        """Request self-fencing, then bound shutdown of only the child process tree we created."""
        self.state = "stopping"
        await asyncio.to_thread(self.publish)
        if self.child is None or self.child.returncode is not None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.child_exit), timeout=25)
        except TimeoutError:
            if sys.platform == "win32":
                await asyncio.to_thread(subprocess.run, ["taskkill", "/PID", str(self.child.pid), "/T", "/F"],
                                        capture_output=True, timeout=10, check=False)
            else:
                self.child.kill()
            await self.child_exit

    async def serve(self) -> None:
        """Back off failed whole services; the durable queues decide which tasks remain eligible."""
        pulse = asyncio.create_task(self.pulse())
        failures = 0
        try:
            while not await asyncio.to_thread(self.stop_file.exists):
                began = time.monotonic()
                await self.launch()
                while self.child.returncode is None and not await asyncio.to_thread(self.stop_file.exists):
                    await asyncio.wait({self.child_exit}, timeout=0.5)
                if await asyncio.to_thread(self.stop_file.exists):
                    break
                self.restarts += 1
                failures = 0 if time.monotonic() - began > 30 else min(failures + 1, 5)
                self.state = "backoff"
                for _ in range(2 * min(20, 2**failures)):
                    if await asyncio.to_thread(self.stop_file.exists):
                        break
                    await asyncio.sleep(0.5)
        finally:
            await self.stop_child()
            self.state = "stopped"
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            await asyncio.to_thread(self.publish)


def main() -> None:
    parser = argparse.ArgumentParser(description="本机平台服务守护；停止时写入对应的services/*.stop文件")
    parser.add_argument("--service", choices=tuple(MODULES), required=True)
    parser.add_argument("--slot", type=int, choices=range(4), default=0)
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--start", action="store_true", help="本次明确启动，清除该类服务的停止标记")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or (args.service in {"api", "ops"} and args.slot != 0):
        parser.error("API只支持一个实例，端口应为1024–65535")
    host = ServiceHost(args.service, args.slot, args.port, Settings.from_env().data_dir / "services")
    try:
        lock = acquire(host.manifest.with_suffix(".lock"))
    except OSError:
        print("该服务守护实例已运行", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        if args.start:
            host.stop_file.unlink(missing_ok=True)
        factory = asyncio.ProactorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(host.serve())
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
