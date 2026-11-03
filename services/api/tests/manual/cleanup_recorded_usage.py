"""Remove ledger rows for recorded browser fixtures whose models and runs are already deleted."""

import argparse
import json
from pathlib import Path
from uuid import UUID

import psycopg

from app.config import Settings


def cleanup(path: Path):
    expected = Path(__file__).parents[4] / "apps/web/test-results"
    if path.resolve().parent != expected.resolve() or path.name not in {
        "harness-test-ids.json", "team-test-ids.json", "workflow-studio-test-ids.json"
    }:
        raise ValueError("Only recorded model-call browser fixture files are supported")
    data = json.loads(path.read_text())
    models = data.get("models", [data.get("model")])
    runs = data.get("runs", [data.get("run")])
    models, runs = [str(UUID(value)) for value in models], [UUID(value) for value in runs]
    with psycopg.connect(Settings.from_env().database_url) as connection:
        assert connection.info.dbname == "owner_agent"
        if connection.execute("SELECT 1 FROM model_configs WHERE id=ANY(%s) LIMIT 1", (models,)).fetchone():
            raise ValueError("Remove test models through the authenticated API first")
        for table in ("task_runs", "work_queue_runs", "workflow_runs"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE id=ANY(%s) LIMIT 1", (runs,)).fetchone():
                raise ValueError("A recorded execution still exists; remove its owned source first")
        if connection.execute(
            "SELECT 1 FROM model_usage WHERE model_id=ANY(%s) AND NOT(run_id=ANY(%s)) LIMIT 1",
            (models, runs),
        ).fetchone():
            raise ValueError("Unexpected model use outside the recorded fixture roots")
        count = connection.execute(
            "DELETE FROM model_usage WHERE model_id=ANY(%s) AND run_id=ANY(%s)", (models, runs)
        ).rowcount
        print(json.dumps({"recorded_ledger_rows_removed": count}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    cleanup(parser.parse_args().metadata)
