"""Host capacity observations; measurements are not throughput guarantees."""

import ctypes
import os
import platform
import shutil
import sys
import threading
import time

_cpu = None
_mutex = threading.Lock()


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong)
        for name in (
            "total_physical",
            "available_physical",
            "total_page",
            "available_page",
            "total_virtual",
            "available_virtual",
            "extended",
        )
    ]


def host(path):
    global _cpu
    disk = shutil.disk_usage(path)
    result = {
        "logical_cpus": os.cpu_count(),
        "system": platform.system(),
        "disk_total": disk.total,
        "disk_free": disk.free,
        "memory_total": None,
        "memory_available": None,
        "cpu_percent": None,
    }
    if sys.platform == "win32":
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result.update(memory_total=memory.total_physical, memory_available=memory.available_physical)
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            with _mutex:
                current = (idle.value, kernel.value + user.value, time.monotonic())
                if _cpu and current[1] > _cpu[1]:
                    result["cpu_percent"] = round(100 * (1 - (current[0] - _cpu[0]) / (current[1] - _cpu[1])), 1)
                _cpu = current
    result["alerts"] = []
    if disk.free < max(1024**3, disk.total * 0.05):
        result["alerts"].append("磁盘空间不足：剩余低于1GB或总容量的5%，请先确认备份与成果存储空间。")
    available, total = result["memory_available"], result["memory_total"]
    if available is not None and available < max(512 * 1024**2, total * 0.05):
        result["alerts"].append("可用内存偏低：低于512MB或总内存的5%，请减少并行任务或保留环境。")
    return result
