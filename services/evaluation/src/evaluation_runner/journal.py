"""Local durable submission intents; an unknown POST is never automatically repeated."""

import hashlib
import json
import os
from pathlib import Path

import httpx
import portalocker


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Journal:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = directory / "runner.lock"
        self.handle = portalocker.Lock(self.lock, timeout=0)
        self.handle.acquire()
        self.path = directory / "journal.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"schema_version": 1, "attempts": []}
        except BaseException:
            self.close()
            raise

    def save(self) -> None:
        atomic_json(self.path, self.data)

    def close(self) -> None:
        self.handle.release()

    def post(self, client, attempt: dict, path: str, payload: dict, operation: str) -> dict:
        intents = attempt.setdefault("submission_intents", {})
        if operation in intents:
            previous = intents[operation]
            if previous["status"] == "CONFIRMED":
                return previous["response"]
            previous["status"] = "UNKNOWN"
            self.save()
            raise UnknownSubmission("Submission requires manual reconciliation")
        intents[operation] = {"status": "SUBMITTING", "path": path, "payload_digest": digest(payload)}
        self.save()
        try:
            response = client.post(path, json=payload)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError):
            # Even a response parse failure may follow a committed remote write.
            intents[operation]["status"] = "UNKNOWN"
            self.save()
            raise UnknownSubmission("Remote submission outcome is unknown") from None
        safe = {key: value[key] for key in ("id", "run_id", "status", "version", "revision", "created") if key in value}
        intents[operation].update(status="CONFIRMED", response=safe)
        self.save()
        return value


class UnknownSubmission(RuntimeError):
    pass
