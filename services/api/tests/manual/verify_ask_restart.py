"""Local protocol double: pause one request while the operator restarts only this API."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import httpx

from app.config import Settings
from app.storage import Database

API = Path(__file__).parents[2]
ROOT = API.parents[1]
DIRECTORY = API / ".local" / "validation"
RESUME = DIRECTORY / "ask-restart.resume"
RESPONSE = "独立后台在 API 重启后完成，未重复请求。"


async def database_check(own: str | None = None, model: str | None = None) -> None:
    database = Database(Settings.from_env().database_url)
    await database.pool.open(wait=True, timeout=10)
    try:
        for table in ["task_runs", "work_queue_runs", "workflow_runs"]:
            rows = await database.fetch(
                f"SELECT id FROM {table} WHERE status IN ('PENDING','RUNNING') AND (%s::uuid IS NULL OR id<>%s)",
                (UUID(own) if own else None, UUID(own) if own else None),
            )
            if rows:
                raise RuntimeError("User work is active: do not restart the API")
        if model and own:
            await database.execute("DELETE FROM model_usage WHERE run_id=%s AND model_id=%s", (UUID(own), model))
    finally:
        await database.pool.close()


def check_database(own=None, model=None):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(database_check(own, model))


def main() -> None:
    check_database()
    DIRECTORY.mkdir(exist_ok=True)
    RESUME.unlink(missing_ok=True)
    entered, released = threading.Event(), threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body.get("model"))
            entered.set()
            if not released.wait(120):
                self.send_error(504)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            base = {"id": "manual-ask-restart", "object": "chat.completion.chunk", "created": 1, "model": body["model"]}
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": RESPONSE}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}},
            ]
            for chunk in chunks:
                self.wfile.write(("data: " + json.dumps({**base, **chunk}) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    values = dict(line.split("=", 1) for line in (API / ".env.admin").read_text().splitlines() if "=" in line)
    model, conversation, run = "", "", ""
    with httpx.Client(
        base_url="http://127.0.0.1:8100", timeout=10, trust_env=False, headers={"Origin": "http://localhost:3100"}
    ) as client:
        try:
            client.post(
                "/api/v1/auth/login", json={"username": values["ADMIN_USERNAME"], "password": values["ADMIN_PASSWORD"]}
            ).raise_for_status()
            created = client.post(
                "/api/v1/admin/models",
                json={
                    "name": "本机API重启验收",
                    "model": "restart-local-only",
                    "kind": "chat",
                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                    "api_key": "local-only",
                    "max_output_tokens": 64,
                },
            )
            created.raise_for_status()
            model = created.json()["id"]
            created = client.post("/api/v1/conversations", json={"title": "本机API重启验收"})
            created.raise_for_status()
            conversation = created.json()["id"]
            submitted = client.post(
                f"/api/v1/conversations/{conversation}/runs",
                json={"model_id": model, "message": "仅使用本机模型替身验证后台跨API重启", "native_search": False},
            )
            submitted.raise_for_status()
            run = submitted.json()["run_id"]
            if not entered.wait(15):
                raise RuntimeError("Worker did not request the local double")
            check_database(run)
            before = (ROOT / ".local" / "api.pid").read_text().strip()
            print(json.dumps({"state": "ready_for_api_restart", "run_id": run, "resume_file": str(RESUME)}), flush=True)
            deadline = time.monotonic() + 90
            while not RESUME.exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("API restart verification was not resumed")
                time.sleep(0.25)
            after = (ROOT / ".local" / "api.pid").read_text().strip()
            if before == after:
                raise RuntimeError("API process did not change")
            deadline = time.monotonic() + 15
            while True:
                try:
                    client.get("/health/ready").raise_for_status()
                    break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.25)
            released.set()
            deadline = time.monotonic() + 20
            while True:
                state = client.get(f"/api/v1/runs/{run}").json()
                if state["status"] not in {"PENDING", "RUNNING"}:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Run did not finish")
                time.sleep(0.25)
            assert state["status"] == "SUCCEEDED" and len(calls) == 1
            usage = client.get(f"/api/v1/conversations/{conversation}/usage").json()["total"]
            assert usage["calls"] == 1 and usage["input_tokens"] == 10 and usage["output_tokens"] == 3
            report = {
                "case": "actual_api_restart_with_independent_worker",
                "passed": True,
                "local_double_calls": len(calls),
                "usage": usage,
                "api_process_changed": True,
                "external_providers_called": False,
            }
            (DIRECTORY / "ask-restart-verification.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False), flush=True)
        finally:
            released.set()
            if run:
                client.post(f"/api/v1/runs/{run}/cancel")
                time.sleep(0.5)
            if conversation:
                client.delete(f"/api/v1/conversations/{conversation}")
            if model:
                client.delete(f"/api/v1/admin/models/{model}")
            if run and model:
                check_database(run, model)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
