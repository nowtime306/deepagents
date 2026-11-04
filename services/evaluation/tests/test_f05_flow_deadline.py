"""Shared F05 deadline boundaries through real CLI/HTTP client with a controlled transport."""

import json
from types import SimpleNamespace

import pytest
from test_f05_flow_code import Fixture, command

from evaluation_runner import fixed_flow_code as client
from evaluation_runner.journal import Journal, atomic_json


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(ms=1_700_000_000_000)
    monkeypatch.setattr(client.time, "time_ns", lambda: value.ms * 1_000_000)
    return value


def saved(tmp_path):
    directory = tmp_path / "output"
    data = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
    return directory, data, data["attempts"][0]


def test_same_sealed_deadline_survives_init_run_and_offline_after_expiry(tmp_path, monkeypatch, clock):
    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    directory, data, attempt = saved(tmp_path)
    reference = attempt["flow_code_refs"]["deadline"]
    value = client.deadline_value(directory, data, attempt)
    assert {key: value[key] for key in ("started_at_ms", "expires_at_ms", "limit_seconds")} == {
        "started_at_ms": clock.ms, "expires_at_ms": clock.ms + 300_000, "limit_seconds": 300}
    clock.ms += 299_000
    recorded = []
    original = fixture.server

    def observe(request):
        if request.method == "POST":
            recorded.append(request.extensions["timeout"])
        return original(request)

    fixture.server = observe
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    assert recorded and all(all(0 < timeout <= 1 for timeout in row.values()) for row in recorded)
    assert saved(tmp_path)[2]["flow_code_refs"]["deadline"] == reference
    clock.ms += 1_000
    assert command(tmp_path, monkeypatch, fixture, "cleanup") == 0
    assert command(tmp_path, monkeypatch, fixture, "report") == 0
    assert len(fixture.roots) == 3 and len(fixture.sessions) == 10


def test_expired_run_in_new_cli_has_no_new_root_or_approval_and_cannot_reset(tmp_path, monkeypatch, clock):
    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    reference = saved(tmp_path)[2]["flow_code_refs"]["deadline"]
    before = list(fixture.posts)
    clock.ms += 300_000
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert fixture.posts == before and len(fixture.roots) == 1
    assert saved(tmp_path)[2]["flow_code_refs"]["deadline"] == reference


@pytest.mark.parametrize("stage", ["model", "approval", "published-final"])
def test_expiry_after_local_save_blocks_the_actual_next_http_effect(tmp_path, monkeypatch, clock, stage):
    fixture = Fixture()
    original_save = Journal.save

    def save(journal):
        original_save(journal)
        attempt = next(iter(journal.data.get("attempts", [])), {})
        trigger = (stage == "model" and attempt.get("fixture_intents", {}).get("model-lead", {}).get("status") == "SUBMITTING"
                   or stage == "approval" and attempt.get("submission_intents", {}).get("f05-draft-decision-0", {}).get("status") == "SUBMITTING"
                   or stage == "published-final" and "published-final" in attempt.get("flow_code_refs", {}))
        if trigger:
            clock.ms = 1_700_000_300_000

    monkeypatch.setattr(Journal, "save", save)
    if stage == "published-final":
        assert command(tmp_path, monkeypatch, fixture, "init") == 0
        assert command(tmp_path, monkeypatch, fixture, "run") != 0
        assert len(fixture.roots) == 2
        assert len([path for path, _ in fixture.posts if path.endswith("/decisions")]) == 6
    else:
        assert command(tmp_path, monkeypatch, fixture, "init") != 0
        assert len(fixture.roots) == (0 if stage == "model" else 1)
        assert not any(path.endswith("/decisions") for path, _ in fixture.posts)
        if stage == "model":
            assert fixture.posts == []


def test_polling_expiry_does_not_approve_an_observed_pending_action(tmp_path, monkeypatch, clock):
    fixture = Fixture()
    original = fixture.server

    def expire(request):
        response = original(request)
        if request.method == "GET" and request.url.path.startswith("/api/v1/work-runs/") and request.url.path.count("/") == 4:
            clock.ms += 300_000
        return response

    fixture.server = expire
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert len(fixture.roots) == 1
    assert not any(path.endswith("/decisions") for path, _ in fixture.posts)


def test_missing_deadline_after_initialization_cannot_be_regenerated(tmp_path, monkeypatch, clock):
    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    directory, data, attempt = saved(tmp_path)
    del attempt["flow_code_refs"]["deadline"]
    atomic_json(directory / "journal.json", data)
    before = list(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert fixture.posts == before


def test_approval_response_arriving_after_deadline_cannot_advance_or_replay(tmp_path, monkeypatch, clock):
    fixture = Fixture()
    original = fixture.server

    def late(request):
        response = original(request)
        if request.method == "POST" and request.url.path.endswith("/decisions"):
            clock.ms += 300_000
        return response

    fixture.server = late
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert len([path for path, _ in fixture.posts if path.endswith("/decisions")]) == 1
    assert next(iter(fixture.roots.values()))["phase"] == 1
    assert saved(tmp_path)[2]["submission_intents"]["f05-draft-decision-0"]["status"] == "SUBMITTING"
    before = list(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert fixture.posts == before


def test_deadline_save_failure_prevents_even_model_creation(tmp_path, monkeypatch, clock):
    fixture = Fixture()
    original = Journal.save

    def fail(journal):
        if any("deadline" in row.get("flow_code_refs", {}) for row in journal.data.get("attempts", [])):
            raise OSError("controlled deadline persistence failure")
        original(journal)

    monkeypatch.setattr(Journal, "save", fail)
    with pytest.raises(OSError, match="controlled deadline persistence failure"):
        command(tmp_path, monkeypatch, fixture, "init")
    assert fixture.posts == [] and fixture.roots == {}


def test_clock_rollback_cannot_extend_shared_deadline(tmp_path, monkeypatch, clock):
    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    clock.ms -= 1
    before = list(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert fixture.posts == before
