"""Target-only fixed W02 verifier, dispatched by a genuinely leased WorkWorker."""
import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from app.resource_runtime import resource_run
from app.work_worker import CapacityUnavailable
from opensandbox.models.execd import RunCommandOpts

PROGRAM = Path(__file__).with_name("java_verifier_program.py").read_bytes()
PROGRAM_SHA = hashlib.sha256(PROGRAM).hexdigest()
LAUNCHER = Path(__file__).with_name("java_verifier_launcher.py").read_bytes()
LAUNCHER_SHA = hashlib.sha256(LAUNCHER).hexdigest()
TESTS_SHA = hashlib.sha256(b"[[2,8,8],[-7,-3,-3],[4,4,4]]").hexdigest()
ORIGINAL = b'public class Max { public static int max(int a,int b){return a;} }'
ORIGINAL_SHA = hashlib.sha256(ORIGINAL).hexdigest()
BASELINE_PROTOCOL = "original-repair-v1"
SHA = re.compile(r"^[a-f0-9]{64}$")
BOOTSTRAP = '''import base64,json,os,sys
from pathlib import Path
body=json.loads(base64.b64decode(sys.argv[1],validate=True))
phase=body.pop('phase')
if phase not in {'baseline','candidate'}:raise ValueError('Unknown fixed phase')
base=Path('/opt/ae-java-verifier');archive=Path('/opt/ae-java-verifier-baseline')
if phase=='baseline':
 if base.exists() or archive.exists():raise ValueError('Fixed baseline directory already exists')
else:
 if not base.is_dir() or base.is_symlink() or archive.exists():raise ValueError('Fixed baseline directory unavailable')
 source=Path('/workspace/Max.java')
 if source.is_symlink() or source.read_bytes()!=b'public class Max { public static int max(int a,int b){return a;} }':raise ValueError('Original source changed')
 base.rename(archive)
base.mkdir(mode=0o755)
private=base/'private';private.mkdir(mode=0o700)
program=private/'supervisor.py';program.write_bytes(base64.b64decode(body['program'],validate=True));program.chmod(0o400)
launcher=base/'launch.py';launcher.write_bytes(base64.b64decode(body['launcher'],validate=True));launcher.chmod(0o444)
Path('/workspace').mkdir(exist_ok=True)
os.execv(sys.executable,[sys.executable,'-I',str(program),body['payload']])
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


EVALUATION_EFFECT_GUARD_VERSION = 'platform-effect-permit-v1'


class JavaVerifier:
    def __init__(self, database, sandboxes, artifacts, models, profiles, *, environment_id, state_path):
        self.database, self.sandboxes, self.artifacts = database, sandboxes, artifacts
        self.models, self.profiles = models, profiles
        self.environment_id, self.path = str(UUID(str(environment_id))), Path(state_path)
        self.lock, self.storage_failed = asyncio.Lock(), False
        self.jobs = {}
        self.ready = self.path.exists()
        if self.path.exists():
            if self.path.is_symlink() or self.path.is_junction() or self.path.stat().st_size > 1024 * 1024:
                raise ValueError("Invalid verifier state")
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (set(value) != {"schema_version", "environment_id", "jobs"} or type(value["schema_version"]) is not int
                    or value["schema_version"] != 3 or value["environment_id"] != self.environment_id or not isinstance(value["jobs"], dict)):
                raise ValueError("Verifier state belongs to another environment")
            self.jobs = value["jobs"]
            for task_id, job in self.jobs.items():
                if (not isinstance(job.get("source_model_id"), str) or not 0 < len(job["source_model_id"]) <= 120
                        or str(UUID(task_id)) != job["task_id"] or job.get("isolation_sha256") != LAUNCHER_SHA
                        or job.get("baseline_source_sha256") != ORIGINAL_SHA):
                    raise ValueError("Verifier state lacks its sealed source model or isolation identity")
                if job["phase"] in {"RUNNING", "BASELINE_RECORDED", "RESULT_RECORDED"}:
                    job["phase"] = "UNKNOWN"
            self.save()

    async def initialize(self):
        """Create the registry only before any Work admission; missing recovery state must not fall through."""
        if self.ready:
            return
        if await self.database.fetch("SELECT 1 FROM work_tasks UNION ALL SELECT 1 FROM work_queue_runs LIMIT 1"):
            raise RuntimeError("Missing verifier registry on a used target requires review")
        self.save()
        self.ready = True

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"schema_version": 3, "environment_id": self.environment_id, "jobs": self.jobs},
                          stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            self.storage_failed = True
            raise

    def handles(self, task_id):
        if self.storage_failed or not self.ready:
            raise RuntimeError("Verifier state persistence is unavailable")
        return str(task_id) in self.jobs

    async def source(self, owner, source_run, artifact, checksum, namespace):
        rows = await self.database.fetch(
            "SELECT a.id,a.run_id,a.snapshot_id,a.owner_id,a.path,a.digest,a.size,r.status,r.task_id,r.model_id,"
            "r.environment,(SELECT id FROM work_sandbox_namespace WHERE singleton) AS namespace,"
            "(SELECT count(*) FROM work_sandbox_sessions WHERE run_id=r.id) AS session_count,"
            "(SELECT count(*) FROM work_sandbox_sessions WHERE run_id=r.id AND state<>'RELEASED') AS active_sessions,"
            "(SELECT count(*) FROM model_usage WHERE owner_id=r.owner_id AND mode='work' AND run_id=r.id "
            "AND status IN ('PENDING','UNKNOWN')) AS unknown_usage,"
            "(SELECT count(*) FROM operation_executions WHERE run_id=r.id AND status IN ('STARTED','UNKNOWN')) AS unknown_operations "
            "FROM work_artifacts a JOIN work_queue_runs r ON r.id=a.run_id "
            "WHERE a.id=%s AND r.id=%s AND a.owner_id=%s AND r.owner_id=%s "
            "AND a.snapshot_id=(SELECT id FROM work_snapshots WHERE run_id=r.id ORDER BY created_at DESC,id DESC LIMIT 1)",
            (artifact, source_run, owner, owner))
        if not rows:
            raise ValueError("Owned candidate artifact is unavailable")
        row = rows[0]
        if (row["status"] != "COMPLETED" or row["environment"] != "coding" or str(row["namespace"]) != namespace
                or row["session_count"] != 1 or row["active_sessions"] or row["unknown_usage"] or row["unknown_operations"]
                or row["path"] != "/Max.java" or row["digest"] != checksum or not 0 < row["size"] <= 65536):
            raise ValueError("Candidate identity, terminal evidence or source digest is incomplete")
        with self.profiles.lock:
            binding = self.profiles.models.get(row["model_id"])
            if binding != {"owner_id": str(owner), "case_id": "AET-W02", "role": "lead"}:
                raise ValueError("Candidate model is not the sealed W02 lead")
        path = self.artifacts.path(UUID(str(artifact)))
        if (path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(self.artifacts.directory.resolve())):
            raise ValueError("Aliased candidate artifact")
        with path.open("rb") as stream:
            content = stream.read(65537)
        if len(content) != row["size"] or hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError("Candidate bytes changed")
        content.decode("utf-8")
        return row, content

    async def register(self, owner, namespace, task_id, source_run_id, artifact_id, source_sha256):
        if not self.ready:
            raise RuntimeError("Initialize the verifier registry before Work admission")
        owner, task_id = UUID(str(owner)), UUID(str(task_id))
        namespace, source_run_id, artifact_id = str(UUID(str(namespace))), UUID(str(source_run_id)), UUID(str(artifact_id))
        if not SHA.fullmatch(source_sha256):
            raise ValueError("Invalid source digest")
        row, _ = await self.source(owner, source_run_id, artifact_id, source_sha256, namespace)
        tasks = await self.database.fetch(
            "SELECT revision,(SELECT count(*) FROM work_queue_runs WHERE task_id=t.id) AS runs "
            "FROM work_tasks t WHERE id=%s AND owner_id=%s", (task_id, owner))
        if not tasks or tasks[0]["runs"] or str(row["task_id"]) == str(task_id):
            raise ValueError("An unused separate owned verifier task is required")
        job = {"owner_id": str(owner), "namespace": namespace, "task_id": str(task_id),
               "task_revision": tasks[0]["revision"], "source_run_id": str(source_run_id), "artifact_id": str(artifact_id),
               "source_model_id": row["model_id"], "isolation_sha256": LAUNCHER_SHA,
               "source_snapshot_id": str(row["snapshot_id"]), "source_sha256": source_sha256,
               "program_sha256": PROGRAM_SHA, "tests_sha256": TESTS_SHA, "baseline_source_sha256": ORIGINAL_SHA, "phase": "REGISTERED", "verifier_run_id": None}
        async with self.lock:
            if self.storage_failed:
                raise RuntimeError("Verifier persistence is unavailable")
            if str(task_id) in self.jobs:
                if {k: v for k, v in self.jobs[str(task_id)].items() if k in job} != job:
                    raise ValueError("A sealed verifier cannot be rebound or replayed")
            else:
                self.jobs[str(task_id)] = job
                self.save()
        return {"task_id": str(task_id), "sealed": True, "source_sha256": source_sha256,
                "program_sha256": PROGRAM_SHA, "tests_sha256": TESTS_SHA}

    async def guard(self, run, job):
        if run["model_id"] != job["source_model_id"]:
            raise ValueError("Verifier must use the exact sealed source model ID")
        with self.profiles.lock:
            if self.profiles.models.get(job["source_model_id"]) != {"owner_id": job["owner_id"], "case_id": "AET-W02", "role": "lead"}:
                raise ValueError("Verifier model binding was revoked or changed")
        rows = await self.database.fetch(
            "SELECT 1 FROM work_queue_runs WHERE id=%s AND task_id=%s AND owner_id=%s AND status='RUNNING' "
            "AND lease_token=%s AND lease_until>now() AND NOT cancel_requested "
            "AND task_revision=%s AND model_id=%s AND environment='coding' AND input_files='[]'::jsonb "
            "AND (SELECT id FROM work_sandbox_namespace WHERE singleton)=%s",
            (run["id"], UUID(job["task_id"]), UUID(job["owner_id"]), run["lease_token"], job["task_revision"], job["source_model_id"], UUID(job["namespace"])))
        if not rows or str(run["id"]) == job["source_run_id"] or str(run["owner_id"]) != job["owner_id"]:
            raise RuntimeError("Live verifier ownership or Work lease was lost")
        parent_guard = getattr(self.database, 'evaluation_effect_guard', None)
        if parent_guard is not None:
            await parent_guard('tool')

    async def execute(self, run):
        task = str(run["task_id"])
        async with self.lock:
            if self.storage_failed or task not in self.jobs:
                raise RuntimeError("No sealed verifier task")
            job = self.jobs[task]
            if (job["phase"] != "REGISTERED" or job["program_sha256"] != PROGRAM_SHA
                    or job["tests_sha256"] != TESTS_SHA or job["isolation_sha256"] != LAUNCHER_SHA
                    or job["baseline_source_sha256"] != ORIGINAL_SHA
                    or job["verifier_run_id"] not in {None, str(run["id"])}):
                raise RuntimeError("Verifier requires review; no execution replay")
            await self.guard(run, job)
            job.update(phase="RUNNING", verifier_run_id=str(run["id"]))
            self.save()  # Durable intent precedes any sandbox creation.
        session = None
        try:
            async with resource_run(self.database, UUID(job["owner_id"]), "work", run["id"]):
                config = await self.models.get(run["model_id"])
                if config.kind != "chat" or config.protocol != "openai" or config.model != "eval::AET-W02::v1":
                    raise ValueError("Verifier requires the reviewed enabled W02 model identity")
                source, content = await self.source(UUID(job["owner_id"]), UUID(job["source_run_id"]), UUID(job["artifact_id"]),
                                               job["source_sha256"], job["namespace"])
                if source["model_id"] != job["source_model_id"]:
                    raise ValueError("Candidate source model changed after sealing")
                await self.guard(run, job)
                if self.sandboxes.maximum != 2 or self.sandboxes.attempts != 1:
                    raise RuntimeError("Exactly one candidate sandbox must precede the verifier")
                created = await self.sandboxes.create(run["id"], run["lease_token"], "coding")
                if created is None:
                    job["phase"] = "REGISTERED"
                    self.save()
                    raise CapacityUnavailable("Known local sandbox admission refusal")
                session, sandbox = created
                job.update(session_id=str(session["id"]), sandbox_id=str(sandbox.id))
                self.save()
                await self.guard(run, job)
                baseline = await self.run_fixed(run, job, sandbox, ORIGINAL, "baseline")
                validate_baseline_report(baseline)
                job.update(phase="BASELINE_RECORDED", baseline_report=baseline)
                self.save()  # The actual negative control is durable before repaired execution.
                report = await self.run_fixed(run, job, sandbox, content, "candidate")
                validate_pair(baseline, report, job["source_sha256"])
                job.update(phase="RESULT_RECORDED", report=report)
                self.save()
        except CapacityUnavailable:
            raise
        except BaseException:
            job["phase"] = "UNKNOWN"
            self.save()
            raise
        finally:
            if session is not None:
                try:
                    if not await self.sandboxes.release(UUID(str(session["id"]))):
                        raise RuntimeError("Verifier sandbox release is not confirmed")
                except BaseException:
                    job["phase"] = "UNKNOWN"
                    self.save()
                    raise
        job["phase"] = "COMPLETED"
        self.save()
        return {"case_id": "AET-W02", "verifier_run_id": str(run["id"]), "source_run_id": job["source_run_id"],
                "artifact_id": job["artifact_id"], "source_sha256": job["source_sha256"],
                "baseline_verification": baseline, "verification": report}


    async def run_fixed(self, run, job, sandbox, content, phase):
        if phase not in {"baseline", "candidate"} or (phase == "baseline" and content != ORIGINAL):
            raise ValueError("Only the fixed original/candidate phases are allowed")
        await self.guard(run, job)
        checksum = hashlib.sha256(content).hexdigest()
        payload = base64.b64encode(json.dumps({"source_base64": base64.b64encode(content).decode(),
                                              "source_sha256": checksum}).encode()).decode()
        envelope = base64.b64encode(json.dumps({"program": base64.b64encode(PROGRAM).decode(),
            "launcher": base64.b64encode(LAUNCHER).decode(), "payload": payload, "phase": phase}).encode()).decode()
        result = await sandbox.commands.run(shlex.join(["python3", "-I", "-c", BOOTSTRAP, envelope]),
            opts=RunCommandOpts(uid=0, gid=0, timeout=timedelta(seconds=120)))
        await self.guard(run, job)
        if result.error or result.exit_code != 0 or len(result.text.encode()) > 131072:
            raise RuntimeError("Fixed verifier result is unavailable")
        return validate_report(json.loads(result.text), checksum)


def validate_baseline_report(report):
    validate_report(report, ORIGINAL_SHA)
    if report["passed"] is not False or len(report["cases"]) != 3:
        raise ValueError("Compiled original negative control with three actual receipts is required")
    for row, actual in zip(report["cases"], (2, -7, 4), strict=True):
        if (row["exit_code"] != 0 or row["timed_out"] or row["truncated"]
                or row["stdout_sha256"] != hashlib.sha256(str(actual).encode()).hexdigest()):
            raise ValueError("Original negative control was not actually observed")
    return report


def validate_pair(baseline, candidate, checksum):
    validate_baseline_report(baseline)
    validate_report(candidate, checksum)
    for key in ("program_sha256", "tests_sha256", "java_version_sha256", "javac_version_sha256"):
        if baseline[key] != candidate[key]:
            raise ValueError("Paired executions must use the same fixed program/tests/JDK")
    if baseline["isolation"]["policy_sha256"] != candidate["isolation"]["policy_sha256"]:
        raise ValueError("Paired launcher policy differs")


def validate_report(value, checksum):
    required = {"schema_version", "protocol", "source_sha256", "program_sha256", "protection", "isolation", "tests_sha256", "compile", "cases", "passed",
                "javac_version_sha256", "java_version_sha256"}
    protection = {"uid": 1000, "gid": 1000, "blocked": True, "comparator_unchanged": True,
                  "cwd": "/workspace", "workspace_writable": True, "parent_replace_blocked": True}
    if (set(value) not in (required, required | {"adapter_compile"}, required | {"adapter_compile", "class_protection"}) or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["protocol"] != "java-max-v2" or value["source_sha256"] != checksum
            or value["program_sha256"] != PROGRAM_SHA or value["tests_sha256"] != TESTS_SHA
            or set(value["protection"]) != set(protection)
            or any(type(value["protection"][key]) is not type(expected) or value["protection"][key] != expected
                   for key, expected in protection.items())
            or any(not isinstance(value[key], str) or not SHA.fullmatch(value[key]) for key in ("javac_version_sha256", "java_version_sha256"))
            or type(value["passed"]) is not bool):
        raise ValueError("Unbound or unprotected verifier result")
    isolation = value["isolation"]
    markers = {"schema_version": 1, "architecture": "amd64", "uid": 1000, "gid": 1000,
               "euid": 1000, "egid": 1000, "saved_uid": 1000, "saved_gid": 1000, "no_supplementary_groups": True,
               "no_new_privs": 1, "seccomp_mode": 2, "no_inherited_sockets": True,
               "native_getpid_allowed": True, "policy_sha256": LAUNCHER_SHA}
    if (set(isolation) != {*markers, "denied_errno"}
            or any(type(isolation[key]) is not type(expected) or isolation[key] != expected for key, expected in markers.items())
            or set(isolation["denied_errno"]) != {"inet", "inet6", "socketpair", "execd_loopback", "connect", "x32", "compat", "syscall_425", "syscall_426", "syscall_427", "syscall_438"}
            or any(type(value) is not int or value != 1 for value in isolation["denied_errno"].values())):
        raise ValueError("Inherited socket denial evidence is incomplete")
    def successful(row):
        if (set(row) != {"exit_code", "timed_out", "truncated", "stdout_sha256", "stderr_sha256"}
                or type(row["exit_code"]) is not int or any(type(row[key]) is not bool for key in ("timed_out", "truncated"))
                or any(not SHA.fullmatch(row[key]) for key in ("stdout_sha256", "stderr_sha256"))):
            raise ValueError("Invalid command receipt")
        return row["exit_code"] == 0 and not row["timed_out"] and not row["truncated"]
    compiled = successful(value["compile"])
    if "adapter_compile" in value:
        compiled = successful(value["adapter_compile"]) and compiled
    elif compiled:
        raise ValueError("Missing adapter compilation")
    if len(value["cases"]) != (3 if compiled else 0):
        raise ValueError("Incomplete fixed case results")
    if compiled:
        markers = value.get("class_protection", {})
        if set(markers) != {"adapter_blocked", "candidate_blocked", "digests_unchanged"} or any(marker is not True for marker in markers.values()):
            raise ValueError("Compiled classes are not protected")
    outcomes = []
    for index, (row, expected) in enumerate(zip(value["cases"], (8, -3, 4))):
        expected_sha = hashlib.sha256(str(expected).encode()).hexdigest()
        if set(row) != {"case_index", "passed", "expected_sha256", "exit_code", "timed_out", "truncated", "stdout_sha256", "stderr_sha256"}:
            raise ValueError("Unexpected fixed test fields")
        ok = successful({k: row[k] for k in ("exit_code", "timed_out", "truncated", "stdout_sha256", "stderr_sha256")}) and row["stdout_sha256"] == expected_sha
        if type(row["case_index"]) is not int or row["case_index"] != index or row["expected_sha256"] != expected_sha or type(row["passed"]) is not bool or row["passed"] != ok:
            raise ValueError("A test's declared verdict differs from its actual receipt")
        outcomes.append(ok)
    if value["passed"] != (compiled and len(outcomes) == 3 and all(outcomes)):
        raise ValueError("A declared pass cannot replace fixed output comparison")
    return value


def attach_java_verifier(app, service):
    """Runtime calls this only on its attested test target, never the production app."""
    from app.agent_evaluation_environment import environment
    from app.permissions import require_account
    from app.security import administrator
    from fastapi import Request
    from pydantic import BaseModel, ConfigDict, Field

    class Binding(BaseModel):
        model_config = ConfigDict(extra="forbid")
        task_id: UUID
        source_run_id: UUID
        artifact_id: UUID
        source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @app.post("/api/v1/agent-evaluations/profiles/java-verifiers")
    async def register(body: Binding, request: Request):
        from app.errors import fail
        proof = await environment(request)
        user = await administrator(request)
        await require_account(request.app.state.db, user["id"], "evaluation.run")
        await require_account(request.app.state.db, user["id"], "work.execute")
        try:
            return await service.register(user["id"], str(proof["sandbox_namespace"]), body.task_id,
                                          body.source_run_id, body.artifact_id, body.source_sha256)
        except ValueError:
            fail(422, "EVALUATION_VERIFIER_INVALID", "固定Java验证器绑定信息不匹配")

    app.state.evaluation_java_verifier = service
