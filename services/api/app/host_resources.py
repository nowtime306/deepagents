"""Conservative local admission estimate; no changes to other Docker workloads."""

import asyncio
import ctypes
import os
import re
import subprocess
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

GIB = 1024**3
_cache = (0.0, 0)
_lock = threading.Lock()
_cache_observation = None
_observation = ContextVar("resource_budget_observation", default=None)


def budget_observation(*, reset=False):
    """Return only this caller's actual sample, never manufacture a new historic observation."""
    value = _observation.get()
    if reset:
        _observation.set(None)
    return dict(value) if value is not None else None


def bytes_size(value: str) -> int:
    match = re.fullmatch(r"([0-9.]+)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)", value.strip())
    if not match:
        raise ValueError("Unknown memory unit")
    units = {
        "B": 1,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": GIB,
        "TiB": 1024**4,
    }
    return int(float(match[1]) * units[match[2]])


def host_available() -> int:
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                *[
                    (name, ctypes.c_ulonglong)
                    for name in [
                        "total",
                        "available",
                        "total_page",
                        "available_page",
                        "total_virtual",
                        "available_virtual",
                        "extended",
                    ]
                ],
            ]

        state = MemoryStatus()
        state.length = ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            raise OSError("Memory probe failed")
        return state.available
    values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(values["MemAvailable"].split()[0]) * 1024


def docker(arguments: list[str]) -> str:
    result = subprocess.run(
        ["docker", *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=8,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.stdout


def sample() -> int:
    global _cache, _cache_observation
    with _lock:
        if time.monotonic() - _cache[0] < 5:
            _observation.set({**_cache_observation, "cached": True} if _cache_observation else None)
            return _cache[1]
        started = time.monotonic()
        observation = {"sampled_at": datetime.now(UTC).isoformat(), "cached": False,
                       "host_available_bytes": None, "docker_total_bytes": None, "docker_used_bytes": None,
                       "margin_bytes": GIB, "probe_stage": "docker_total", "error_type": None, "status": "OK"}
        try:
            total = int(docker(["info", "--format", "{{.MemTotal}}"]).strip())
            observation.update(docker_total_bytes=total, probe_stage="docker_used")
            used = sum(
                bytes_size(line.split("/", 1)[0])
                for line in docker(["stats", "--no-stream", "--format", "{{.MemUsage}}"]).splitlines()
                if line.strip()
            )
            observation.update(docker_used_bytes=used, probe_stage="host_available")
            available = host_available()
            observation["host_available_bytes"] = available
            budget = max(0, min(available, total - used) - GIB)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            budget = 0
            names = {OSError, PermissionError, FileNotFoundError, ValueError,
                     subprocess.SubprocessError, subprocess.TimeoutExpired, subprocess.CalledProcessError}
            observation.update(
                status="ERROR", error_type=type(error).__name__ if type(error) in names else "OtherProbeError")
        observation.update(budget_bytes=budget, elapsed_ms=round((time.monotonic() - started) * 1000))
        _cache_observation = observation
        _observation.set(dict(observation))
        _cache = (time.monotonic(), budget)
        return budget


async def resource_budget() -> int:
    """Reserve one GiB margin and count current container use plus platform reservations conservatively."""
    def observed():
        budget_observation(reset=True)
        result = sample()
        return result, budget_observation()
    result, observation = await asyncio.to_thread(observed)
    _observation.set(observation)
    return result
