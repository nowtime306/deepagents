"""Bounded, content-free diagnostics; process liveness and database admission are distinct."""

import ctypes
import os
import platform
import re
import sys
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from app.monitoring_api import PREFIX, Window, operator

router = APIRouter(tags=["监测详情"])
RUNS = """
 SELECT r.id,'ask' AS mode,r.status,r.created_at,r.finished_at,r.error_code,c.owner_id,
 c.id AS resource_id FROM task_runs r JOIN conversations c ON c.id=r.conversation_id
 UNION ALL SELECT id,'work',status,created_at,
 CASE WHEN status IN ('COMPLETED','FAILED','CANCELLED') THEN updated_at END,error_code,owner_id,task_id
 FROM work_queue_runs
 UNION ALL SELECT r.id,'workflow',r.status,r.created_at,r.finished_at,NULL,w.owner_id,w.id
 FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id
"""


def process_alive(pid):
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.c_uint)
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint))
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            status = ctypes.c_uint()
            return status.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(status)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


@router.get(PREFIX + "/instances")
async def instances(request: Request):
    await operator(request)
    db = request.app.state.db
    rows = await db.fetch(
        "SELECT s.*,m.node,m.fault_domain,m.release,m.required_locks FROM service_instances s "
        "LEFT JOIN service_instance_metadata m ON m.instance_id=s.id "
        "ORDER BY s.heartbeat_at DESC,s.id LIMIT 100")
    locks = await db.fetch("SELECT pid,objid::bigint AS id,mode FROM pg_locks WHERE locktype='advisory' "
                           "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                           "AND classid=0 AND objsubid=1 AND granted")
    now = datetime.now(UTC)
    items = []
    for row in rows:
        fresh = (now-row["heartbeat_at"]).total_seconds() < 15
        live = process_alive(row["process_id"]) if row["node"] == platform.node() else None
        expected = row["required_locks"]
        ready = None if not expected else fresh and live is not False and all(
            any(lock["pid"] == row["backend_id"] and lock["id"] == item[0] and
                lock["mode"] == ("ShareLock" if item[1] else "ExclusiveLock") for lock in locks)
            for item in expected)
        fields = ("id", "kind", "node", "fault_domain", "release", "started_at", "heartbeat_at")
        items.append({key: row[key] for key in fields}
                     | {"live": live, "ready": ready, "stale": not fresh})
    return {"items": items, "limit": 100, "sampled_at": now,
            "note": "存活为本节点进程观测；远端未探测为未知。就绪为15秒心跳与数据库准入锁，"
                    "不冒称HTTP探测。单机故障域不代表物理高可用。"}


@router.get(PREFIX + "/run-trend")
async def trend(request: Request, hours: Window = Window.HOUR):
    await operator(request)
    hours = int(hours)
    rows = await request.app.state.db.fetch(
        "WITH runs AS (" + RUNS + ") SELECT date_bin(make_interval(secs=>%s),created_at,"
        "'2020-01-01'::timestamptz) AS time,count(*) AS started,"
        "count(*) FILTER(WHERE status='FAILED') AS failed,"
        "count(*) FILTER(WHERE status IN ('COMPLETED','SUCCEEDED')) AS succeeded "
        "FROM runs WHERE created_at>=now()-make_interval(hours=>%s) GROUP BY time ORDER BY time",
        (hours*30, hours))
    return {"items": rows, "hours": hours, "source": "运行记录创建时间分桶，结果状态为当前值；空桶没有运行记录"}


@router.get(PREFIX + "/errors")
async def errors(request: Request, hours: Window = Window.HOUR):
    user = await operator(request)
    rows = await request.app.state.db.fetch(
        "WITH runs AS (" + RUNS + ") SELECT * FROM runs WHERE status='FAILED' "
        "AND created_at>=now()-make_interval(hours=>%s) ORDER BY created_at DESC,id DESC LIMIT 50", (int(hours),))
    permissions = {"ask": "chat.read", "work": "work.read", "workflow": "workflows.read"}
    return {"items": [{key: row[key] for key in ("id", "mode", "status", "created_at", "finished_at")}
            | {"error_code": row["error_code"] if re.fullmatch(r"[A-Z_0-9]{1,80}", row["error_code"] or "")
                            else "UNCLASSIFIED",
               "can_open": row["owner_id"] == user["id"] and
                           (user["is_admin"] or permissions[row["mode"]] in user["permissions"]),
               "resource_id": row["resource_id"] if row["owner_id"] == user["id"] else None} for row in rows],
            "limit": 50, "note": "仅返回最近50条脱敏错误；他人运行不开放正文或详情。"}
