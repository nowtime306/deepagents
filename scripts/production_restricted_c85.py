"""Fixed restricted C85 production entry. Never migrates, restores or resets stop files."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path("D:/project/my_agent/deepagents")
HERE = Path(__file__).resolve().parent
SOURCE_SHA = "8a649adc50177e38ecb7c77b19c89066012c622b9b0b72732e81cf62a1db0af8"
RELEASE = "1358d026dd6fba3d4b1cdabb0b5f0e9154897fdc"
DATA = ROOT / "services/api/.local"
API_STOP = DATA / "services/api.stop"
ASK_STOP = DATA / "services/ask.stop"
ACTIVE_QUERIES = (
    "SELECT count(*) FROM task_runs WHERE status IN ('PENDING','RUNNING')",
    "SELECT count(*) FROM workflow_runs WHERE status IN ('PENDING','RUNNING')",
    "SELECT count(*) FROM work_queue_runs WHERE status IN ('PENDING','RUNNING','CLOSING')",
    "SELECT count(*) FROM repository_actions WHERE status='STARTED'",
    "SELECT count(*) FROM agent_evaluation_runs WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')",
    "SELECT count(*) FROM notification_deliveries WHERE status IN ('PENDING','SENDING')",
    "SELECT count(*) FROM identity_deliveries WHERE status IN ('pending','sending')",
)


def verify_source():
    if "app" in sys.modules:
        raise RuntimeError("A cold production process is required")
    manifest = HERE / "production-c85-sources.json"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError("C85 manifest differs")
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    sources = set((ROOT / "services/api/app").rglob("*.py"))
    sources.update((ROOT / "services/api/migrations").glob("*.sql"))
    sources.update((ROOT / "services/evaluation/src/evaluation_runner").rglob("*.py"))
    sources.update((ROOT / "services/evaluation/profile_support").glob("*.py"))
    sources.update((ROOT / "services/evaluation/datasets").glob("*.json"))
    sources.update((ROOT / "scripts").glob("*evaluation*.py"))
    sources.update((ROOT / "scripts").glob("*evaluation*.ps1"))
    sources.update(
        path
        for path in (
            ROOT / "services/evaluation/uv.lock",
            ROOT / "services/evaluation/pyproject.toml",
            ROOT / "services/api/uv.lock",
            ROOT / "services/api/pyproject.toml",
            ROOT / "services/evaluation/profile_support/suite-capabilities.json",
        )
        if path.is_file()
    )
    actual = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)
    }
    if len(expected) != 351 or actual != expected:
        raise ValueError("C85 source content or file set differs")
    sys.path.insert(0, str(ROOT / "services/api"))
    import app

    if Path(app.__file__).resolve() != (ROOT / "services/api/app/__init__.py").resolve():
        raise RuntimeError("Application import origin differs")
    return sorted(name.rsplit("/", 1)[-1] for name in expected if name.startswith("services/api/migrations/"))


def production_settings():
    versions = verify_source()
    from dotenv import dotenv_values
    from psycopg import connect
    from psycopg.conninfo import conninfo_to_dict

    safe = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
    }
    os.environ.clear()
    os.environ.update(safe)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
    values = dotenv_values(ROOT / "services/api/.env", encoding="utf-8", interpolate=False)
    url = values["DATABASE_URL"]
    parameters = conninfo_to_dict(url)
    if (
        parameters.get("dbname") != "owner_agent"
        or parameters.get("port") != "55432"
        or parameters.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or parameters.get("hostaddr", "127.0.0.1") not in {"127.0.0.1", "::1"}
    ):
        raise ValueError("C85 production database identity differs")
    data = Path(values.get("PLATFORM_DATA_DIR") or DATA)
    if not data.is_absolute():
        data = ROOT / "services/api" / data
    key = data / "model.key"
    if (
        data.resolve() != DATA.resolve()
        or not key.is_file()
        or any(path.is_symlink() or path.is_junction() for path in (key, data, *data.parents))
    ):
        raise ValueError("C85 production data/key identity differs")
    if API_STOP.exists() or ASK_STOP.exists():
        raise ValueError("Existing production stop marker must be resolved by the operator")
    with connect(url, connect_timeout=5) as db:
        db.execute("SET TRANSACTION READ ONLY")
        actual = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
        if len(versions) != 85 or actual != versions:
            raise ValueError("C85 requires exact schema85; no automatic migration or rollback")
        row = db.execute("SELECT active FROM platform_maintenance WHERE singleton").fetchone()
        if not row or row[0] is not False:
            raise ValueError("Maintenance prevents C85 startup")
        if any(db.execute(query).fetchone()[0] for query in ACTIVE_QUERIES):
            raise ValueError("Existing active effects prevent C85 startup")

    # Native Ask main re-reads this fixed environment; .env cannot add working flags or keys.
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1",
        LANGCHAIN_TRACING_V2="false",
        LANGSMITH_TRACING="false",
        DATABASE_URL=url,
        PLATFORM_DATA_DIR=str(DATA),
        WEB_ORIGIN="http://localhost:3100",
        PLATFORM_INTERNAL_API_URL="http://127.0.0.1:8101",
        RESTRICTED_EXECUTION="true",
        ASK_WORKER_ENABLED="true",
        WORK_ENABLED="false",
        AUTOMATION_ENABLED="false",
        EVALUATION_ENABLED="false",
        MEDIA_WORKER_ENABLED="false",
        PLATFORM_RELEASE=RELEASE,
    )
    from app.config import Settings

    settings = Settings.from_env()
    if (
        settings.database_url != url
        or settings.data_dir != DATA
        or settings.web_origin != "http://localhost:3100"
        or settings.internal_api_url != "http://127.0.0.1:8101"
        or (
            settings.restricted_execution,
            settings.ask_worker_enabled,
            settings.work_enabled,
            settings.automation_enabled,
            settings.evaluation_enabled,
            settings.media_worker_enabled,
        )
        != (True, True, False, False, False, False)
    ):
        raise ValueError("C85 explicit production settings differ")
    return settings


async def serve_api(settings):
    import uvicorn

    from app.main import create_app

    application = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=8101, access_log=False, timeout_graceful_shutdown=15)
    )

    async def watch():
        while True:
            fence = getattr(application.state, "service_fence", None)
            if API_STOP.exists() or fence and fence.lost.is_set():
                server.should_exit = True
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["check", "serve-api", "serve-ask"])
    args = parser.parse_args()
    settings = production_settings()
    if args.mode == "check":
        print("C85 source/schema/settings check only; no service started")
        return
    if args.mode == "serve-ask":
        from app.ask_server import main as serve_ask

        operation = serve_ask()  # Unchanged native entry reads the same explicit environment.
    else:
        operation = serve_api(settings)
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    ) as runner:
        runner.run(operation)


if __name__ == "__main__":
    main()
