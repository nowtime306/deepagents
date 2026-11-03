"""Consistent local JSON/JUnit/readable reports; uncertainty cannot produce green CI."""

from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from .journal import atomic_json


def write_report(directory: Path, data: dict) -> dict:
    attempts = data.get("attempts", [])
    counts = dict(Counter(row.get("task_outcome", "NEEDS_REVIEW") for row in attempts))
    gate = "PASS" if attempts and all(row.get("task_outcome") == "PASS" for row in attempts) else "NEEDS_REVIEW"
    if counts.get("FAIL"):
        gate = "FAIL"
    expected = data.get("expected_attempts", len(attempts))
    if (len(attempts) != expected or data.get("cancelled")) and gate == "PASS":
        gate = "NEEDS_REVIEW"
    report = {"schema_version": 1, "scope": "controlled engineering; external semantic quality/UI not evaluated",
              "environment_id": data.get("environment_id"), "dataset_digest": data.get("dataset_digest"),
              "gate": gate, "counts": counts, "expected_attempts": expected,
              "unexecuted_attempts": max(0, expected - len(attempts)), "attempts": attempts}
    atomic_json(directory / "report.json", report)
    suite = ET.Element("testsuite", name="agent-evaluation", tests=str(len(attempts)),
                       failures=str(counts.get("FAIL", 0)),
                       errors=str(sum(count for status, count in counts.items() if status not in {"PASS", "FAIL"})))
    for attempt in attempts:
        node = ET.SubElement(suite, "testcase", classname=attempt["case_id"], name=str(attempt["repeat_index"]))
        status = attempt.get("task_outcome", "NEEDS_REVIEW")
        if status != "PASS":
            ET.SubElement(node, "failure" if status == "FAIL" else "error", message=status).text = attempt.get("reason", status)
    if not attempts:
        suite.set("tests", "1")
        suite.set("errors", "1")
        ET.SubElement(ET.SubElement(suite, "testcase", name="unexecuted"), "error", message="No execution evidence")
    elif expected > len(attempts) or data.get("cancelled"):
        suite.set("tests", str(len(attempts) + 1))
        suite.set("errors", str(int(suite.get("errors", "0")) + 1))
        ET.SubElement(ET.SubElement(suite, "testcase", name="unexecuted"), "error", message="Partial or cancelled batch")
    ET.ElementTree(suite).write(directory / "results.xml", encoding="utf-8", xml_declaration=True)
    lines = ["# Agent 评测报告", "", f"门禁：{gate}。仅受控工程验证，未执行真实模型效果或 UI 验收。", ""]
    for row in attempts:
        lines.append(f"- {row['case_id']} / repeat {row['repeat_index']}：{row.get('task_outcome', 'NEEDS_REVIEW')}；"
                     f"cleanup={row.get('cleanup_status', 'UNKNOWN')}")
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
