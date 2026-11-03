"""Compare original rows and vault bytes without printing user content or credentials."""

import argparse
import hashlib
import json
from pathlib import Path

import psycopg

from app.config import Settings

TABLES = ("users", "model_configs", "conversations", "conversation_messages", "task_runs", "model_usage")


def snapshot():
    settings = Settings.from_env()
    result = {}
    with psycopg.connect(settings.database_url) as connection:
        assert connection.info.dbname == "owner_agent", "Preservation check requires the local production database"
        for table in TABLES:
            # Names are fixed constants; contents stay in digests, never console output.
            key = "call_id" if table == "model_usage" else "id"
            rows = connection.execute(f"SELECT {key},row_to_json(t) FROM {table} t ORDER BY {key}").fetchall()
            if table == "task_runs":
                for _, row in rows:
                    row.pop("context_import", None)
            if table == "model_usage":
                for _, row in rows:
                    # New nullable routing metadata does not change original usage values.
                    row.pop("route_selection_id", None)
                    for key in ("price_version_id", "cost_status", "estimate_method"):
                        row.pop(key, None)
            result[table] = {
                str(identifier): hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()
                for identifier, row in rows
            }
    result["vault"] = hashlib.sha256((settings.data_dir / "model.key").read_bytes()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["snapshot", "check"])
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    current = snapshot()
    if args.command == "snapshot":
        with args.file.open("x", encoding="utf-8") as output:
            json.dump(current, output)
        print(json.dumps({"snapshot_saved": True, "counts": {key: len(current[key]) for key in TABLES}}))
    else:
        before = json.loads(args.file.read_text(encoding="utf-8"))
        results = {key: current[key] == before[key] for key in (*TABLES, "vault")}
        print(json.dumps(results))
        if not all(results.values()):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
