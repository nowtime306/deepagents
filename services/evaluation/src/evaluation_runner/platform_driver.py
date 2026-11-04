"""Fixed engineering suite process adapter. No arbitrary commands, provider fallback or report upload."""

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from xml.etree import ElementTree as ET

from . import fixed_run
from .contracts import Dataset
from .fixed_audit import validate_ready
from .journal import atomic_json, digest

ROOT = Path(__file__).resolve().parents[4]
LIMIT = 8 * 1024 * 1024


def bounded(path, *, expected=None):
    if path.is_symlink() or path.is_junction():
        raise ValueError("Linked process artifact")
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT or expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Changed or oversized process artifact")
    return raw


def value(path, *, expected=None):
    result = json.loads(bounded(path, expected=expected).decode("utf-8"))
    json.dumps(result, allow_nan=False)
    return result


def suite_module():
    specification = importlib.util.spec_from_file_location(
        "registered_platform_suite", ROOT / "scripts/evaluation-suite.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def request_definition(request):
    expected = {
        "schema_version",
        "run_id",
        "owner_id",
        "candidate_commit",
        "definition",
        "budgets",
        "repeats",
        "version_set",
    }
    if (
        not isinstance(request, dict)
        or set(request) != expected
        or type(request["schema_version"]) is not int
        or request["schema_version"] != 1
    ):
        raise ValueError("Invalid platform process request")
    UUID(request["run_id"])
    UUID(request["owner_id"])
    if type(request["repeats"]) is not int or request["repeats"] != 1:
        raise ValueError("Only the reviewed engineering subset is implemented")
    proposed = Dataset.model_validate(request["definition"])
    registered = fixed_run.catalog()
    cases = {case.case_id: case for case in registered.cases}
    for case in proposed.cases:
        if case.case_id not in cases or digest(case.model_dump(mode="json")) != digest(
            cases[case.case_id].model_dump(mode="json")
        ):
            raise ValueError("Changed or unsupported fixed definition")
        descriptor = fixed_run.describe(case, registered)
        if descriptor["implemented"] is not True:
            raise ValueError("Fixed profile not implemented")
    budgets = request["budgets"]
    maxima = {
        "max_seconds": 86400,
        "max_model_calls": 10000,
        "max_tokens": 100000000,
        "max_sandboxes": 600,
    }
    if set(budgets) != set(maxima) or any(
        type(budgets[key]) is not int or not 0 <= budgets[key] <= maximum for key, maximum in maxima.items()
    ):
        raise ValueError("Invalid hard process budgets")
    required = {
        "max_seconds": sum(case.budgets.timeout_seconds for case in proposed.cases),
        "max_model_calls": sum(case.budgets.max_model_calls for case in proposed.cases),
        "max_tokens": sum(case.budgets.max_tokens for case in proposed.cases),
        "max_sandboxes": 2 * len(proposed.cases),
    }
    if any(budgets[key] < amount for key, amount in required.items()):
        raise ValueError("Hard parent budgets do not cover the declared selected cases")
    return proposed, registered


def subset_run(request, directory):
    proposed, registered = request_definition(request)
    # Credential material is never a CLI argument. These are explicit trusted ACL-file handles.
    admin = Path(os.environ["EVALUATION_PLATFORM_ADMIN_CONNECTION_FILE"])
    manager = os.environ.get("EVALUATION_PLATFORM_MANAGER_KEY_FILE")
    if (
        not admin.is_file()
        or not os.environ.get("EVALUATION_PLATFORM_PERMIT_SECRET")
        or not os.environ.get("EVALUATION_PLATFORM_PERMIT_ORIGIN")
    ):
        raise ValueError("Reviewed target permission delegation is unavailable")
    arguments = SimpleNamespace(
        dataset=ROOT / "services/evaluation/datasets/a20-fixed.json",
        output=directory / "suite",
        admin_connection_file=admin,
        manager_key_file=Path(manager) if manager else None,
        case_id=[case.case_id for case in proposed.cases],
        repeat_count=request["repeats"],
        platform_parent_run=request["run_id"],
        candidate_commit=request["candidate_commit"],
        rules_id="controlled-a20-v1",
        **request["budgets"],
    )
    arguments.output.mkdir(parents=True, exist_ok=False)
    atomic_json(
        arguments.output / "platform-parent.json",
        {
            "schema_version": 1,
            "contract": "platform-effect-permit-v1",
            "parent_run_id": request["run_id"],
            "parent_owner_id": request["owner_id"],
            "candidate_commit": request["candidate_commit"],
            "selected_cases": [case.case_id for case in proposed.cases],
        },
    )
    return suite_module().run_suite(arguments), registered


def sample_projection(child, row, ready):
    attempts = row["attempts"]
    if len(attempts) != 1:
        raise ValueError("A fixed child must contain exactly one actual attempt")
    actual = attempts[0]
    if (actual["case_id"], actual["repeat_index"]) != (
        child["case_id"],
        child["repeat_ordinal"],
    ):
        raise ValueError("Child attempt identity changed")
    facts = actual.get("fact_results", [])
    metrics = actual.get("metric_results", [])

    # Deliberately omit free-text reasons, prompts, arguments, URLs and tool outputs from platform projections.
    def results(rows):
        return [{"registered_id": item["registered_id"], "status": item["status"]} for item in rows]

    outcome = actual.get("task_outcome", "NEEDS_REVIEW")
    if outcome not in {"PASS", "FAIL", "NEEDS_REVIEW", "NOT_RUN"}:
        raise ValueError("Invalid original task outcome")
    if not facts and outcome == "PASS":
        raise ValueError("An empty fact set cannot pass")
    metric_status = (
        "NOT_REQUIRED" if not metrics else "ERROR" if any(item["status"] == "ERROR" for item in metrics) else "COMPLETE"
    )
    cleanup = "CLEAN" if child["cleanup"] == "DATABASE_REMOVED_EVIDENCE_RETAINED" else "RETAINED"
    verdicts = [row.get("gate", "NEEDS_REVIEW"), ready.get("gate", "NEEDS_REVIEW"), child.get("status", "NEEDS_REVIEW")]
    if any(value not in {"PASS", "FAIL", "NEEDS_REVIEW"} for value in verdicts):
        raise ValueError("Invalid original child gate")
    gate = (
        "FAIL"
        if outcome == "FAIL" or "FAIL" in verdicts or any(item["status"] == "FAIL" for item in facts)
        else "NEEDS_REVIEW"
        if outcome != "PASS"
        or any(value != "PASS" for value in verdicts)
        or "infrastructure_error" in child
        or cleanup != "CLEAN"
        or any(item["status"] != "PASS" for item in metrics)
        else "PASS"
    )
    root_refs = []
    for root in child.get("root_run_ids", []):
        root_refs.append({"run_id": str(UUID(root))})
    return {
        "case_id": child["case_id"],
        "repeat_index": child["repeat_ordinal"],
        "task_outcome": outcome,
        "metric_status": metric_status,
        "gate_verdict": gate,
        "cleanup_status": cleanup,
        "facts": results(facts),
        "metrics": results(metrics),
        "root_refs": root_refs,
        "evidence_refs": [
            {
                "kind": "verified-fixed-ready",
                "sha256": digest(ready),
                "session_id": ready["session_id"],
            }
        ],
        "usage": {**actual["usage"], "completeness": "KNOWN"} if actual.get("usage_completeness") == "KNOWN" else None,
    }


def collect(request, directory, registered):
    suite = value(directory / "suite/suite.json")
    if suite["candidate_commit"] != request["candidate_commit"] or suite["dataset_digest"] != digest(
        registered.model_dump(mode="json")
    ):
        raise ValueError("Foreign suite metadata")
    expected = {
        (case["case_id"], ordinal)
        for case in request["definition"]["cases"]
        for ordinal in range(1, request["repeats"] + 1)
    }
    children = suite["attempts"]
    if (
        len(children) != len(expected)
        or {(child["case_id"], child["repeat_ordinal"]) for child in children} != expected
    ):
        raise ValueError("Partial or duplicate suite; retain its original evidence")
    samples = []
    suite_root = (directory / "suite").resolve()
    for child in children:
        relative = Path(child["child"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid fixed child handle")
        base = suite_root / relative
        for ancestor in (base, *base.parents):
            if ancestor == suite_root.parent:
                break
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError("Linked child handle")
        target = value(base / "target/target-manifest.json")
        context = value(base / "suite-context.json")
        ready = validate_ready(base / "result")
        if (
            ready["environment_id"] != target["environment_id"]
            or ready["owner_id"] != target["owner_id"]
            or ready["suite_context"] != context
            or context["suite_id"] != suite["suite_id"]
            or context["case_id"] != child["case_id"]
            or context["repeat_index"] != child["repeat_ordinal"]
            or context["candidate_commit"] != request["candidate_commit"]
        ):
            raise ValueError("Foreign target or case receipt")
        report = value(base / "result/report.json", expected=ready["files"]["report.json"])
        samples.append(sample_projection(child, report, ready))
    suite_gate = suite.get("gate", "NEEDS_REVIEW")
    if suite_gate not in {"PASS", "FAIL", "NEEDS_REVIEW"}:
        raise ValueError("Invalid original suite gate")
    if suite.get("stopped_reason") and suite_gate == "PASS":
        suite_gate = "NEEDS_REVIEW"
    for sample in samples:
        if "FAIL" in {sample["gate_verdict"], suite_gate}:
            sample["gate_verdict"] = "FAIL"
        elif "NEEDS_REVIEW" in {sample["gate_verdict"], suite_gate}:
            sample["gate_verdict"] = "NEEDS_REVIEW"
    cleanup = "CLEAN" if all(item["cleanup_status"] == "CLEAN" for item in samples) else "RETAINED"
    gate = (
        "FAIL"
        if any(item["gate_verdict"] == "FAIL" for item in samples)
        else "PASS"
        if cleanup == "CLEAN" and all(item["gate_verdict"] == "PASS" for item in samples)
        else "NEEDS_REVIEW"
    )
    public = {
        "schema_version": 1,
        "run_id": request["run_id"],
        "scope": "controlled engineering; external model quality/UI not evaluated",
        "gate_verdict": gate,
        "cleanup_status": cleanup,
        "cost": {"external_models": "NOT_EXECUTED", "infrastructure": "NOT_PRICED", "complete_total": None},
        "samples": samples,
    }
    xml = ET.Element(
        "testsuite",
        name="agent-platform",
        tests=str(len(samples)),
        failures=str(sum(sample["gate_verdict"] == "FAIL" for sample in samples)),
        errors=str(sum(sample["gate_verdict"] == "NEEDS_REVIEW" for sample in samples)),
    )
    for sample in samples:
        node = ET.SubElement(
            xml,
            "testcase",
            classname=sample["case_id"],
            name=str(sample["repeat_index"]),
        )
        if sample["gate_verdict"] != "PASS":
            ET.SubElement(
                node,
                "failure" if sample["gate_verdict"] == "FAIL" else "error",
                message=sample["gate_verdict"],
            )
    files = {
        "json": json.dumps(public, ensure_ascii=False, allow_nan=False),
        "junit": ET.tostring(xml, encoding="unicode"),
        "markdown": "# Agent评测报告\n\n门禁："
        + gate
        + "。受控工程结果，未执行真实模型效果或UI验收。\n\n"
        + "\n".join(
            f"- {sample['case_id']} / {sample['repeat_index']}：门禁 {sample['gate_verdict']}，"
            f"任务事实 {sample['task_outcome']}"
            for sample in samples
        ),
    }
    manifest = {
        "kind": "agent-platform-report-v1",
        "run_id": request["run_id"],
        "owner_id": request["owner_id"],
        "ready_verified": True,
        "files_sha256": {key: hashlib.sha256(text.encode()).hexdigest() for key, text in files.items()},
        "evidence_scope": (
            "private fixed child facts and exact audit indices verified; public projection omits free text"
        ),
    }
    return {
        "schema_version": 1,
        "run_id": request["run_id"],
        "owner_id": request["owner_id"],
        "verified": True,
        "gate_verdict": gate,
        "samples": samples,
        "cleanup_status": cleanup,
        "report": {
            "verified": True,
            "redacted": True,
            "manifest": manifest,
            "files": files,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=UUID, required=True)
    args = parser.parse_args()
    directory = ROOT / ".local/agent-evaluations/platform-worker" / str(args.job_id)
    try:
        for ancestor in (directory, *directory.parents):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError("Linked platform job root")
        request = value(directory / "request.json")
        if request["run_id"] != str(args.job_id):
            raise ValueError("Wrong process job identity")
        code, registered = subset_run(request, directory)
        result = collect(request, directory, registered)
        result["runtime_attestation_sha256"] = hashlib.sha256(
            bounded(directory / "runtime-attestation.json")
        ).hexdigest()
        secret = os.environ["EVALUATION_PLATFORM_RESULT_SECRET"]
        if len(secret) < 32:
            raise ValueError("Missing private reader result key")
        atomic_json(directory / "result.json", result)
        raw = bounded(directory / "result.json")
        atomic_json(
            directory / "result-ready.json",
            {
                "schema_version": 1,
                "run_id": request["run_id"],
                "owner_id": request["owner_id"],
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mac": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
            },
        )
        return code if code in {0, 1} else 1
    except Exception as error:  # noqa: BLE001 -- Private evidence is retained; errors never become a fake report.
        if directory.is_dir():
            atomic_json(
                directory / "driver-error.json",
                {
                    "status": "NEEDS_REVIEW",
                    "code": type(error).__name__,
                    "run_id": str(args.job_id),
                },
            )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
