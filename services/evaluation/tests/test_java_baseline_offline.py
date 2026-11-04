"""Portable synthetic HTTP fixtures; no historical .local reports or real services."""
import json
from types import SimpleNamespace

import pytest
from test_w02_pipeline import fixture

from evaluation_runner import fixed_java_client as java
from evaluation_runner.fixed_evidence import load, store

pytest_plugins = ["test_w02_pipeline"]


@pytest.mark.parametrize("fault", [None, "missing-baseline", "wrong-baseline", "different-jdk"])
def test_portable_same_offline_reader_and_clean_require_baseline(tmp_path, monkeypatch, linked, fault):
    command, output, _, module, _ = fixture(tmp_path, monkeypatch, linked)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(case for case in module.catalog().cases if case.case_id == "AET-W02")
    original = attempt["java_final_ref"]
    value = load(output, original, "java-final")
    result = value["body"]["root"]["result"]
    if fault == "missing-baseline": del result["baseline_verification"]
    elif fault == "wrong-baseline": result["baseline_verification"]["source_sha256"] = "c" * 64
    elif fault == "different-jdk": result["baseline_verification"]["javac_version_sha256"] = "0" * 64
    reference = store(output, "java-final", value)
    attempt["java_final_ref"] = reference
    attempt["evidence_refs"][-1] = reference
    journal = SimpleNamespace(directory=output, data=data)
    platform = SimpleNamespace(get=lambda _: value["body"]["observer"])
    if fault is None:
        assert java.verified(output, data, attempt, specimen)["verifier"]["status"] == "COMPLETED"
        assert java.cleanup_ready(output, data, attempt, specimen)
        assert java.cleanup(platform, journal, attempt, specimen) == "CLEAN"
    else:
        for method in (lambda: java.verified(output, data, attempt, specimen),
                       lambda: java.cleanup_ready(output, data, attempt, specimen),
                       lambda: java.cleanup(platform, journal, attempt, specimen)):
            with pytest.raises((ValueError, TypeError, RuntimeError)): method()
