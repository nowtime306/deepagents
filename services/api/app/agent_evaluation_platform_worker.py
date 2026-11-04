"""Persistent, fenced evaluation consumer and loopback per-effect permission delegate.

Not installed by import. A reviewed driver plus trusted publication must be registered explicitly.
No DeepEval dependency or target provisioning code is imported into the API environment.
"""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from subprocess import Popen
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.agent_evaluation_platform_admission import Admission, checked_report, validate_version_snapshot
from app.agent_evaluation_platform_queue import Queue, authorize
from app.agent_evaluation_platform_types import Definition, digest
from app.errors import APIError

OS_ENV = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
FILE_LIMIT = 8 * 1024 * 1024


def read_json(path, maximum=FILE_LIMIT):
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Bounded worker artifact exceeded")
    value = json.loads(
        data.decode("utf-8"),
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
    )
    json.dumps(value, allow_nan=False)
    return value


def authenticated_result(directory, secret, run, owner):
    receipt = read_json(directory / "result-ready.json", 2048)
    path = directory / "result.json"
    if path.is_symlink() or path.is_junction():
        raise ValueError("Linked process result")
    with path.open("rb") as stream:
        raw = stream.read(FILE_LIMIT + 1)
    if (
        type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
        or set(receipt) != {"schema_version", "run_id", "owner_id", "sha256", "mac"}
        or len(raw) > FILE_LIMIT
        or receipt.get("run_id") != str(run)
        or receipt.get("owner_id") != str(owner)
        or receipt.get("sha256") != hashlib.sha256(raw).hexdigest()
        or not hmac.compare_digest(
            str(receipt.get("mac", "")), hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        )
    ):
        raise ValueError("Unauthenticated or changed reader result")
    result = json.loads(raw.decode("utf-8"))
    json.dumps(result, allow_nan=False)
    return result


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def private_directory(path):
    for ancestor in (path, *path.parents):
        if ancestor.exists() and (ancestor.is_symlink() or ancestor.is_junction()):
            raise ValueError("Worker paths must not contain links")
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if os.name == "nt":
        import re
        import subprocess

        identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], timeout=5).decode(errors="replace")
        sid = re.search(r"S-1-[0-9-]+", identity)
        if sid is None:
            raise RuntimeError("Private worker SID unavailable")
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{sid[0]}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Worker private ACL unavailable")


def open_logs(directory):
    first = (directory / "stdout.bin").open("xb")
    try:
        return [first, (directory / "stderr.bin").open("xb")]
    except Exception:
        first.close()
        raise


class PermitServer:
    """Private one-run delegate. A missing parent, expired lease or revoked owner denies the next effect."""

    def __init__(self, validator):
        self.validator = validator
        self.secret = secrets.token_urlsafe(32)
        self.server = None
        self.origin = None
        self.bindings = {}
        self.lock = asyncio.Lock()
        self.requests = 0

    async def permit(self, secret, payload):
        if not isinstance(secret, str) or not hmac.compare_digest(secret, self.secret):
            return False
        if (
            not isinstance(payload, dict)
            or set(payload) != {"environment_id", "case_id", "mode", "boundary"}
            or payload["mode"] not in {"ask", "work", "workflow", "resource"}
            or payload["boundary"] not in {"provider", "tool", "sandbox", "submission"}
        ):
            return False
        try:
            environment = str(UUID(payload["environment_id"]))
        except (TypeError, ValueError, AttributeError):
            return False
        async with self.lock:
            case = self.bindings.get(environment)
            if case is not None and case != payload["case_id"]:
                return False
            if await self.validator(payload) is not True:
                return False
            self.bindings[environment] = payload["case_id"]
            self.requests += 1
            return True

    async def _handle(self, reader, writer):
        allowed = False
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            lines = header.decode("ascii").split("\r\n")
            if lines[0] != "POST /permit HTTP/1.1":
                raise ValueError("Invalid permit method")
            pairs = [line.split(":", 1) for line in lines[1:] if line]
            headers = {key.lower().strip(): value.strip() for key, value in pairs}
            if len(headers) != len(pairs) or "transfer-encoding" in headers:
                raise ValueError("Invalid permit framing")
            length = int(headers["content-length"])
            if not 0 < length <= 2048:
                raise ValueError("Invalid permit bound")
            raw = await asyncio.wait_for(reader.readexactly(length), 3)
            authorization = headers.get("authorization", "")
            allowed = await self.permit(
                authorization[7:] if authorization.startswith("Bearer ") else "",
                json.loads(raw),
            )
        except (
            ValueError,
            KeyError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
        ):
            allowed = False
        except Exception:  # noqa: BLE001 -- Database, lease and permission faults deny without exposing errors.
            allowed = False
        try:
            code = "200 OK" if allowed else "403 Forbidden"
            body = b'{"allowed":true}' if allowed else b'{"allowed":false}'
            writer.write(
                (
                    f"HTTP/1.1 {code}\r\nConnection: close\r\n"
                    f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=8192)
        self.origin = "http://127.0.0.1:" + str(self.server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *_):
        self.server.close()
        await self.server.wait_closed()
        self.secret = secrets.token_urlsafe(32)


@dataclass(frozen=True)
class DriverConfig:
    repository: Path
    python: Path
    candidate_commit: str
    pins: dict[str, str]
    admin_connection_file: Path
    manager_key_file: Path | None = None
    # Receipt is an independently reviewed *source wiring* manifest, not a client admission assertion.
    bridge_receipt: Path | None = None
    bridge_receipt_sha256: str | None = None
    runtime_pin: dict[str, str] | None = None


class FixedSuiteDriver:
    """The subprocess actually runs the registered suite; unsupported definitions never dispatch."""

    def __init__(self, config, version_set):
        self.config = config
        self.version_set = version_set
        self.prepared = None
        self.consumer = None
        self.consumer_stop = None
        self.runtime_home = None
        self.attested = None

    def verify_source(self):
        import subprocess

        config = self.config
        if not config.pins or config.repository.resolve() != config.repository:
            raise ValueError("A pinned absolute checkout is required")
        from importlib.util import module_from_spec, spec_from_file_location

        helper = config.repository / "scripts/evaluation_sources.py"
        if hashlib.sha256(helper.read_bytes()).hexdigest() != config.pins.get("scripts/evaluation_sources.py"):
            raise ValueError("Untrusted inventory helper")
        spec = spec_from_file_location("reviewed_platform_source_inventory", helper)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.source_fingerprint(config.repository) != config.pins:
            raise ValueError("Reviewed source inventory or file set changed")
        for relative, expected in config.pins.items():
            path = config.repository / relative
            if (
                Path(relative).is_absolute()
                or ".." in Path(relative).parts
                or path.is_symlink()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                raise ValueError("Reviewed driver source changed")
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=config.repository,
            timeout=5,
            text=True,
            encoding="utf-8",
        ).strip()
        if head != config.candidate_commit or not config.python.is_file() or not config.admin_connection_file.is_file():
            raise ValueError("Trusted driver installation unavailable")
        pin = config.runtime_pin
        if (
            not isinstance(pin, dict)
            or set(pin)
            != {
                "python_version",
                "python_sha256",
                "base_python_sha256",
                "pyvenv_sha256",
                "installed_versions_digest",
                "installed_files_digest",
            }
            or not pin["python_version"].startswith("3.12.")
            or hashlib.sha256(config.python.read_bytes()).hexdigest() != pin["python_sha256"]
            or hashlib.sha256((config.python.parent.parent / "pyvenv.cfg").read_bytes()).hexdigest()
            != pin["pyvenv_sha256"]
        ):
            raise ValueError("Explicit reviewed interpreter/runtime pin is missing or changed")
        return True

    def runtime_envelope(self, action, argument=None):
        return {
            "schema_version": 1,
            "repository": str(self.config.repository),
            "candidate_commit": self.config.candidate_commit,
            "source_pins": self.config.pins,
            "runtime_pin": self.config.runtime_pin,
            "version_set": self.version_set,
            "nonce": str(uuid4()),
            "action": action,
            "argument": argument,
        }

    def process_command(self):
        helper = self.config.repository / "scripts/evaluation-runtime.py"
        if hashlib.sha256(helper.read_bytes()).hexdigest() != self.config.pins.get("scripts/evaluation-runtime.py"):
            raise ValueError("Fixed process entry is not reviewed source")
        return [str(self.config.python), "-I", str(helper)]

    def runtime_environment(self, home):
        env = {key: value for key, value in os.environ.items() if key.upper() in OS_ENV}
        env.update(
            {
                key: str(home)
                for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "DEEPEVAL_HOME", "ENV_DIR_PATH")
            }
        )
        env.update(PYTHON_DOTENV_DISABLED="1", DEEPEVAL_TELEMETRY_OPT_OUT="1", DEEPEVAL_DISABLE_DOTENV="1")
        return env

    def verify_runtime(self):
        import subprocess

        if self.runtime_home is None:
            raise ValueError("Explicit runtime preparation is required")
        envelope = self.runtime_envelope("attest")
        result = subprocess.run(
            self.process_command(),
            input=json.dumps(envelope).encode(),
            env=self.runtime_environment(self.runtime_home),
            cwd=self.config.repository,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError("Actual isolated runtime/source attestation failed")
        actual = json.loads(result.stdout.decode("utf-8"))
        self.validate_runtime_proof(actual, envelope["nonce"])
        self.attested = actual
        return True

    def validate_runtime_proof(self, actual, nonce):
        validate_version_snapshot(self.version_set)
        if (
            self.version_set["business_refs"] != []
            or self.version_set["model_refs"] != []
            or self.version_set["judge_ref"] is not None
        ):
            raise ValueError("Fixed engineering cannot claim unobserved business/model/judge identities")
        modules = {
            name: value
            for name, value in self.config.pins.items()
            if name.startswith("services/evaluation/src/evaluation_runner/") and name.endswith(".py")
        }
        if (
            set(actual)
            != {
                "schema_version",
                "nonce",
                "runtime",
                "candidate_commit",
                "source_digest",
                "runner_lock_digest",
                "package_origin",
                "module_digest",
                "rules_id",
                "rules_digest",
            }
            or actual.get("module_digest") != digest(modules)
            or type(actual.get("schema_version")) is not int
            or actual["schema_version"] != 1
            or actual.get("nonce") != nonce
            or actual.get("runtime") != self.config.runtime_pin
            or actual.get("candidate_commit") != self.config.candidate_commit
            or actual.get("source_digest") != digest(self.config.pins)
            or actual.get("runner_lock_digest") != self.config.pins.get("services/evaluation/uv.lock")
            or actual.get("package_origin")
            != str(self.config.repository / "services/evaluation/src/evaluation_runner/__init__.py")
            or any(
                self.version_set.get(key) != actual.get(name)
                for key, name in (
                    ("platform_commit", "candidate_commit"),
                    ("source_digest", "source_digest"),
                    ("runner_lock_digest", "runner_lock_digest"),
                    ("rules_id", "rules_id"),
                    ("rules_digest", "rules_digest"),
                )
            )
        ):
            raise ValueError("Actual isolated runtime differs from registered version set")
        return True

    def prepare(self):
        """Read-only real evaluator CLI descriptors; called explicitly before registering a driver, never by GET."""
        import subprocess

        self.verify_source()
        home = self.config.repository / ".local/agent-evaluations/platform-worker-preflight" / str(uuid4())
        private_directory(home)
        self.runtime_home = home
        env = self.runtime_environment(home)
        catalog = read_json(self.config.repository / "services/evaluation/datasets/a20-fixed.json")
        capabilities = read_json(self.config.repository / "services/evaluation/profile_support/suite-capabilities.json")
        raw = {row["case_id"]: row for row in catalog["cases"]}
        prepared = {}
        envelope = self.runtime_envelope("describe", list(capabilities["sandbox_bounds"]))
        result = subprocess.run(
            self.process_command(),
            input=json.dumps(envelope).encode(),
            env=env,
            cwd=self.config.repository,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError("Actual evaluator descriptor unavailable")
        payload = json.loads(result.stdout.decode("utf-8"))
        self.validate_runtime_proof(payload["attestation"], envelope["nonce"])
        descriptors = {row["case_id"]: row for row in payload["descriptors"]}
        if len(descriptors) != len(payload["descriptors"]) or set(descriptors) != set(capabilities["sandbox_bounds"]):
            raise ValueError("Fixed descriptors are missing or repeated")
        for case_id, bound in capabilities["sandbox_bounds"].items():
            descriptor = descriptors[case_id]
            declared = Definition.model_validate({**catalog, "cases": [raw[case_id]]}).cases[0].model_dump(mode="json")
            if (
                descriptor.get("implemented") is not True
                or descriptor.get("case_id") != case_id
                or descriptor.get("case_definition_digest") != digest(declared)
                or type(descriptor.get("maximum_sandboxes")) is not int
                or descriptor["maximum_sandboxes"] != bound
            ):
                raise ValueError("Actual registered descriptor does not match the fixed declaration")
            prepared[case_id] = {"definition": declared, "descriptor": descriptor}
        self.verify_source()
        self.attested = payload["attestation"]
        self.prepared = prepared
        return True

    def bridge_ready(self):
        config = self.config
        if config.bridge_receipt is None or config.bridge_receipt_sha256 is None:
            return False
        raw = config.bridge_receipt.read_bytes()
        if hashlib.sha256(raw).hexdigest() != config.bridge_receipt_sha256:
            return False
        value = json.loads(raw)
        return (
            value.get("kind") == "platform-effect-bridge-reviewed-v1"
            and value.get("independent_review") == "CLOSED"
            and value.get("gate") == "PASS"
            and value.get("candidate_commit") == config.candidate_commit
            and value.get("source_pins") == config.pins
            and all(
                value.get(key) is True
                for key in (
                    "provider",
                    "tool",
                    "sandbox",
                    "submission",
                    "env_forwarding",
                )
            )
        )

    def readiness(self, owner):
        try:
            source = self.verify_source()
            source = (
                source
                and self.attested is not None
                and self.validate_runtime_proof(self.attested, self.attested["nonce"])
            )
            bridge = self.bridge_ready()
        except (OSError, ValueError):
            source = bridge = False
        return {
            "ready": source
            and bridge
            and self.prepared is not None
            and self.consumer is not None
            and not self.consumer.done()
            and not self.consumer_stop.is_set(),
            "source_verified": source,
            "lease_enforced": True,
            "owner_permissions_per_effect": bridge,
            "cooperative_cancel": True,
            "ready_reader": source,
            "precise_cleanup": source,
        }

    def candidates(self, owner):
        return (
            [
                {
                    "id": self.version_set["candidate_ref"],
                    "platform_commit": self.config.candidate_commit,
                }
            ]
            if self.readiness(owner)["ready"]
            else []
        )

    def default_version_set(self, owner):
        return self.version_set if self.readiness(owner)["ready"] else None

    def validate_definition(self, owner, definition, repeats):
        self.verify_source()
        if self.attested is None or not self.validate_runtime_proof(self.attested, self.attested["nonce"]):
            raise ValueError("Actual runtime has not been prepared")
        if self.prepared is None:
            raise ValueError("Actual evaluator descriptors have not been prepared")
        value = Definition.model_validate(definition)
        if repeats != 1:
            raise ValueError("Only exact registered engineering subsets are implemented")
        for case in value.cases:
            observed = self.prepared.get(case.case_id)
            if observed is None or digest(observed["definition"]) != digest(case.model_dump(mode="json")):
                raise ValueError("Changed or unsupported fixed case definition")
        if any(case.mode == "work" for case in value.cases) and self.config.manager_key_file is None:
            raise ValueError("A reviewed explicit manager key handle is required for Work")
        return True

    async def execute(self, row, permit, cancelled):
        config = self.config
        if self.validate_definition(row["owner_id"], row["snapshot"]["definition"], row["repeats"]) is not True:
            raise ValueError("Fixed definition validation did not succeed")
        if digest(row["snapshot"]["version_set"]) != digest(self.version_set):
            raise ValueError("Dispatch cannot substitute the registered version set")
        self.verify_source()
        identifier = UUID(str(row["id"]))
        directory = config.repository / ".local/agent-evaluations/platform-worker" / str(identifier)
        private_directory(directory)
        request = {
            "schema_version": 1,
            "run_id": str(identifier),
            "owner_id": str(row["owner_id"]),
            "candidate_commit": config.candidate_commit,
            "definition": row["snapshot"]["definition"],
            "budgets": row["budgets"],
            "repeats": row["repeats"],
            "version_set": row["snapshot"]["version_set"],
        }
        atomic(directory / "request.json", request)
        env = {key: value for key, value in os.environ.items() if key.upper() in OS_ENV}
        for key in (
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "DEEPEVAL_HOME",
            "ENV_DIR_PATH",
        ):
            env[key] = str(directory)
        result_secret = secrets.token_urlsafe(32)
        env.update(
            EVALUATION_PLATFORM_RESULT_SECRET=result_secret,
            PYTHON_DOTENV_DISABLED="1",
            DEEPEVAL_TELEMETRY_OPT_OUT="1",
            DEEPEVAL_DISABLE_DOTENV="1",
            EVALUATION_PLATFORM_ADMIN_CONNECTION_FILE=str(config.admin_connection_file),
            EVALUATION_PLATFORM_PERMIT_ORIGIN=permit.origin,
            EVALUATION_PLATFORM_PERMIT_SECRET=permit.secret,
        )
        if config.manager_key_file:
            env["EVALUATION_PLATFORM_MANAGER_KEY_FILE"] = str(config.manager_key_file)
        logs = await asyncio.to_thread(open_logs, directory)
        process = None
        started = asyncio.get_running_loop().time()
        try:
            envelope = self.runtime_envelope("execute", str(identifier))
            # A regular file cannot block on a child that does not consume stdin.
            atomic(directory / "runtime-envelope.json", envelope)
            atomic(directory / "process.json", {"run_id": str(identifier), "status": "LAUNCHING"})
            interrupted = False
            with (directory / "runtime-envelope.json").open("rb") as input_file:
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evaluation-launch")
                try:
                    launching = executor.submit(
                        Popen,
                        self.process_command(),
                        cwd=config.repository,
                        env=env,
                        stdout=logs[0],
                        stderr=logs[1],
                        stdin=input_file,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    # asyncio shutdown cannot cancel this native Future or discard its handle.
                    while not launching.done():
                        try:
                            await asyncio.sleep(0.05)
                        except asyncio.CancelledError:
                            interrupted = True
                    process = launching.result()
                finally:
                    executor.shutdown(wait=False)
            atomic(
                directory / "process.json",
                {"run_id": str(identifier), "pid": process.pid, "status": "STARTED"},
            )
            if interrupted:
                raise asyncio.CancelledError
            while process.poll() is None:
                elapsed = asyncio.get_running_loop().time() - started
                if elapsed > row["budgets"]["max_seconds"] + 180:
                    raise TimeoutError("Cooperative process teardown exceeded its bound")
                if elapsed > row["budgets"]["max_seconds"] or await cancelled():
                    (directory / "suite").mkdir(exist_ok=True)
                    (directory / "suite/cancel.request").touch()
                await asyncio.sleep(1)
            result = await asyncio.to_thread(
                authenticated_result, directory, result_secret, identifier, row["owner_id"]
            )
            with (directory / "runtime-attestation.json").open("rb") as stream:
                attestation_bytes = stream.read(65537)
            if (
                len(attestation_bytes) > 65536
                or result.get("runtime_attestation_sha256") != hashlib.sha256(attestation_bytes).hexdigest()
            ):
                raise ValueError("Authenticated result is not bound to this process attestation")
            self.validate_runtime_proof(json.loads(attestation_bytes.decode("utf-8")), envelope["nonce"])
            if result.get("run_id") != str(identifier) or result.get("owner_id") != str(row["owner_id"]):
                raise ValueError("Driver returned a foreign result")
            if process.returncode not in {0, 1}:
                if result.get("verified") is not True or not any(
                    sample.get("task_outcome") == "FAIL" for sample in result.get("samples", [])
                ):
                    raise ValueError("Driver exit has no verified factual failure to preserve")
                checked_report(result["report"], identifier, row["owner_id"])
                result["infrastructure_error"] = "PROCESS_EXIT_" + str(process.returncode)
            self.verify_source()
            return result
        finally:
            if process is not None and process.poll() is None:
                (directory / "suite").mkdir(exist_ok=True)
                (directory / "suite/cancel.request").touch()
                # Do not kill an uncertain child or claim its resources are released.
                atomic(
                    directory / "retained-process.json",
                    {
                        "run_id": str(identifier),
                        "pid": process.pid,
                        "status": "RETAINED",
                    },
                )
            for log in logs:
                log.close()


class Worker:
    """Explicitly-started consumer: a dispatched lease is never automatically replayed."""

    def __init__(self, database, admission, *, maintenance):
        self.db, self.admission, self.maintenance = database, admission, maintenance
        self.queue = Queue(database)
        self.identifier = uuid4()
        self.stop = None

    async def permitted(self, row, payload):
        if (
            self.stop is not None
            and self.stop.is_set()
            or not await self.maintenance()
            or self.admission.inspect(row["owner_id"])["status"] != "READY"
        ):
            return False
        cases = {case["case_id"]: case["mode"] for case in row["snapshot"]["definition"]["cases"]}
        if payload["case_id"] not in cases or payload["mode"] not in {
            cases[payload["case_id"]],
            "resource",
        }:
            return False
        query = (
            "SELECT id FROM agent_evaluation_runs WHERE id=%s AND owner_id=%s AND "
            "lease_token=%s AND lease_until>now() AND status='RUNNING' AND NOT cancel_requested"
        )
        params = (row["id"], row["owner_id"], row["lease_token"])
        if not await self.db.fetch(query, params):
            return False
        try:
            await authorize(self.db, row["owner_id"], row["snapshot"]["definition"])
        except APIError:
            return False
        if self.stop is not None and self.stop.is_set() or not await self.maintenance():
            return False
        # Authorization awaits may race a cancel/expiry; close that window before granting the effect.
        return bool(await self.db.fetch(query, params))

    async def retain(self, row, code):
        await self.db.execute(
            "UPDATE agent_evaluation_runs SET status='NEEDS_REVIEW',gate_verdict=CASE WHEN "
            "gate_verdict='FAIL' THEN 'FAIL' ELSE 'NEEDS_REVIEW' END,cleanup_status='RETAINED',"
            "errors=errors || %s,revision=revision+1,finished_at=now() WHERE id=%s AND owner_id=%s AND lease_token=%s",
            (
                Jsonb(
                    [
                        {
                            "code": code,
                            "message": "执行证据或租约尚未闭合，保留现场，不自动重试",
                        }
                    ]
                ),
                row["id"],
                row["owner_id"],
                row["lease_token"],
            ),
        )

    async def commit(self, row, result):
        if (
            type(result.get("schema_version")) is not int
            or result["schema_version"] != 1
            or result.get("verified") is not True
        ):
            raise ValueError("Unverified process receipt")
        samples = result["samples"]
        if (
            not isinstance(samples, list)
            or len(samples) > 300
            or any(not isinstance(sample, dict) or type(sample.get("repeat_index")) is not int for sample in samples)
        ):
            raise ValueError("Invalid typed sample set")
        expected = {
            (case["case_id"], repeat)
            for case in row["snapshot"]["definition"]["cases"]
            for repeat in range(1, row["repeats"] + 1)
        }
        if (
            len(samples) != len(expected)
            or {(sample["case_id"], sample["repeat_index"]) for sample in samples} != expected
        ):
            raise ValueError("Empty, repeated or foreign sample set")
        checked_report(result["report"], row["id"], row["owner_id"])
        counts = {"pass": 0, "fail": 0, "needs_review": 0, "not_run": 0}
        for sample in samples:
            if sample["task_outcome"] not in {
                "PASS",
                "FAIL",
                "NEEDS_REVIEW",
                "NOT_RUN",
            }:
                raise ValueError("Invalid task fact")
            counts[
                {
                    "PASS": "pass",
                    "FAIL": "fail",
                    "NEEDS_REVIEW": "needs_review",
                    "NOT_RUN": "not_run",
                }[sample["task_outcome"]]
            ] += 1
        if any(sample["task_outcome"] == "PASS" and not sample["facts"] for sample in samples):
            raise ValueError("An empty fact set cannot pass")
        verdicts = [result.get("gate_verdict"), *(sample.get("gate_verdict") for sample in samples)]
        if any(value not in {"PASS", "FAIL", "NEEDS_REVIEW"} for value in verdicts):
            raise ValueError("Invalid or missing result gate")
        gate = (
            "FAIL"
            if counts["fail"]
            or "FAIL" in verdicts
            or any(fact.get("status") == "FAIL" for sample in samples for fact in sample["facts"])
            else "NEEDS_REVIEW"
            if counts["needs_review"]
            or counts["not_run"]
            or result["cleanup_status"] != "CLEAN"
            or "NEEDS_REVIEW" in verdicts
            or any(sample["cleanup_status"] != "CLEAN" for sample in samples)
            else "PASS"
        )
        report_json = json.loads(result["report"]["files"]["json"])
        if (
            not isinstance(report_json, dict)
            or report_json.get("gate_verdict") != gate
            or report_json.get("samples") != samples
            or result["gate_verdict"] != gate
        ):
            raise ValueError("Report, process and imported gates disagree")
        async with self.db.pool.connection() as conn:
            fence = await (
                await conn.execute(
                    "SELECT id FROM agent_evaluation_runs WHERE id=%s AND owner_id=%s AND "
                    "lease_token=%s AND lease_until>now() AND status IN ('RUNNING','CANCEL_REQUESTED') FOR UPDATE",
                    (row["id"], row["owner_id"], row["lease_token"]),
                )
            ).fetchone()
            if not fence:
                raise ValueError("Result import lost its lease")
            for sample in samples:
                await conn.execute(
                    "UPDATE agent_evaluation_samples SET status='COMPLETED',task_outcome=%s,metric_status=%s,"
                    "gate_verdict=%s,cleanup_status=%s,facts=%s,metrics=%s,evidence_refs=%s,root_refs=%s,usage=%s,"
                    "revision=revision+1,finished_at=now() WHERE run_id=%s AND owner_id=%s "
                    "AND case_id=%s AND repeat_index=%s",
                    (
                        sample["task_outcome"],
                        sample["metric_status"],
                        sample["gate_verdict"],
                        sample["cleanup_status"],
                        Jsonb(sample["facts"]),
                        Jsonb(sample["metrics"]),
                        Jsonb(sample["evidence_refs"]),
                        Jsonb(sample["root_refs"]),
                        Jsonb(sample["usage"]),
                        row["id"],
                        row["owner_id"],
                        sample["case_id"],
                        sample["repeat_index"],
                    ),
                )
            report = result["report"]
            await conn.execute(
                "INSERT INTO agent_evaluation_reports(id,owner_id,run_id,manifest,files,verified,redacted) "
                "VALUES(%s,%s,%s,%s,%s,true,true)",
                (
                    uuid4(),
                    row["owner_id"],
                    row["id"],
                    Jsonb(report["manifest"]),
                    Jsonb(report["files"]),
                ),
            )
            await conn.execute(
                "UPDATE agent_evaluation_runs SET status='COMPLETED',gate_verdict=%s,completed_samples=%s,"
                "counts=%s,cleanup_status=%s,revision=revision+1,finished_at=now(),"
                "lease_token=NULL,lease_until=NULL,worker_id=NULL "
                "WHERE id=%s AND owner_id=%s",
                (
                    gate,
                    len(samples),
                    Jsonb(counts),
                    result["cleanup_status"],
                    row["id"],
                    row["owner_id"],
                ),
            )
            await conn.execute(
                "INSERT INTO agent_evaluation_events(owner_id,run_id,event,data) VALUES(%s,%s,'result.imported',%s)",
                (
                    row["owner_id"],
                    row["id"],
                    Jsonb(
                        {
                            "gate_verdict": gate,
                            "samples": len(samples),
                            "result_digest": digest(result),
                        }
                    ),
                ),
            )

    async def once(self):
        if not isinstance(self.admission, Admission) or not await self.maintenance():
            return False
        if self.admission.publication is None or self.admission.driver is None:
            return False
        try:
            if not self.admission.publication.valid():
                return False
        except Exception:  # noqa: BLE001 -- A malformed publication cannot activate a queue.
            return False
        await self.queue.expire()
        row = await self.queue.claim(self.identifier)
        if row is None:
            return False
        try:
            if self.admission.inspect(row["owner_id"])["status"] != "READY":
                raise ValueError("Execution admission revoked")
            driver = self.admission.driver
            if driver.validate_definition(row["owner_id"], row["snapshot"]["definition"], row["repeats"]) is not True:
                raise ValueError("Fixed definition was not explicitly admitted")
            if self.stop is not None and self.stop.is_set():
                raise ValueError("Worker is stopping before dispatch")
            if not await self.queue.begin_dispatch(row["id"], row["owner_id"], row["lease_token"]):
                raise ValueError("Dispatch lease unavailable")
            lost = False
            renewed = asyncio.get_running_loop().time()

            async def cancelled():
                nonlocal lost, renewed
                if lost:
                    return True
                now = asyncio.get_running_loop().time()
                if now - renewed >= 15:
                    lost = not await self.queue.renew(row["id"], row["owner_id"], row["lease_token"])
                    renewed = now
                status = await self.db.fetch(
                    "SELECT cancel_requested FROM agent_evaluation_runs WHERE id=%s AND owner_id=%s",
                    (row["id"], row["owner_id"]),
                )
                return (
                    lost
                    or not status
                    or status[0]["cancel_requested"]
                    or not await self.maintenance()
                    or self.stop is not None
                    and self.stop.is_set()
                )

            async with PermitServer(lambda payload: self.permitted(row, payload)) as permit:
                result = await driver.execute(row, permit, cancelled)
            await self.commit(row, result)
        except asyncio.CancelledError:
            await asyncio.shield(self.retain(row, "EVALUATION_SUPERVISOR_CANCELLED"))
            raise
        except Exception as error:  # noqa: BLE001 -- Preserve unknown effects; never replay a dispatched process.
            await self.retain(row, "EVALUATION_WORKER_" + type(error).__name__.upper())
        return True

    async def serve(self, stop):
        self.stop = stop
        driver = self.admission.driver
        if not isinstance(driver, FixedSuiteDriver) or driver.prepared is None:
            raise ValueError("Explicitly prepared fixed driver required before consumer startup")
        if driver.consumer is not None and not driver.consumer.done():
            raise ValueError("This driver already has a live consumer")
        driver.consumer = asyncio.current_task()
        driver.consumer_stop = stop
        try:
            while not stop.is_set():
                if not await self.once():
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=2)
                    except TimeoutError:
                        continue
        finally:
            driver.consumer = None
            driver.consumer_stop = None
