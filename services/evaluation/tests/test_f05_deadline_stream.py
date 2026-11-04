"""Actual HTTPX body consumption crosses the F05 deadline; no network or physical task."""

from types import SimpleNamespace

import httpx
import pytest
from test_f05_flow_code import Fixture, command
from test_f05_flow_deadline import saved

from evaluation_runner import fixed_flow_code as client


@pytest.mark.parametrize("effect,path,key,collection", [
    ("model", "/api/v1/admin/models", "model-lead", "fixture_intents"),
    ("approval", "/decisions", "f05-draft-decision-0", "submission_intents"),
])
def test_body_after_on_time_headers_cannot_confirm_effect_or_advance(tmp_path, monkeypatch, effect, path, key, collection):
    clock = SimpleNamespace(ms=1_700_000_000_000)
    monkeypatch.setattr(client.time, "time_ns", lambda: clock.ms * 1_000_000)
    fixture = Fixture()
    original = fixture.server
    streams = []

    class SlowBody(httpx.SyncByteStream):
        def __init__(self, raw):
            self.raw = raw
            self.closed = False

        def __iter__(self):
            clock.ms += 300_000  # The response hook has already run; only the body is late.
            yield self.raw

        def close(self):
            self.closed = True

    def response(request):
        result = original(request)
        if request.method == "POST" and request.url.path.endswith(path):
            stream = SlowBody(result.content)
            streams.append(stream)
            return httpx.Response(result.status_code, stream=stream)
        return result

    fixture.server = response
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert saved(tmp_path)[2][collection][key]["status"] == "SUBMITTING"
    assert streams and all(stream.closed for stream in streams)
    if effect == "model":
        assert [posted for posted, _ in fixture.posts] == [path]
        assert fixture.roots == {}
    else:
        assert len([posted for posted, _ in fixture.posts if posted.endswith(path)]) == 1
        assert next(iter(fixture.roots.values()))["phase"] == 1
    before = list(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert fixture.posts == before


@pytest.mark.parametrize("effect,key,collection", [
    ("model", "model-lead", "fixture_intents"),
    ("approval", "f05-draft-decision-0", "submission_intents"),
])
def test_json_parsing_crossing_deadline_is_checked_before_confirming(tmp_path, monkeypatch, effect, key, collection):
    clock = SimpleNamespace(ms=1_700_000_000_000)
    monkeypatch.setattr(client.time, "time_ns", lambda: clock.ms * 1_000_000)
    fixture = Fixture()
    original = httpx.Response.json

    def late_json(response, *args, **kwargs):
        value = original(response, *args, **kwargs)
        if (effect == "model" and value == {"id": fixture.model}
                or effect == "approval" and value == {"decision": "approve"}):
            clock.ms += 300_000
        return value

    monkeypatch.setattr(httpx.Response, "json", late_json)
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert saved(tmp_path)[2][collection][key]["status"] == "SUBMITTING"
    assert len(fixture.roots) == (0 if effect == "model" else 1)
    assert len([path for path, _ in fixture.posts if path.endswith("/decisions")]) == (0 if effect == "model" else 1)
