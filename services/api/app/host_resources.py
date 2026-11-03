"""Conservative local admission estimate; no changes to other Docker workloads."""

import asyncio
import ctypes
import os
import re
import subprocess
import threading
import time
from pathlib import Path

GIB = 1024**3
_cache = (0.0, 0)
_lock = threading.Lock()


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
    global _cache
    with _lock:
        if time.monotonic() - _cache[0] < 5:
            return _cache[1]
        try:
            total = int(docker(["info", "--format", "{{.MemTotal}}"]).strip())
            used = sum(
                bytes_size(line.split("/", 1)[0])
                for line in docker(["stats", "--no-stream", "--format", "{{.MemUsage}}"]).splitlines()
                if line.strip()
            )
            budget = max(0, min(host_available(), total - used) - GIB)
        except (OSError, ValueError, subprocess.SubprocessError):
            budget = 0
        _cache = (time.monotonic(), budget)
        return budget


async def resource_budget() -> int:
    """Reserve one GiB margin and count current container use plus platform reservations conservatively."""
    return await asyncio.to_thread(sample)
