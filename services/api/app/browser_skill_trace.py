"""Bounded import of server-produced BrowserSkill v3 bundles; never retain raw input."""

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.errors import APIError, fail

SENSITIVE = re.compile(r"password|passwd|secret|token|api.?key|otp|验证码|密码|密钥|令牌|bearer", re.I)
SUPPORTED = {"navigate", "click", "fill", "select"}


def invalid() -> None:
    fail(422, "BROWSER_TRACE_INVALID", "录制文件不完整、超限或格式不受支持")


def safe_url(value: str) -> str:
    """Keep only explicit credential-free origins and paths, never query values."""
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and not SENSITIVE.search(unquote(value))
            and len(value) <= 2000
            and not any(ord(c) <= 32 for c in value)
        )
        _ = parts.port
    except (ValueError, TypeError):
        valid = False
    if not valid:
        fail(422, "BROWSER_URL_INVALID", "起点须为不含凭据、查询参数或敏感片段的 HTTP(S) 地址")
    return value


def checked_file(root: Path, relative: str, maximum: int) -> str:
    path = root / relative
    if root.is_symlink() or any(p.is_symlink() for p in (path, path.parent)):
        invalid()
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > maximum:
        invalid()
    return path.read_text(encoding="utf-8-sig")


def import_bundle(root: Path) -> dict:
    """Accept only canonical v3 bundle paths; page bodies are validated then discarded."""
    try:
        trace = json.loads(checked_file(root, "trace.json", 512 * 1024))
        states, steps = trace["states"], trace["steps"]
        if trace["version"] != 3 or not 1 <= len(states) <= 200 or not 1 <= len(steps) <= 100:
            invalid()
        ids, total = set(), 0
        for state in states:
            sid = state["id"]
            if not re.fullmatch(r"s[1-9][0-9]*", sid) or sid in ids or state["page"] != f"{sid}.txt":
                invalid()
            body = checked_file(root, f"states/{state['page']}", 128 * 1024)
            total += len(body.encode())
            ids.add(sid)
        if total > 2 * 1024 * 1024:
            invalid()
        for step in steps:
            if step["state"] not in ids or step["result"]["state"] not in ids:
                invalid()
        return normalize(trace)
    except (KeyError, TypeError, ValueError, OSError, UnicodeError):
        invalid()


def normalize(trace: dict) -> dict:
    """Generate required parameters; discard every original fill/select value and VOM."""
    values = [s.get("value", "") for s in trace["steps"] if s.get("op") == "fill"]
    values += [v.get("value", "") for s in trace["steps"] for v in s.get("selection", [])]
    secrets = [v for v in values if isinstance(v, str) and v]
    truncated = {s["id"] for s in trace["states"] if s.get("truncated")}
    result, parameters = [], []
    for index, step in enumerate(trace["steps"], 1):
        op, blocked = step.get("op"), []
        item = {"id": index, "op": op if op in SUPPORTED else "unsupported"}
        if op not in SUPPORTED:
            blocked.append("不支持的操作")
        if step["state"] in truncated or step["result"]["state"] in truncated:
            blocked.append("页面观察被截断")
        if op == "navigate":
            url = step.get("to", "")
            if any(v in url for v in secrets):
                blocked.append("地址包含输入值")
            else:
                try:
                    item["url"] = safe_url(url)
                except APIError:
                    blocked.append("地址含敏感内容或不受支持")
        elif op in SUPPORTED:
            target = step.get("target", {})
            label = " ".join(str(target.get(k, "")) for k in ("role", "name", "ctx"))
            sensitive = bool(step.get("redacted") or SENSITIVE.search(label))
            clean = {k: str(target[k]) for k in ("role", "name", "ctx") if target.get(k)}
            if sensitive or any(v in label for v in secrets) or any(len(v) > 200 for v in clean.values()):
                clean = {}
                blocked.append("敏感目标必须人工处理")
            if target.get("unmatched") or not clean.get("role") or not clean.get("name"):
                blocked.append("目标缺失或无法定位")
            item["target"] = clean
            if op in {"fill", "select"}:
                if op == "select" and len(step.get("selection", [])) != 1:
                    blocked.append("暂不支持多选或空选择")
                name = f"input_{index}"
                item["parameter"] = name
                parameters.append({"name": name, "description": "运行时提供的输入", "required": True})
        item["blocked"] = blocked
        result.append(item)
    if result[0]["op"] != "navigate":
        entry = trace.get("entry", {}).get("start_url", "")
        try:
            if any(v in entry for v in secrets):
                invalid()
            first = {"id": 0, "op": "navigate", "url": safe_url(entry), "blocked": []}
        except APIError:
            first = {"id": 0, "op": "navigate", "blocked": ["起点地址不受支持"]}
        result.insert(0, first)
    return {"steps": result, "parameters": parameters}


def validate_draft(draft: dict) -> None:
    """Revalidate edited metadata without allowing callers to unblock captured failures."""
    names = [p["name"] for p in draft["parameters"]]
    if len(names) != len(set(names)) or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", n) for n in names):
        fail(422, "BROWSER_PARAMETER_INVALID", "参数名须唯一且使用小写英文、数字和下划线")
    if any(s.get("parameter") not in names for s in draft["steps"] if s["op"] in {"fill", "select"}):
        fail(422, "BROWSER_PARAMETER_MISSING", "步骤引用的参数不存在")
    if any(s["blocked"] for s in draft["steps"]):
        fail(422, "BROWSER_PUBLISH_BLOCKED", "路径包含无法执行的步骤，请重新录制")
