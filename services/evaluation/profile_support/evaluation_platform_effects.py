"""Target-only synchronous lease delegate. Check actual effects; never trust cached grants."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID

CONTRACT = "platform-effect-permit-v1"
ORIGIN = "EVALUATION_PLATFORM_PERMIT_ORIGIN"
SECRET = "EVALUATION_PLATFORM_PERMIT_SECRET"


def configuration():
    origin, secret = os.environ.get(ORIGIN), os.environ.get(SECRET)
    if origin is None and secret is None:
        return None  # Standalone controlled targets have no parent platform worker.
    if not origin or not secret or len(secret) < 32 or len(secret) > 256:
        raise RuntimeError("Parent evaluation permission delegate is incomplete")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not parsed.port
        or not 1024 <= parsed.port <= 65535
        or parsed.port in {3100, 3131, 8100, 8101, 18180}
    ):
        raise RuntimeError("Parent evaluation delegate must be its private loopback origin")
    return origin, secret


def require_permit(environment_id, case_id, mode, boundary, *, required=False):
    configured = configuration()
    if configured is None:
        if required:
            raise RuntimeError("Required parent delegation disappeared before permission request")
        return
    if (
        not isinstance(case_id, str)
        or re.fullmatch(r"AET-[AWF][0-9]{2}", case_id) is None
        or mode not in {"ask", "work", "workflow", "resource"}
        or boundary not in {"provider", "tool", "sandbox", "submission"}
    ):
        raise RuntimeError("Invalid fixed parent permission scope")
    payload = {
        "environment_id": str(UUID(str(environment_id))),
        "case_id": case_id,
        "mode": mode,
        "boundary": boundary,
    }
    origin, secret = configured
    request = Request(
        origin + "/permit",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/json",
        },
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            raw = response.read(513)
            value = json.loads(raw)
            if (
                response.status != 200
                or len(raw) > 512
                or not isinstance(value, dict)
                or set(value) != {"allowed"}
                or value["allowed"] is not True
            ):
                raise RuntimeError("Parent evaluation lease or permission denied")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError("Parent evaluation lease or permission unavailable") from None
