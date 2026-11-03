"""Python socket observations; this is not an operating-system network boundary."""

import ipaddress
import os
import sys

EVENTS: list[dict] = []
PHASE = "import"


def install_guard(port: int) -> None:
    os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT="1", DEEPEVAL_DISABLE_DOTENV="1")

    def audit(event, arguments):
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
        allowed = local and destination == port
        EVENTS.append({"phase": PHASE, "event": event, "allowed": allowed})
        if not allowed:
            raise PermissionError("Evaluation network allowlist blocked a connection")

    sys.addaudithook(audit)
