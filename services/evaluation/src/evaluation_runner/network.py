"""Python socket observations; this is not an operating-system network boundary."""

import ipaddress
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

EVENTS: list[dict] = []
PHASE = "import"
ACTIVE = None
_generation = 0


def now():
    return datetime.now(UTC).isoformat()


class Session:
    """One evaluator command's socket audit, including phases with no socket events."""

    def __init__(self, action: str, identity: dict, origin: str | None):
        self.data = {"schema_version": 1, "session_id": str(uuid4()), "action": action,
                     "identity": identity, "policy": {"allowed_origin": origin, "redirects": False},
                     "scope": "Python evaluator socket audit only; excludes OS, platform workers and SSE trajectory",
                     "started_at": now(), "ended_at": None, "status": "PENDING", "guard_enabled": True,
                     "phases": [], "events": []}
        self.closed = False


@contextmanager
def phase(name: str):
    global PHASE
    previous = PHASE
    PHASE = name
    session = ACTIVE
    record = {"name": name, "started_at": now(), "ended_at": None, "status": "PENDING", "event_count": 0}
    if session is not None:
        session.data["phases"].append(record)
    try:
        yield
    except BaseException:
        record["status"] = "ERROR"
        raise
    else:
        record["status"] = "COMPLETE"
    finally:
        record["ended_at"] = now()
        PHASE = previous


def install_guard(port: int | None, *, session: Session | None = None) -> None:
    global ACTIVE, _generation
    ACTIVE = session
    _generation += 1
    generation = _generation
    os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT="1", DEEPEVAL_DISABLE_DOTENV="1")

    def audit(event, arguments):
        if generation != _generation:
            return
        if event not in {"socket.connect", "socket.getaddrinfo"}:
            return
        address = arguments[1] if event == "socket.connect" else arguments[:2]
        host, destination = address[:2]
        if host == "localhost":
            local = True
        else:
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = False
        allowed = local and port is not None and destination == port and not (session and session.closed)
        observation = {"phase": PHASE, "event": event, "allowed": allowed}
        EVENTS.append(observation)
        if session is not None:
            session.data["events"].append(observation)
            for record in reversed(session.data["phases"]):
                if record["status"] == "PENDING" and record["name"] == PHASE:
                    record["event_count"] += 1
                    break
        if not allowed:
            raise PermissionError("Evaluation network allowlist blocked a connection")

    sys.addaudithook(audit)
