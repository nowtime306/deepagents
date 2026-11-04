"""Fixed isolated-process entry. Stdlib verifies interpreter/install/source before runner imports."""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import UUID

LIMIT = 256 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normal(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_identity(repository):
    """Actual environment files, not lock-file existence or a reported package version alone."""
    prefix = Path(sys.prefix).resolve()
    lock = tomllib.loads((repository / "services/evaluation/uv.lock").read_text(encoding="utf-8"))
    locked = {normal(item["name"]): item["version"] for item in lock["package"]}
    versions, files = {}, {}
    for distribution in importlib.metadata.distributions():
        name, version = normal(distribution.metadata["Name"]), distribution.version
        if name in versions or locked.get(name) != version:
            raise ValueError("Installed dependency/version differs from the reviewed lock")
        versions[name] = version
        if not distribution.files:
            raise ValueError("Installed dependency has no verifiable file inventory")
        for relative in distribution.files:
            path = Path(distribution.locate_file(relative)).resolve()
            if not path.is_relative_to(prefix) or not path.is_file() or path.is_symlink():
                raise ValueError("Dependency file escaped its fixed virtual environment")
            files[path.relative_to(prefix).as_posix()] = file_hash(path)
    if not {"owner-agent-evaluation", "deepeval", "httpx", "pydantic", "portalocker"} <= versions.keys():
        raise ValueError("Required locked evaluator dependencies are absent")
    return {
        **interpreter_identity(),
        "installed_versions_digest": digest(versions),
        "installed_files_digest": digest(files),
    }


def interpreter_identity():
    return {
        "python_version": sys.version.split()[0],
        "python_sha256": file_hash(Path(sys.executable).resolve()),
        "base_python_sha256": file_hash(Path(sys._base_executable).resolve()),
        "pyvenv_sha256": file_hash(Path(sys.prefix).resolve() / "pyvenv.cfg"),
    }


def source_identity(request):
    root = Path(request["repository"])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("Fixed absolute checkout required")
    pins = request["source_pins"]
    for relative, expected in pins.items():
        parts = Path(relative).parts
        path = root / relative
        if Path(relative).is_absolute() or ".." in parts or path.is_symlink() or file_hash(path) != expected:
            raise ValueError("Reviewed source differs before process dispatch")
    helper = root / "scripts/evaluation_sources.py"
    if file_hash(helper) != pins.get("scripts/evaluation_sources.py"):
        raise ValueError("Source inventory helper is not pinned")
    spec = importlib.util.spec_from_file_location("fixed_runtime_source_inventory", helper)
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    if inventory.source_fingerprint(root) != pins:
        raise ValueError("Source file inventory changed")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, timeout=5, text=True).strip()
    if head != request["candidate_commit"]:
        raise ValueError("Actual checkout is not the fixed candidate")
    # find_spec of the top-level package does not import its __init__ or runner code.
    package = importlib.util.find_spec("evaluation_runner")
    expected = root / "services/evaluation/src/evaluation_runner"
    if (
        package is None
        or package.origin is None
        or Path(package.origin).resolve() != expected / "__init__.py"
        or list(package.submodule_search_locations or []) != [str(expected)]
    ):
        raise ValueError("Isolated interpreter resolves another evaluator installation")
    actual = {path.relative_to(root).as_posix(): file_hash(path) for path in expected.rglob("*.py")}
    desired = {
        name: value
        for name, value in pins.items()
        if name.startswith("services/evaluation/src/evaluation_runner/") and name.endswith(".py")
    }
    if actual != desired:
        raise ValueError("Actual runner modules differ from reviewed package bytes")
    return root, {
        "candidate_commit": head,
        "source_digest": digest(pins),
        "runner_lock_digest": file_hash(root / "services/evaluation/uv.lock"),
        "package_origin": str(expected / "__init__.py"),
        "module_digest": digest(actual),
    }


def attest(request):
    root, source = source_identity(request)
    if request["action"] != "inspect":
        expected = request["runtime_pin"]
        if not isinstance(expected, dict) or any(
            expected.get(key) != value for key, value in interpreter_identity().items()
        ):
            raise ValueError("Actual isolated interpreter differs from the trusted pin")
    actual = runtime_identity(root)
    expected = request["runtime_pin"]
    if request["action"] != "inspect" and (not isinstance(expected, dict) or actual != expected):
        raise ValueError("Actual interpreter/dependency files do not match the trusted runtime pin")
    # Only verified checkout runner code is imported; this remains in the isolated eval process.
    from evaluation_runner import fixed_run

    dataset = fixed_run.catalog()
    rules = [
        {
            "case_id": case.case_id,
            "validators": [value.model_dump(mode="json") for value in case.validators],
            "metrics": [value.model_dump(mode="json") for value in case.metrics],
            "approval_policy": case.approval_policy,
        }
        for case in dataset.cases
    ]
    proof = {
        "schema_version": 1,
        "nonce": request["nonce"],
        "runtime": actual,
        **source,
        "rules_id": fixed_run.RULES_ID,
        "rules_digest": digest(rules),
    }
    if request["action"] != "inspect":
        version = request["version_set"]
        if (
            set(version)
            != {
                "candidate_ref",
                "platform_commit",
                "source_digest",
                "runner_lock_digest",
                "rules_id",
                "rules_digest",
                "business_refs",
                "model_refs",
                "judge_ref",
            }
            or any(
                version[key] != proof[name]
                for key, name in (
                    ("platform_commit", "candidate_commit"),
                    ("source_digest", "source_digest"),
                    ("runner_lock_digest", "runner_lock_digest"),
                    ("rules_id", "rules_id"),
                    ("rules_digest", "rules_digest"),
                )
            )
            or version["business_refs"] != []
            or version["model_refs"] != []
            or version["judge_ref"] is not None
        ):
            raise ValueError("Registered fixed engineering version set differs from actual source/rules/runtime")
        UUID(version["candidate_ref"])
    return root, proof


def main():
    raw = sys.stdin.buffer.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Oversized fixed process envelope")
    request = json.loads(raw.decode("utf-8"))
    if (
        set(request)
        != {
            "schema_version",
            "repository",
            "candidate_commit",
            "source_pins",
            "runtime_pin",
            "version_set",
            "nonce",
            "action",
            "argument",
        }
        or type(request["schema_version"]) is not int
        or request["schema_version"] != 1
        or request["action"] not in {"inspect", "attest", "describe", "execute"}
    ):
        raise ValueError("Invalid fixed process envelope")
    UUID(request["nonce"])
    if request["action"] != "execute":

        def deny_network(event, arguments):
            if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.listen"}:
                raise RuntimeError("Readonly runtime attestation cannot use the network")

        sys.addaudithook(deny_network)
    root, proof = attest(request)
    if request["action"] in {"inspect", "attest"}:
        print(json.dumps(proof, ensure_ascii=False))
        return 0
    if request["action"] == "describe":
        selected = request["argument"]
        if (
            not isinstance(selected, list)
            or not 1 <= len(selected) <= 20
            or len(set(selected)) != len(selected)
            or any(not isinstance(case, str) or not re.fullmatch(r"AET-[AWF][0-9]{2}", case) for case in selected)
        ):
            raise ValueError("Only fixed case descriptors are allowed")
        from evaluation_runner import fixed_run

        registered = fixed_run.catalog()
        specimens = {case.case_id: case for case in registered.cases}
        print(
            json.dumps(
                {
                    "attestation": proof,
                    "descriptors": [fixed_run.describe(specimens[case], registered) for case in selected],
                }
            )
        )
    else:
        identifier = str(UUID(request["argument"]))
        directory = root / ".local/agent-evaluations/platform-worker" / identifier
        if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
            raise ValueError("Fixed private job directory required")
        target = directory / "runtime-attestation.json"
        with target.open("x", encoding="utf-8") as stream:
            json.dump(proof, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        sys.argv = ["evaluation_runner.platform_driver", "--job-id", identifier]
        runpy.run_module("evaluation_runner.platform_driver", run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 -- Redact local paths and credentials at the process boundary.
        # No exception values/paths/credentials are emitted to a public client.
        print(json.dumps({"status": "BLOCKED", "code": type(error).__name__}), file=sys.stderr)
        raise SystemExit(3) from None
