"""Review-only private patch retains F06's full four-root formal HTTP/ready behavior."""

import json

from test_fixed_parallel_profiles import ParallelFixture
from test_w02_pipeline import linked as configured_run

from evaluation_runner import fixed_audit

linked = configured_run


def test_f06_http_initialization_execution_cleanup_ready_and_no_replay_remain_intact(tmp_path, monkeypatch, linked):
    import test_fixed_workflow_profiles as existing

    monkeypatch.setattr(existing, "fixed_run", linked)
    fixture = ParallelFixture()
    for action in ("init", "run", "cleanup", "report"):
        assert existing.command(tmp_path, monkeypatch, fixture, action) == 0
    assert len(fixture.workflows) == 3 and len(fixture.rows) == 4
    assert fixed_audit.validate_ready(tmp_path / "output")["gate"] == "PASS"
    saved = json.loads((tmp_path / "output/report.json").read_text(encoding="utf-8"))["attempts"][0]
    assert saved["fact_results"][0]["registered_id"] == "all_branches_join"
    assert saved["usage"]["calls"] == saved["usage"]["input_tokens"] == saved["usage"]["output_tokens"] == 0
    before = len(fixture.posts)
    assert existing.command(tmp_path, monkeypatch, fixture, "run") == 0
    assert len(fixture.posts) == before
