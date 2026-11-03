"""Run only inside an already approved unprivileged Work container, never on the host."""

import ctypes
import json
import os
import select
import signal
import subprocess
import sys
import time


def execute(body):
    # This Linux-only bridge owns its CLI descendants even when a CLI exits first.
    # Keep the outer Work process group so the existing hard timeout also applies.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError("PROTOCOL_SUBREAPER_UNAVAILABLE")

    def cancelled(signum, frame):
        raise RuntimeError("PROTOCOL_CANCELLED")

    previous = {s: signal.signal(s, cancelled) for s in (signal.SIGTERM, signal.SIGINT)}
    process = None
    updates, buffer = [], b""
    deadline, size = time.monotonic() + 45, 0

    def send(value, *, until=None):
        payload = json.dumps({"jsonrpc": "2.0", **value}).encode() + b"\n"
        if len(payload) > 131072:
            raise RuntimeError("PROTOCOL_INPUT_LIMIT")
        end = deadline if until is None else until
        descriptor = process.stdin.fileno()
        pending = memoryview(payload)
        while pending:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("PROTOCOL_WRITE_TIMEOUT")
            if not select.select([], [descriptor], [], min(remaining, 0.1))[1]:
                continue
            try:
                written = os.write(descriptor, pending[:4096])
            except BlockingIOError:
                continue
            if not written:
                raise RuntimeError("PROTOCOL_PROCESS_EXITED")
            pending = pending[written:]

    def request(identifier, method, params):
        nonlocal buffer, size
        send({"id": identifier, "method": method, "params": params})
        while time.monotonic() < deadline:
            if b"\n" not in buffer:
                if not select.select([process.stdout], [], [], 0.1)[0]:
                    continue
                piece = os.read(process.stdout.fileno(), 8192)
                if not piece:
                    raise RuntimeError("PROTOCOL_PROCESS_EXITED")
                buffer += piece
                size += len(piece)
                if size > 524288 or len(buffer) > 131072:
                    raise RuntimeError("PROTOCOL_OUTPUT_LIMIT")
                continue
            line, buffer = buffer.split(b"\n", 1)
            event = json.loads(line)
            if event.get("jsonrpc") != "2.0":
                raise RuntimeError("PROTOCOL_INVALID_MESSAGE")
            if event.get("id") == identifier and "method" not in event:
                if "error" in event:
                    raise RuntimeError("PROTOCOL_REMOTE_ERROR")
                return event.get("result", {})
            if "method" in event and "id" in event:
                # No client filesystem/terminal/permission capabilities are advertised.
                send({"id": event["id"], "error": {"code": -32601, "message": "Client capability denied"}})
            elif event.get("method") == "session/update":
                if len(updates) < 100:
                    updates.append(event.get("params"))
        raise RuntimeError("PROTOCOL_TIMEOUT")

    session = None
    try:
        process = subprocess.Popen(
            body["command"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/workspace",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/workspace", "LANG": "C.UTF-8"},
            bufsize=0,
        )
        os.set_blocking(process.stdin.fileno(), False)
        if body["transport"] == "acp":
            initialized = request(
                1,
                "initialize",
                {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": {"name": "owner-agent", "version": "1"}},
            )
            if initialized.get("protocolVersion") != 1:
                raise RuntimeError("ACP_VERSION_UNSUPPORTED")
            session = request(2, "session/new", {"cwd": "/workspace", "mcpServers": []})["sessionId"]
            result = request(
                3, "session/prompt", {"sessionId": session, "prompt": [{"type": "text", "text": body["text"]}]}
            )
            return {"session_id": session, "result": result, "updates": updates}
        initialized = request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "owner-agent", "version": "1"},
            },
        )
        if initialized.get("protocolVersion") not in {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}:
            raise RuntimeError("MCP_VERSION_UNSUPPORTED")
        send({"method": "notifications/initialized"})
        if body["operation"] == "discover":
            return request(2, "tools/list", {})
        return request(2, "tools/call", {"name": body["tool_name"], "arguments": body["arguments"]})
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        if process is None:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            raise RuntimeError("PROTOCOL_PROCESS_START_FAILED")
        if session:
            try:
                send({"method": "session/cancel", "params": {"sessionId": session}}, until=time.monotonic() + 0.25)
            except Exception:
                # A best-effort notification must never prevent process cleanup.
                pass
        def terminate_children(parent):
            path = f"/proc/{parent}/task/{parent}/children"
            try:
                with open(path, encoding="ascii") as source:
                    children = source.read().split()
            except FileNotFoundError:
                return
            for child in children:
                descriptor = None
                try:
                    descriptor = os.pidfd_open(int(child))
                    with open(f"/proc/{child}/stat", encoding="ascii") as source:
                        ppid = int(source.read().rsplit(")", 1)[1].split()[1])
                    if ppid != parent:
                        continue
                    # PID handles avoid targeting a recycled, unrelated process.
                    signal.pidfd_send_signal(descriptor, signal.SIGSTOP)
                    terminate_children(int(child))
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except (ProcessLookupError, FileNotFoundError):
                    pass
                finally:
                    if descriptor is not None:
                        os.close(descriptor)

        try:
            # Repeat after each reap: children exiting during enumeration may
            # have reparented additional descendants to this subreaper.
            while True:
                terminate_children(os.getpid())
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                    if not pid:
                        time.sleep(0.01)
                except ChildProcessError:
                    break
            process.wait()
        finally:
            process.stdin.close()
            process.stdout.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    print(json.dumps(execute(json.loads(sys.stdin.read())), ensure_ascii=False))
