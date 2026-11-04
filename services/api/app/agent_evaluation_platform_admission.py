"""Fail-closed platform admission. Only server-registered verified evidence/driver can enable execution."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.agent_evaluation_platform_types import ComparisonMaterial, digest
from app.errors import fail

EXPECTED = {f"AET-{mode}{number:02}" for mode, size in (("A", 5), ("W", 8), ("F", 7)) for number in range(1, size + 1)}
THRESHOLDS = {
    "a20": {"total": 20, "ask": 5, "work": 8, "workflow": 7},
    "b100": {"total": 100, "ask": 25, "work": 40, "workflow": 35},
    "effect_repeats": 3,
}


class RegisteredDriver(Protocol):
    def validate_definition(self, owner: UUID, definition: dict, repeats: int) -> bool: ...
    def readiness(self, owner: UUID) -> dict: ...
    def candidates(self, owner: UUID) -> list[dict]: ...
    def default_version_set(self, owner: UUID) -> dict | None: ...


@dataclass(frozen=True)
class Publication:
    receipt: bytes
    pinned_sha256: str
    verify_evidence: Callable[[bytes], bool]

    def valid(self) -> bool:
        if hashlib.sha256(self.receipt).hexdigest() != self.pinned_sha256:
            return False
        if self.verify_evidence(self.receipt) is not True:
            return False
        value = json.loads(self.receipt)
        rows = value.get("cases", [])
        if (
            value.get("schema_version") != 2
            or type(value.get("schema_version")) is not int
            or value.get("kind") != "agent-evaluation-stage-a-publication-v2"
            or value.get("acceptance_verdict") != "PASS"
            or value.get("independent_review") != "CLOSED"
            or not isinstance(rows, list)
            or len(rows) != 20
            or any(not isinstance(row, dict) for row in rows)
            or {row.get("case_id") for row in rows} != EXPECTED
        ):
            return False
        for row in rows:
            if (
                row.get("acceptance_verdict") != "PASS"
                or row.get("evidence_complete") is not True
                or row.get("cleanup_verified") is not True
                or row.get("actual_task_outcome") not in {"PASS", "FAIL", "NEEDS_REVIEW"}
                or row.get("expected_gate") not in {"PASS", "FAIL", "NEEDS_REVIEW"}
                or row.get("actual_gate") != row["expected_gate"]
                or row.get("actual_cleanup") not in {"CLEAN", "RETAINED"}
                or row.get("actual_cleanup") != row.get("expected_cleanup")
            ):
                return False
            for field in ("evidence_digest", "definition_digest"):
                fingerprint = row.get(field)
                if (
                    not isinstance(fingerprint, str)
                    or len(fingerprint) != 64
                    or any(char not in "0123456789abcdef" for char in fingerprint)
                ):
                    return False
        return True


@dataclass
class Admission:
    publication: Publication | None = None
    driver: RegisteredDriver | None = None

    def inspect(self, owner: UUID) -> dict:
        accepted = False
        driver_ready = False
        try:
            accepted = isinstance(self.publication, Publication) and self.publication.valid()
        except Exception:  # noqa: BLE001 -- Missing or invalid publication blocks execution.
            accepted = False
        try:
            observed = self.driver.readiness(owner) if self.driver else {}
            driver_ready = bool(observed) and all(
                observed.get(key) is True
                for key in (
                    "ready",
                    "source_verified",
                    "lease_enforced",
                    "owner_permissions_per_effect",
                    "cooperative_cancel",
                    "ready_reader",
                    "precise_cleanup",
                )
            )
        except Exception:  # noqa: BLE001 -- Registration/readiness failure blocks without leaking paths or secret-bearing errors.
            driver_ready = False
        blockers = []
        if not accepted:
            blockers.append(
                {
                    "code": "EVALUATION_STAGE_A_NOT_ACCEPTED",
                    "message": "A20可信独立验收证据尚未闭合，暂不接收平台批次。",
                }
            )
        if not driver_ready:
            blockers.append({"code": "EVALUATION_RUNNER_UNAVAILABLE", "message": "尚无通过准入检查的持久执行驱动。"})
        return {
            "status": "READY" if accepted and driver_ready else "BLOCKED",
            "stage_a_accepted": accepted,
            "runner_ready": driver_ready,
            "blockers": blockers,
        }


def service(request) -> Admission:
    value = getattr(request.app.state, "agent_evaluation_platform_admission", None)
    return value if isinstance(value, Admission) else Admission()


def validate_version_snapshot(snapshot: dict) -> dict:
    required = {
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
    if not isinstance(snapshot, dict) or set(snapshot) != required:
        fail(503, "EVALUATION_VERSION_SET_UNAVAILABLE", "尚无可信冻结的候选版本集")
    UUID(str(snapshot["candidate_ref"]))
    for key, size in (("platform_commit", 40), ("source_digest", 64), ("runner_lock_digest", 64), ("rules_digest", 64)):
        value = snapshot[key]
        if not isinstance(value, str) or len(value) != size or any(char not in "0123456789abcdef" for char in value):
            fail(503, "EVALUATION_VERSION_SET_UNAVAILABLE", "版本集源码或依赖摘要未验证")
    for row in snapshot["business_refs"]:
        if (
            set(row) != {"kind", "resource_id", "version", "definition_digest"}
            or row["kind"] not in {"agent", "workflow"}
            or type(row["version"]) is not int
        ):
            fail(503, "EVALUATION_VERSION_SET_UNAVAILABLE", "业务版本集未闭合")
    for row in snapshot["model_refs"]:
        if set(row) != {"model_id", "effect_fingerprint", "kind", "protocol"}:
            fail(503, "EVALUATION_VERSION_SET_UNAVAILABLE", "模型版本集只能包含非凭据身份")
    json.dumps(snapshot, allow_nan=False)
    return snapshot


def checked_report(row, run, owner):
    manifest, files = row["manifest"], row["files"]
    if (
        row["verified"] is not True
        or row["redacted"] is not True
        or manifest.get("kind") != "agent-platform-report-v1"
        or manifest.get("run_id") != str(run)
        or manifest.get("owner_id") != str(owner)
        or manifest.get("ready_verified") is not True
        or set(files) != {"json", "junit", "markdown"}
        or set(manifest.get("files_sha256", {})) != set(files)
    ):
        fail(409, "EVALUATION_REPORT_NOT_READY", "报告身份或完整性尚未核验")
    for key, text in files.items():
        if (
            not isinstance(text, str)
            or len(text.encode()) > 8 * 1024 * 1024
            or hashlib.sha256(text.encode()).hexdigest() != manifest["files_sha256"][key]
        ):
            fail(409, "EVALUATION_REPORT_NOT_READY", "报告文件缺失、过大或摘要不符")
    return manifest, files


def compare_reports(left, right, axis):
    try:
        left = ComparisonMaterial.model_validate(left).model_dump(mode="json")
        right = ComparisonMaterial.model_validate(right).model_dump(mode="json")
    except ValidationError:
        return False, ["实际比较身份、独立重复或任务事实不完整"], None, []
    reasons = []
    if any(row["task_outcome"] not in {"PASS", "FAIL"} for value in (left, right) for row in value["samples"]):
        reasons.append("存在未执行或未决的任务，不能计算可信提升")
    for key in ("dataset_digest", "rules_digest", "material_digest", "repeats", "execution_mode"):
        if key not in left or key not in right or digest(left[key]) != digest(right[key]):
            reasons.append("比较条件不同：" + key)
    for key in ("comparison_ready", "evidence_complete"):
        if left.get(key) is not True or right.get(key) is not True:
            reasons.append("缺少完整的比较证据：" + key)
    changed = [
        key
        for key in ("model_identity", "business_identity", "platform_identity")
        if digest(left.get(key)) != digest(right.get(key))
    ]
    if any(key not in left or key not in right for key in ("model_identity", "business_identity", "platform_identity")):
        reasons.append("实际使用版本身份缺失")
    requested = {"model": "model_identity", "business_version": "business_identity", "platform": "platform_identity"}
    if len(changed) > 1 or axis != "auto" and any(key != requested[axis] for key in changed):
        reasons.append("声明变化项之外存在版本漂移")
    if reasons:
        return False, reasons, None, []
    a = {(row["case_id"], row["repeat_index"]): row for row in left.get("samples", [])}
    b = {(row["case_id"], row["repeat_index"]): row for row in right.get("samples", [])}
    if not a or set(a) != set(b) or len(a) != len(left.get("samples", [])) or len(b) != len(right.get("samples", [])):
        return False, ["任务或独立重复集合不一致"], None, []
    changes = [
        {
            "case_id": key[0],
            "repeat_index": key[1],
            "baseline": a[key]["task_outcome"],
            "candidate": b[key]["task_outcome"],
        }
        for key in sorted(a)
    ]
    summary = {
        "paired_samples": len(changes),
        "baseline_pass": sum(x["baseline"] == "PASS" for x in changes),
        "candidate_pass": sum(x["candidate"] == "PASS" for x in changes),
        "statistical_significance": "NOT_ASSESSED",
    }
    return True, [], summary, changes
