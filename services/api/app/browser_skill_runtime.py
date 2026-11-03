"""Private supervised BrowserSkill recording processes, with no client-controlled paths."""

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from psycopg.types.json import Jsonb

from app.browser_skill_trace import import_bundle, safe_url
from app.errors import APIError, fail
from app.permissions import require_account


class BrowserSkillRuntime:
    """One administrator-provisioned local connection; never attach a daily profile."""

    def __init__(self, database, data_dir: Path) -> None:
        self.database = database
        self.root = data_dir.resolve() / "browser-skill"
        self.executable = os.getenv("BROWSER_SKILL_EXECUTABLE", "")
        self.instance = os.getenv("BROWSER_SKILL_INSTANCE", "")
        self.configuration_error = False
        self.from_file = False
        self.load_connection()
        self.tasks = {}
        self.processes = {}
        self.cancelled = set()
        self.stop_requests = {}

    def load_connection(self) -> None:
        """Read only the supervisor's fixed, nonsecret local connection manifest."""
        path = self.root / "connection.json"
        if not path.exists():
            if self.from_file:
                self.executable, self.instance = "", ""
            return
        self.from_file = True
        try:
            if path.is_symlink() or path.stat().st_size > 4096:
                raise ValueError("Invalid connection manifest")
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if (
                set(value)
                != {
                    "schema_version",
                    "executable",
                    "instance_id",
                    "cli_version",
                    "extension_version",
                    "protocol_version",
                }
                or value["schema_version"] != 1
                or value["cli_version"] != "0.4.1"
                or value["extension_version"] != "0.4.1"
                or value["protocol_version"] != "1.8"
                or not re.fullmatch(r"[0-9a-f]{8}", value["instance_id"])
                or not Path(value["executable"]).is_absolute()
                or Path(value["executable"]).name.lower() not in {"bsk.exe", "bsk"}
            ):
                raise ValueError("Invalid connection manifest")
            self.executable, self.instance = value["executable"], value["instance_id"]
            self.configuration_error = False
        except (ValueError, TypeError, KeyError, OSError):
            self.executable, self.instance, self.configuration_error = "", "", True

    def environment(self) -> dict:
        return {
            **os.environ,
            "BSK_HOME": str(self.root / "home"),
            "BSK_AUTO_START": "0",
            "BSK_AUTO_UPDATE": "off",
            "BSK_BROWSER_WAIT_MS": "0",
        }

    async def launch(self, args: list[str], *, capture: bool = False):
        """Use threads on Windows SelectorEventLoop; clean up a cancelled process launch."""
        pending = asyncio.create_task(
            asyncio.to_thread(
                subprocess.Popen,
                [self.executable, *args],
                env=self.environment(),
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        )
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            process = await pending
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait)
            raise

    async def command(self, *args: str, seconds: int = 15):
        process = await self.launch(list(args), capture=True)
        try:
            output, _ = await asyncio.to_thread(process.communicate, timeout=seconds)
        except BaseException:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait)
            raise
        if process.returncode or len(output) > 1024 * 1024:
            fail(503, "BROWSER_CONNECTION_OFFLINE", "专用浏览器连接离线或命令失败")
        return output.decode("utf-8")

    async def status(self) -> dict:
        self.load_connection()
        if self.configuration_error:
            return {
                "ready": False,
                "code": "BROWSER_CONFIG_INVALID",
                "message": "专用连接登记文件无效",
                "connections": [],
            }
        if (self.root / "recovery-required").exists():
            return {
                "ready": False,
                "code": "BROWSER_RECOVERY_REQUIRED",
                "message": "录制曾中断，请管理员确认专用会话已停止后恢复连接",
                "connections": [],
            }
        if not self.executable or not Path(self.executable).is_absolute() or not self.instance:
            return {
                "ready": False,
                "code": "BROWSER_NOT_CONFIGURED",
                "message": "尚未登记专用浏览器连接",
                "connections": [],
            }
        try:
            if (await self.command("--version")).strip() != "bsk 0.4.1":
                fail(503, "BROWSER_VERSION_MISMATCH", "CLI 版本须为 0.4.1")
            peers = json.loads(await self.command("browsers", "--json"))
            peer = next((p for p in peers if p["instance_id"] == self.instance), None)
            if not peer or peer.get("extension_version") != "0.4.1":
                fail(503, "BROWSER_EXTENSION_OFFLINE", "专用扩展未连接或版本不符")
            busy = bool(
                await self.database.fetch(
                    "SELECT 1 FROM browser_skill_recordings WHERE status IN ('starting','recording','stopping')"
                )
            )
            return {
                "ready": not busy,
                "code": "BROWSER_BUSY" if busy else "READY",
                "message": "连接正在使用" if busy else "专用浏览器已连接",
                "connections": [{"id": "local", "name": "专用浏览器", "version": "0.4.1", "busy": busy}],
            }
        except APIError as error:
            return {"ready": False, "code": error.code, "message": error.message, "connections": []}
        except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired):
            return {
                "ready": False,
                "code": "BROWSER_CONNECTION_OFFLINE",
                "message": "专用浏览器连接不可用",
                "connections": [],
            }

    async def recover(self) -> None:
        """Keep interrupted ownership busy until an operator confirms dedicated processes stopped."""
        rows = await self.database.fetch(
            "SELECT id FROM browser_skill_recordings WHERE status IN ('starting','recording','stopping')"
        )
        if rows:
            self.quarantine()
            await self.database.fetch(
                "UPDATE browser_skill_recordings SET status='stopping',error_code='BROWSER_INTERRUPTED',"
                "updated_at=now() "
                "WHERE status IN ('starting','recording','stopping') RETURNING id"
            )

    def quarantine(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "recovery-required").touch()

    def start(self, identifier: UUID, owner: UUID, url: str) -> None:
        self.stop_requests[identifier] = asyncio.Event()
        self.tasks[identifier] = asyncio.create_task(self.record(identifier, owner, safe_url(url)))

    def clean(self, identifier: UUID) -> None:
        directory = self.root / "recordings" / str(identifier)
        if directory.resolve().is_relative_to((self.root / "recordings").resolve()) and not directory.is_symlink():
            if directory.exists():
                shutil.rmtree(directory)
        else:
            raise OSError("Recording cleanup ownership cannot be confirmed")
        for filename in ("record-recovery.json", "record-recovery.tmp"):
            path = self.root / "home" / filename
            if path.resolve().is_relative_to(self.root.resolve()) and not path.is_symlink():
                path.unlink(missing_ok=True)
            else:
                raise OSError("Recovery cleanup ownership cannot be confirmed")

    async def shutdown_recording(self, identifier: UUID, directory: Path, *, attempted: bool = False) -> bool:
        """Confirm both CLI completion and release of its dedicated recording state."""
        process = self.processes.get(identifier)
        if process is None:
            return not attempted
        state = self.root / "home" / "record-session.json"
        try:
            if process.poll() is None or state.exists():
                await self.command("record", "stop", "--output", str(directory), "--json", seconds=30)
            await asyncio.to_thread(process.wait, timeout=5)
        except (APIError, OSError, TimeoutError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait)
                return False
        return process.poll() is not None and not state.exists()

    async def record(self, identifier: UUID, owner: UUID, url: str) -> None:
        directory = self.root / "recordings" / str(identifier)
        outcome, error_code, draft = "failed", "BROWSER_RECORD_FAILED", None
        attempted = False
        try:
            account = await require_account(self.database, owner, "skills.manage")
            if not account["is_admin"]:
                fail(403, "BROWSER_HOST_ADMIN_REQUIRED", "本机浏览器需要主机管理员权限")
            directory.mkdir(parents=True, exist_ok=False)
            attempted = True
            process = await self.launch(
                ["record", "start", "--browser", self.instance, "--url", url, "--output", str(directory), "--json"]
            )
            self.processes[identifier] = process
            while process.poll() is None and not self.stop_requests[identifier].is_set():
                account = await require_account(self.database, owner, "skills.manage")
                if not account["is_admin"]:
                    fail(403, "BROWSER_HOST_ADMIN_REQUIRED", "主机管理员权限已撤销")
                if (self.root / "home" / "record-session.json").is_file():
                    await self.database.fetch(
                        "UPDATE browser_skill_recordings SET status='recording',updated_at=now() "
                        "WHERE id=%s AND status='starting' RETURNING id",
                        (identifier,),
                    )
                try:
                    await asyncio.to_thread(process.wait, timeout=0.2)
                except subprocess.TimeoutExpired:
                    continue
            outcome = "review"
        except asyncio.CancelledError:
            self.cancelled.add(identifier)
        except (APIError, OSError, ValueError) as error:
            error_code = error.code if isinstance(error, APIError) else "BROWSER_RECORD_FAILED"
        finally:
            try:
                await self.database.fetch(
                    "UPDATE browser_skill_recordings SET status='stopping',updated_at=now() WHERE id=%s RETURNING id",
                    (identifier,),
                )
                if not await self.shutdown_recording(identifier, directory, attempted=attempted):
                    raise OSError("Recording session release was not confirmed")
                if identifier in self.cancelled:
                    outcome = "cancelled"
                elif outcome == "review":
                    try:
                        if self.processes[identifier].returncode:
                            fail(503, "BROWSER_RECORD_FAILED", "录制进程失败")
                        account = await require_account(self.database, owner, "skills.manage")
                        if not account["is_admin"]:
                            fail(403, "BROWSER_HOST_ADMIN_REQUIRED", "主机管理员权限已撤销")
                        draft = await asyncio.to_thread(import_bundle, directory)
                    except (APIError, OSError, ValueError) as error:
                        outcome = "failed"
                        error_code = error.code if isinstance(error, APIError) else "BROWSER_RECORD_FAILED"
                await asyncio.to_thread(self.clean, identifier)
                if identifier in self.cancelled:
                    outcome, draft = "cancelled", None
                await self.database.fetch(
                    "UPDATE browser_skill_recordings SET status=%s,error_code=%s,"
                    "draft=COALESCE(%s::jsonb,draft),revision=revision+1,updated_at=now() WHERE id=%s RETURNING id",
                    (
                        outcome,
                        error_code if outcome == "failed" else None,
                        Jsonb(draft) if draft is not None else None,
                        identifier,
                    ),
                )
            except (APIError, OSError, ValueError):
                self.quarantine()
                await self.database.fetch(
                    "UPDATE browser_skill_recordings SET status='stopping',error_code='BROWSER_RECOVERY_REQUIRED',"
                    "updated_at=now() WHERE id=%s RETURNING id",
                    (identifier,),
                )
            finally:
                self.processes.pop(identifier, None)
                self.tasks.pop(identifier, None)
                self.stop_requests.pop(identifier, None)
                self.cancelled.discard(identifier)

    async def stop(self, identifier: UUID, *, cancel: bool = False) -> None:
        """Signal the single cleanup owner; repeat requests never start another stop command."""
        task = self.tasks.get(identifier)
        if task is None:
            return
        if cancel:
            self.cancelled.add(identifier)
        self.stop_requests[identifier].set()
        await asyncio.wait_for(asyncio.shield(task), 45)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
