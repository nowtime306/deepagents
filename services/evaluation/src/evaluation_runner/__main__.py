"""Command line entry point. Credentials are accepted through dedicated environment variables only."""

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from . import network
from .client import Platform
from .contracts import Dataset, Target
from .journal import Journal, atomic_json
from .reports import write_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preflight", "run", "report", "cleanup"])
    parser.add_argument("--target-manifest", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.action == "report":
        journal = Journal(output)
        try:
            result = write_report(output, journal.data)
        finally:
            journal.close()
        return 0 if result["gate"] == "PASS" else 1
    if not args.target_manifest:
        parser.error("--target-manifest is required")
    try:
        target = Target.model_validate_json(args.target_manifest.read_text(encoding="utf-8"))
        if target.fixture_profile.id != "controlled-v1" or target.fixture_profile.version != 1:
            raise ValueError("Unregistered target fixture profile")
        for key in ("DEEPEVAL_HOME", "DEEPEVAL_RESULTS_FOLDER", "DEEPEVAL_CACHE_FOLDER", "ENV_DIR_PATH"):
            private = output / key.lower()
            private.mkdir(exist_ok=True)
            os.environ[key] = str(private)
        network.install_guard(urlsplit(target.api_base_url).port)
        platform = Platform(target)
        try:
            platform.login()
            attestation = platform.attest()
            if args.action == "cleanup":
                from .adapters import cleanup
                journal = Journal(output)
                try:
                    if (journal.data.get("environment_id") != target.environment_id
                            or journal.data.get("owner_id") != target.owner_id):
                        raise ValueError("Cleanup environment mismatch")
                    for attempt in journal.data["attempts"]:
                        attempt["cleanup_status"] = cleanup(platform, journal, attempt)
                    journal.save()
                    result = write_report(output, journal.data)
                finally:
                    journal.close()
                return 0 if all(a.get("cleanup_status") == "CLEAN" for a in result["attempts"]) else 1
            if not args.dataset:
                parser.error("--dataset is required")
            dataset = Dataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
            if args.action == "preflight":
                atomic_json(output / "preflight.json", {"status": "ATTESTED", "attestation": attestation,
                                                       "case_count": len(dataset.cases), "ui_evaluated": False})
                return 0
            from .engine import run
            result = run(platform, target, dataset, output)
            return 130 if (output / "cancel.request").exists() else (0 if result["gate"] == "PASS" else 1)
        finally:
            platform.close()
    except ValueError:
        print("Evaluation contract/attestation is invalid; no credential details are printed")
        return 2
    except KeyboardInterrupt:
        (output / "cancel.request").touch()
        print("Cancellation recorded; inspect journal and reconcile any active/unknown run")
        return 130
    except Exception as error:  # noqa: BLE001 -- CLI boundary reports infrastructure failure without secret-bearing details.
        print("Evaluation infrastructure error: " + type(error).__name__)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
