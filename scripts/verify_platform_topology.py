"""Exercise two isolated API supervisors; never target production processes or data."""

import argparse
import asyncio
import concurrent.futures
import ctypes
import json
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from contextlib import closing
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "services/api"


def available_port() -> int:
    """Reserve a candidate loopback port; startup still verifies the selected listener."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def ready(port: int) -> bool:
    """Read health only; this probe cannot invoke a provider or create a task."""
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health/ready", timeout=1, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


class ProcessMemory:
    """Read memory counters through a stable handle to this probe's recorded API child."""

    def __init__(self, identifier: int) -> None:
        self.handle = None
        if sys.platform == "win32":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x0400 | 0x0010, False, identifier)

    def read(self) -> dict | None:
        """Unavailable counters stay unknown; no process memory contents are read."""
        if not self.handle:
            return None

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in
                ("peak", "current", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "page", "peak_page")]

        values = Counters()
        values.cb = ctypes.sizeof(values)
        query = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not query(self.handle, ctypes.byref(values), values.cb):
            return None
        return {"working_set_bytes": values.current, "lifetime_peak_working_set_bytes": values.peak}

    def close(self) -> None:
        """Close only this read/query handle, never terminate the process."""
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def capacity(port: int, concurrency: int, child_pid: int) -> dict:
    """Measure five bounded seconds of health traffic, not model or task throughput."""
    observations, samples, allocated = [], [], 0
    mutex = threading.Lock()
    with (
        closing(ProcessMemory(child_pid)) as memory,
        httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False,
                     limits=httpx.Limits(max_connections=concurrency)) as client,
    ):
        def workload() -> list[tuple[float, int | None]]:
            nonlocal allocated
            batch = []
            while time.perf_counter() < deadline:
                with mutex:
                    if allocated >= 2000:
                        break
                    allocated += 1
                started = time.perf_counter()
                try:
                    status = client.get("/health/ready").status_code
                except httpx.HTTPError:
                    status = None
                batch.append((time.perf_counter() - started, status))
            return batch

        began = time.perf_counter()
        deadline = began + 5
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(workload) for _ in range(concurrency)]
            while True:
                observation = memory.read()
                if observation:
                    observations.append(observation)
                if all(future.done() for future in futures):
                    break
                time.sleep(.1)
            for future in futures:
                samples.extend(future.result())
        elapsed = time.perf_counter() - began
    delays = sorted(item[0] * 1000 for item in samples)
    successes = sum(item[1] == 200 for item in samples)
    rejected = sum(item[1] == 429 for item in samples)
    process_memory = {
        "available": bool(observations), "samples": len(observations),
        "method": "Windows_GetProcessMemoryInfo_query_handle" if sys.platform == "win32" else "unavailable",
        "max_observed_working_set_bytes": max((row["working_set_bytes"] for row in observations), default=None),
        "lifetime_peak_working_set_bytes": max(
            (row["lifetime_peak_working_set_bytes"] for row in observations), default=None),
    }
    return {"workload": "isolated_health_only", "concurrency": concurrency, "requests": len(samples),
            "successes": successes, "rejected_429": rejected, "errors": len(samples) - successes - rejected,
            "business_queue": "not_applicable_health_endpoint", "target_duration_seconds": 5,
            "max_requests": 2000, "request_timeout_seconds": 2, "process_memory": process_memory,
            "duration_seconds": round(elapsed, 3), "request_limit_reached": allocated == 2000,
            "requests_per_second": round(len(samples) / elapsed, 2),
            "successful_requests_per_second": round(successes / elapsed, 2),
            "p50_ms": round(statistics.median(delays), 2) if delays else None,
            "p95_ms": round(delays[int(len(delays) * .95)], 2) if delays else None}


class Supervisor:
    """Only the Popen instance created by this probe may be stopped."""

    def __init__(self, directory: Path, url: str, shared: Path, slot: int) -> None:
        self.directory, self.shared, self.slot, self.port = directory, shared, slot, available_port()
        directory.mkdir()
        environment = {**os.environ, "DATABASE_URL": url, "PLATFORM_DATA_DIR": str(shared),
                       "WORK_ENABLED": "false", "ASK_WORKER_ENABLED": "true",
                       "AUTOMATION_ENABLED": "false", "EVALUATION_ENABLED": "false"}
        for key in ("OWNER_AGENT_SERVICE_TOKEN", "OWNER_AGENT_SERVICE_SLOT"):
            environment.pop(key, None)
        self.log = (directory / "supervisor.log").open("wb")
        self.process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()),
                                         "--probe-host", str(slot), "--port", str(self.port)], cwd=API, env=environment,
                                        stdout=self.log, stderr=self.log,
                                        start_new_session=sys.platform != "win32",
                                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    def stop(self, *, crash: bool = False) -> None:
        """A deliberate crash is bounded to this recorded supervisor tree."""
        if self.process.poll() is None:
            if crash:
                self.kill_tree()
            else:
                (self.shared / "services/api.stop").write_text("isolated probe stop", encoding="utf-8")
            try:
                self.process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                self.kill_tree()
                self.process.wait(timeout=15)
        self.log.close()

    def api_child(self) -> int:
        """Verify only this child ancestry, allowing Windows venv launcher intermediaries."""
        record = json.loads((self.shared / f"services/api-{self.slot}.json").read_text(encoding="utf-8"))
        assert record["kind"] == "api" and self.process.poll() is None
        identifiers = re.findall(r"Started server process \[(\d+)\]",
                                 (self.shared / f"services/api-{self.slot}.log").read_text(errors="replace"))
        assert identifiers, "Recorded API server has not published its process ID"
        identifier = int(identifiers[-1])
        if sys.platform == "win32":
            command = (
                f"$cursor={identifier};$owner={self.process.pid};"
                "for($depth=0;$depth -lt 6;$depth++){if($cursor -eq $owner){exit 0};"
                '$row=Get-CimInstance Win32_Process -Filter "ProcessId=$cursor";'
                "if(!$row){exit 1};$cursor=$row.ParentProcessId};exit 1"
            )
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                           capture_output=True, timeout=15, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            assert record["host_pid"] == self.process.pid
        return identifier

    def kill_tree(self) -> None:
        """Only stop this still-running Popen tree; no process discovery or name matching."""
        if self.process.poll() is not None:
            return
        if sys.platform == "win32":
            result = subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                                    capture_output=True, timeout=15, check=False)
            if result.returncode and self.process.poll() is None:
                raise RuntimeError("Recorded supervisor tree could not be stopped")
        else:
            os.killpg(self.process.pid, signal.SIGKILL)


def wait_for(connection, predicate, report: dict, timeout: int = 90) -> None:
    """Observe the authoritative DB lock while waiting for every transition."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = connection.execute("SELECT count(DISTINCT pid) FROM pg_locks WHERE locktype='advisory' "
            "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
            "AND classid=0 AND objid=710014 AND objsubid=1 AND mode='ExclusiveLock' AND granted").fetchone()[0]
        report["max_api_lock_owners"] = max(report["max_api_lock_owners"], count)
        assert count <= 1, "Concurrent API writers detected in isolated database"
        if predicate():
            return
        time.sleep(.25)
    raise TimeoutError("Isolated topology transition timed out; private logs retained")


def main() -> None:
    """Create, exercise and clean only a fresh namespace and test database."""
    sys.path.insert(0, str(API))
    from app.config import Settings
    from app.manage import migrate
    from app.operations_health import host

    source = Settings.from_env().database_url
    parameters = conninfo_to_dict(source)
    assert parameters.get("host") in {"127.0.0.1", "localhost"} and parameters.get("port") == "55432"
    name = "owner_agent_topology_" + uuid4().hex[:12] + "_test"
    assert re.fullmatch(r"owner_agent_topology_[0-9a-f]{12}_test", name)
    url = make_conninfo(**{**parameters, "dbname": name})
    directory = ROOT / ".local/team-workspace/platform-expansion/operations" / name
    directory.mkdir(parents=True)
    report = {"kind": "same_host_logical_active_standby", "physical_ha": False,
              "database": name, "max_api_lock_owners": 0, "external_model_calls": 0,
              "knowledge_vectors": "shared_test_data_dir_and_vault_single_qdrant_writer",
              "capacity_scope": "health_endpoint_only_not_business_capacity", "passed": False}
    hosts, created = [], False
    try:
        with psycopg.connect(source, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(migrate(url))
        with psycopg.connect(url, autocommit=True) as connection:
            assert connection.info.dbname == name
            connection.execute("CREATE TABLE topology_probe_marker(value text)")
            connection.execute("INSERT INTO topology_probe_marker VALUES ('preserved')")
            report["resource_budget"] = {
                "capacity_level_seconds": 5, "max_requests_per_level": 2000,
                "health_concurrency_levels": [1, 4], "per_request_timeout_seconds": 2,
                "memory_sampling_interval_seconds": .1, "work_execution": False,
                "model_requests": False, "business_dataset": "fresh_empty_isolated_database_plus_marker",
                "capacity_interpretation": "health_only_no_model_task_gpu_or_production_capacity_claim",
            }
            shared = directory / "shared-data"
            shared.mkdir()
            active = Supervisor(directory / "active", url, shared, 0)
            hosts.append(active)
            wait_for(connection, lambda: ready(active.port), report)
            standby = Supervisor(directory / "standby", url, shared, 1)
            hosts.append(standby)
            # Wait for an actual standby restart attempt, not just an unstarted process.
            def rejected():
                path = shared / "services/api-1.json"
                if not path.exists():
                    return False
                return json.loads(path.read_text())["restarts"] >= 1 and not ready(standby.port)
            wait_for(connection, rejected, report)
            report["standby_refused_while_active"] = True
            from qdrant_client import QdrantClient
            try:
                duplicate_vectors = QdrantClient(path=str(shared / "vectors"))
            except RuntimeError as error:
                assert "already accessed" in str(error)
                report["shared_qdrant_second_writer_rejected"] = True
            else:
                duplicate_vectors.close()
                raise AssertionError("Shared Qdrant directory was not exclusively held")
            report["data_scale"] = {
                table: connection.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
                for table in ("users", "model_configs", "conversations", "task_runs", "work_queue_runs",
                              "workflow_runs", "knowledge_bases", "documents")
            }
            child_pid = active.api_child()
            report["capacity"] = [capacity(active.port, concurrency, child_pid) for concurrency in (1, 4)]
            assert all(row["requests"] > 0 and row["errors"] == 0 and row["rejected_429"] == 0
                       and row["successes"] == row["requests"] for row in report["capacity"])
            began = time.monotonic()
            active.stop(crash=True)
            wait_for(connection, lambda: ready(standby.port), report)
            report["takeover_seconds"] = round(time.monotonic() - began, 3)
            assert connection.execute("SELECT value FROM topology_probe_marker").fetchone()[0] == "preserved"
            report["marker_preserved"] = True
            standby.stop()
            with psycopg.connect(url, autocommit=True) as maintenance:
                for identifier in (710014, 710017):
                    assert maintenance.execute("SELECT pg_try_advisory_lock(%s)", (identifier,)).fetchone()[0]
                blocked = Supervisor(directory / "maintenance-standby", url, shared, 2)
                hosts.append(blocked)
                def maintenance_rejected():
                    path = shared / "services/api-2.json"
                    return path.exists() and json.loads(path.read_text())["restarts"] >= 1 and not ready(blocked.port)
                wait_for(connection, maintenance_rejected, report)
                report["maintenance_guard_refused_standby"] = True
            wait_for(connection, lambda: ready(blocked.port), report)
            report["resumed_after_maintenance"] = True
            observations = host(directory)
            report["host_observation"] = {key: observations[key] for key in
                ("logical_cpus", "memory_total", "memory_available", "cpu_percent", "disk_free")}
            report["passed"] = True
    finally:
        errors = []
        for instance in reversed(hosts):
            try:
                instance.stop()
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                errors.append(type(error).__name__)
        if created and not errors:
            with psycopg.connect(source, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        report["test_database_removed"] = created and not errors
        report["cleanup_errors"] = errors
        report["passed"] = report["passed"] and not errors
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if errors:
            raise RuntimeError("Isolated supervisor cleanup failed; inspect recorded directory")


def probe_host(slot: int, port: int) -> None:
    """Use separate test supervisor manifests around the unchanged single-writer API."""
    sys.path.insert(0, str(API))
    from app.config import Settings
    from app.service_host import ServiceHost, acquire

    settings = Settings.from_env()
    assert re.fullmatch(r"owner_agent_topology_[0-9a-f]{12}_test", conninfo_to_dict(settings.database_url)["dbname"])
    shared = settings.data_dir.resolve()
    allowed = ROOT / ".local/team-workspace/platform-expansion/operations"
    assert shared.name == "shared-data" and shared.parent.parent == allowed
    host = ServiceHost("api", slot, port, shared / "services")
    descriptor = acquire(host.manifest.with_suffix(".lock"))
    try:
        host.stop_file.unlink(missing_ok=True)
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(host.serve())
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="仅自建隔离数据库的主备边界及健康容量探针")
    parser.add_argument("--probe-host", type=int, choices=range(3))
    parser.add_argument("--port", type=int)
    arguments = parser.parse_args()
    if arguments.probe_host is None:
        main()
    else:
        assert arguments.port and 1024 <= arguments.port <= 65535
        probe_host(arguments.probe_host, arguments.port)
