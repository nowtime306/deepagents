"""Operate only fixed project service scripts and preserve the previous slot inventory."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict

from app.config import DEFAULT_INTERNAL_API_URL, Settings, validate_internal_api_url


class ServiceControlProfileError(RuntimeError):
    """Fixed deployment scripts cannot control an independent API profile."""


def _profile(database, directory, endpoint):
    """Use explicit database identity and absolute data paths for fixed deployment scripts."""
    directory = Path(directory)
    if not directory.is_absolute():
        raise ValueError("Absolute deployment directory required")
    identity = conninfo_to_dict(database)
    if not all(identity.get(key) for key in ("host", "port", "dbname", "user")):
        raise ValueError("Explicit database identity required")
    return identity, directory.resolve(), validate_internal_api_url(endpoint)


class ManagedServices:
    def __init__(self, settings, database):
        self.settings, self.db = settings, database
        self.scripts = Path(__file__).parents[3] / "scripts"

    def require_profile(self):
        """Compare persisted and inherited identities before any service-control side effect."""
        try:
            environment = self.scripts.parent / "services/api/.env"
            if not environment.is_file():
                raise ValueError("Deployment environment unavailable")
            values = dotenv_values(environment, interpolate=False)
            persisted = _profile(values["DATABASE_URL"], values.get("PLATFORM_DATA_DIR") or Settings.data_dir,
                                values.get("PLATFORM_INTERNAL_API_URL") or DEFAULT_INTERNAL_API_URL)
            inherited = _profile(os.getenv("DATABASE_URL", values["DATABASE_URL"]),
                                os.getenv("PLATFORM_DATA_DIR", str(persisted[1])),
                                os.getenv("PLATFORM_INTERNAL_API_URL", persisted[2]))
            actual = _profile(self.settings.database_url, self.settings.data_dir, self.settings.internal_api_url)
            if persisted[2] != DEFAULT_INTERNAL_API_URL or actual != persisted or inherited != persisted:
                raise ValueError("Service deployment profile mismatch")
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ProgrammingError):
            raise ServiceControlProfileError("SERVICE_CONTROL_PROFILE_MISMATCH") from None

    async def snapshot(self):
        self.require_profile()
        result = []
        for kind in ("api", "ask", "work"):
            for slot in range(4):
                file = self.settings.data_dir / f"services/{kind}-{slot}.json"
                if not file.is_file():
                    continue
                row = json.loads(file.read_text(encoding="utf-8"))
                if row["state"] in {"starting", "running", "backoff"} and 0 <= time.monotonic() - row["heartbeat"] < 10:
                    result.append({"kind": kind, "slot": slot})
        return result

    async def command(self, script, arguments):
        self.require_profile()
        if sys.platform != "win32":
            raise RuntimeError("SCHEDULED_BACKUP_WINDOWS_REQUIRED")
        directory = self.settings.data_dir / "services"
        directory.mkdir(parents=True, exist_ok=True)
        # Descendant services can inherit Windows pipe handles after PowerShell exits.
        # Wait for the controller process, never for EOF from its long-lived descendants.
        with (directory / "control.log").open("ab", buffering=0) as output:
            await self.control(script, arguments, output)

    async def control(self, script, arguments, output):
        self.require_profile()
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW}
        launch = asyncio.create_task(
            asyncio.to_thread(
                subprocess.Popen,
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.scripts / script),
                    *arguments,
                ],
                cwd=self.scripts.parent,
                stdout=output,
                stderr=output,
                **flags,
            )
        )
        try:
            process = await asyncio.shield(launch)
            await asyncio.to_thread(process.wait, timeout=60)
        except (asyncio.CancelledError, subprocess.TimeoutExpired):
            process = await launch
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            await asyncio.to_thread(process.wait, timeout=10)
            raise
        if process.returncode:
            raise RuntimeError("SERVICE_CONTROL_FAILED")

    async def stop(self, services):
        self.require_profile()
        for kind in dict.fromkeys(row["kind"] for row in services):
            row = next(item for item in services if item["kind"] == kind)
            await self.command(
                "stop-platform-services.ps1",
                ["-Service", kind, "-OwnerToken", row["stop_marker"].removeprefix("backup:")],
            )

    async def start(self, services):
        self.require_profile()
        ordered = sorted(services, key=lambda item: (("api", "ask", "work").index(item["kind"]), item["slot"]))
        skipped = set()
        for row in ordered:
            marker = self.settings.data_dir / f"services/{row['kind']}.stop"
            if row.get("stop_marker") and marker.is_file() and marker.read_text().strip() != row["stop_marker"]:
                skipped.add(row["kind"])
        if "api" in skipped:
            return False
        ordered = [row for row in ordered if row["kind"] not in skipped]
        for row in ordered:
            if row["kind"] != "api":
                await self.ready()
            await self.command(
                "start-managed-service.ps1",
                ["-Service", row["kind"], "-Slot", str(row["slot"]), "-ExpectedStopMarker", row["stop_marker"]],
            )
            if row["kind"] == "api":
                await self.ready()
        for _ in range(60):
            online = await self.db.fetch(
                "SELECT s.kind,count(*) AS n FROM service_instances s "
                "JOIN pg_stat_activity a ON a.pid=s.backend_id AND a.datname=current_database() "
                "WHERE heartbeat_at>now()-interval '15 seconds' GROUP BY s.kind"
            )
            counts = {row["kind"]: row["n"] for row in online}
            if all(
                counts.get(kind, 0) >= sum(s["kind"] == kind for s in ordered) for kind in {s["kind"] for s in ordered}
            ):
                return not skipped
            await asyncio.sleep(0.5)
        raise RuntimeError("SERVICES_RESTART_NOT_READY")

    async def ready(self):
        async with httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False) as client:
            for _ in range(60):
                try:
                    if (await client.get(self.settings.internal_api_url + "/health/ready")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        raise RuntimeError("API_RESTART_NOT_READY")
