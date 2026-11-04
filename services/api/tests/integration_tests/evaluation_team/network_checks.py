"""Engineering network guard: only the standard library's authenticated socketpair."""

import json
import socket
import sys
from pathlib import Path

COUNTS = {"authenticated_stdlib_socketpair": 0, "other_network_attempts": 0, "denied_event_names": []}
SOCKETPAIR_CODE = socket.socketpair.__code__


def audit(event, arguments):
    if event not in {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.listen"}:
        return
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code is SOCKETPAIR_CODE:
            COUNTS["authenticated_stdlib_socketpair"] += 1
            return
        frame = frame.f_back
    COUNTS["other_network_attempts"] += 1
    COUNTS["denied_event_names"].append(event)
    raise RuntimeError("No test DNS/connect/bind/listen outside the authenticated stdlib self-pair")


if __name__ == "__main__":
    sys.addaudithook(audit)
    import pytest

    status = pytest.main(sys.argv[2:])
    Path(sys.argv[1]).write_text(json.dumps(COUNTS, indent=2), encoding="utf-8")
    raise SystemExit(status)
