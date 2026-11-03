"""Task-private encrypted browser state with bounded, current-domain restoration."""

import json
from urllib.parse import urlsplit

from app.errors import fail

MAX_STATE = 2 * 1024 * 1024


def allowed_origin(url: str, domains: list[str]) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in domains
            and parsed.port in (None, 443)
            and not parsed.username
        )
    except (ValueError, TypeError):
        return False


def filter_state(state: dict, domains: list[str]) -> dict:
    """Drop state for removed grants; this object must never become a model tool result."""
    if not isinstance(state, dict) or len(json.dumps(state, allow_nan=False).encode()) > MAX_STATE:
        fail(413, "BROWSER_STATE_LIMIT", "浏览器登录状态超过 2 MB，未覆盖上次保存的状态")
    storage = state.get("storage", {})
    if not isinstance(storage, dict):
        fail(422, "BROWSER_STATE_INVALID", "浏览器状态格式无效")
    cookies = []
    for cookie in storage.get("cookies", []):
        domain = cookie.get("domain", "").lstrip(".").lower()
        if domain and any(host == domain or host.endswith("." + domain) for host in domains):
            cookies.append(cookie)
    return {
        "storage": {
            "cookies": cookies,
            "origins": [
                origin for origin in storage.get("origins", []) if allowed_origin(origin.get("origin", ""), domains)
            ],
        },
        "session": {
            origin: values for origin, values in state.get("session", {}).items() if allowed_origin(origin, domains)
        },
        "pages": [url for url in state.get("pages", [])[:10] if allowed_origin(url, domains)],
    }


async def load_state(database, vault, task, owner, domains):
    rows = await database.fetch(
        "SELECT s.state_ciphertext,s.revision FROM work_browser_states s JOIN work_tasks t ON t.id=s.task_id "
        "WHERE s.task_id=%s AND s.owner_id=%s AND t.owner_id=%s",
        (task, owner, owner),
    )
    if not rows:
        return {}, 0
    raw = vault.decrypt(rows[0]["state_ciphertext"]).get_secret_value()
    return filter_state(json.loads(raw), domains) if raw else {}, rows[0]["revision"]


async def save_state(database, vault, task, owner, domains, state, revision, guard):
    filtered = filter_state(state, domains)
    encoded = vault.encrypt(json.dumps(filtered, ensure_ascii=False, allow_nan=False))
    async with database.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710035,hashtext(%s))", (str(task),))
        await guard()
        await connection.execute(
            "INSERT INTO work_browser_states(task_id,owner_id) SELECT id,owner_id FROM work_tasks "
            "WHERE id=%s AND owner_id=%s ON CONFLICT(task_id) DO NOTHING",
            (task, owner),
        )
        row = await (
            await connection.execute(
                "UPDATE work_browser_states SET state_ciphertext=%s,revision=revision+1,"
                "status='READY',updated_at=now() "
                "WHERE task_id=%s AND owner_id=%s AND revision=%s RETURNING revision",
                (encoded, task, owner, revision),
            )
        ).fetchone()
        if not row:
            fail(409, "BROWSER_STATE_CHANGED", "浏览器状态已更新或来源任务已删除，未覆盖其他版本")
    return row["revision"]


def public_result(result: dict) -> dict:
    """Model-visible results omit URL query/fragment and never contain private storage."""
    safe = {key: result[key] for key in ("text", "title", "active") if key in result}
    for key in ("url",):
        if key in result:
            parsed = urlsplit(result[key])
            safe[key] = parsed._replace(query="", fragment="").geturl()
    if "tabs" in result:
        safe["tabs"] = [{"index": tab["index"], **public_result(tab)} for tab in result["tabs"]]
    return safe
