import json
import os
import subprocess
import sys

from evaluation_runner import fixed_evidence, network
from evaluation_runner.fixed_run import main


def test_fresh_process_records_real_loopback_sockets_by_phase_without_request_secrets(tmp_path):
    script = r'''
import json, socket, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from evaluation_runner import fixed_audit, network
from evaluation_runner.contracts import Check
from evaluation_runner.metrics import score

class Receiver(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'OK')
    def log_message(self, *args):
        pass

server = HTTPServer(('127.0.0.1', 0), Receiver)
thread = threading.Thread(target=server.handle_request)
thread.start()
port = server.server_port
session = network.Session('run', {'case_id': 'AET-A02'}, f'http://127.0.0.1:{port}')
network.install_guard(port, session=session)
directory = Path(sys.argv[1])
fixed_audit.begin(directory, session)
with network.phase('execution'):
    connection = socket.create_connection(('127.0.0.1', port))
    connection.sendall(b'GET /canary?token=secret-query HTTP/1.0\r\nAuthorization: Bearer secret-header\r\n\r\n')
    while connection.recv(4096):
        pass
    connection.close()
    try:
        socket.getaddrinfo('198.51.100.1', 443)
        raise AssertionError('Denied destination reached resolver')
    except PermissionError:
        pass
metric = score(Check(registered_id='tool_correctness', parameters={'expected_tools': []}), {'tools_complete': True, 'tools': []})
assert metric.status == 'PASS' and 'judge.generate=0' in metric.reason
with network.phase('export'):
    pass
fixed_audit.seal(directory, session, 'COMPLETE')
try:
    socket.create_connection(('127.0.0.1', port))
    raise AssertionError('Post-seal socket was allowed')
except PermissionError:
    pass
thread.join(); server.server_close()
'''
    environment = {key: value for key, value in os.environ.items()
                   if key in {"SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "PATH", "WINDIR"}}
    result = subprocess.run([sys.executable, "-I", "-c", script, str(tmp_path)], env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=40, check=False)
    assert result.returncode == 0, result.stderr
    audit_index = json.loads((tmp_path / "network-audit-index.json").read_text(encoding="utf-8"))
    value = fixed_evidence.load(tmp_path, audit_index["sessions"][0]["reference"], "network-audit")
    assert {row["name"] for row in value["phases"]} == {"execution", "import", "metric", "export"}
    assert all(row["status"] == "COMPLETE" for row in value["phases"])
    assert any(row["event"] == "socket.connect" and row["allowed"] for row in value["events"])
    assert any(row["event"] == "socket.getaddrinfo" and not row["allowed"] for row in value["events"])
    assert not any(row["phase"] in {"import", "metric", "export"} for row in value["events"])
    encoded = json.dumps(value)
    assert not any(secret in encoded for secret in ("secret-query", "secret-header", "Authorization", "198.51.100.1", "/canary"))


def test_describe_has_zero_event_audit_without_platform_login(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fixed_run", "describe", "--case-id", "AET-A02"])
    assert main() == 0
    description = json.loads(capsys.readouterr().out)
    assert description["network_audit"]["action"] == "describe"
    assert description["network_audit"]["events"] == []
    assert description["network_audit"]["policy"]["allowed_origin"] is None
    assert all(row["status"] == "COMPLETE" for row in description["network_audit"]["phases"])
    assert network.ACTIVE.closed


def test_bad_target_still_seals_error_audit_without_echoing_input(tmp_path, monkeypatch, capsys):
    target = tmp_path / "invalid-target.json"
    target.write_text('{"secret": "private canary"}', encoding="utf-8")
    output = tmp_path / "result"
    monkeypatch.setattr(sys, "argv", ["fixed_run", "preflight", "--case-id", "AET-A02",
                                     "--target-manifest", str(target), "--output", str(output)])
    assert main() == 2
    audit_index = json.loads((output / "network-audit-index.json").read_text(encoding="utf-8"))
    audit = fixed_evidence.load(output, audit_index["sessions"][0]["reference"], "network-audit")
    assert audit["status"] == "ERROR" and audit["phases"][0]["status"] == "ERROR"
    assert "private canary" not in json.dumps(audit) + capsys.readouterr().out
