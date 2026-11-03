"""Finite audio/video adapter fee semantics, using only explicit usage and local estimates."""

from decimal import Decimal
from uuid import uuid4

import httpx
from test_api import ORIGIN
from test_api import client as client

from app.config import ModelConfig
from app.extra_cost_api import router
from app.extra_cost_media import ReportedText
from app.speech_api import transcribe_audio


def setup(client, kind, *, provider="custom", amount="10"):
    c, app = client
    app.include_router(router)
    owner = c.get("/api/v1/auth/me").json()["id"]
    c.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    model = str(uuid4())
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol,provider) "
        "VALUES(%s,%s,'local','http://unused','',%s,'openai',%s)",
        (model, model, kind, provider),
    )
    unit = "video_second" if kind == "video" else "audio_second"
    assert (
        c.post(
            "/api/v1/operations/extra-costs/prices",
            headers=ORIGIN,
            json={
                "model_id": model,
                "item": "seconds",
                "unit": unit,
                "currency": "CNY",
                "unit_price": "0.2",
                "source": "local contract",
                "effective_at": "2020-01-01T00:00:00Z",
            },
        ).status_code
        == 201
    )
    assert (
        c.put("/api/v1/billing/budgets/CNY", headers=ORIGIN, json={"amount": amount, "period_days": 30}).status_code
        == 200
    )
    return c, app, model


def test_audio_reported_seconds_settle_and_video_estimate_stays_unknown(client, monkeypatch):
    c, app, model = setup(client, "speech_to_text")

    async def estimate(*args):
        return Decimal(10)

    async def transcription(*args):
        return ReportedText("local transcription", Decimal(8))

    monkeypatch.setattr("app.extra_cost_media.duration", estimate)
    monkeypatch.setattr("app.speech_api.transcribe_audio", transcription)
    result = c.post(
        "/api/v1/speech/transcriptions",
        headers=ORIGIN,
        data={"model_id": model},
        files={"file": ("a.wav", b"RIFF0000WAVEfixture")},
    )
    assert result.status_code == 200, result.text
    rows = c.portal.call(
        app.state.db.fetch,
        "SELECT h.* FROM extra_cost_holds h JOIN extra_cost_prices p ON p.id=h.price_id WHERE p.model_id=%s",
        (model,),
    )
    assert len(rows) == 1 and rows[0]["reserved"] == Decimal(2) and rows[0]["actual"] == Decimal("1.6")


def test_dashscope_video_no_billed_seconds_keeps_hold_and_blocks_next_call(client, monkeypatch):
    c, app, model = setup(client, "video", provider="dashscope", amount="3")
    calls = []

    async def estimate(*args):
        return Decimal(10)

    async def video(*args):
        calls.append(1)
        return "local video answer"

    monkeypatch.setattr("app.extra_cost_media.duration", estimate)
    monkeypatch.setattr("app.vision_api.understand_video", video)
    data = {"model_id": model, "question": "local"}
    file = {"file": ("a.mp4", b"0000ftypmp42fixture")}
    first = c.post("/api/v1/vision/video", headers=ORIGIN, data=data, files=file)
    assert first.status_code == 200, first.text
    rows = c.portal.call(
        app.state.db.fetch,
        "SELECT h.* FROM extra_cost_holds h JOIN extra_cost_prices p ON p.id=h.price_id WHERE p.model_id=%s",
        (model,),
    )
    assert len(rows) == 1 and rows[0]["reserved"] == Decimal(2) and rows[0]["actual"] is None
    assert c.post("/api/v1/vision/video", headers=ORIGIN, data=data, files=file).status_code == 422
    assert len(calls) == 1


async def test_actual_openai_transcription_duration_contract(monkeypatch):
    original = httpx.AsyncClient

    async def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(200, json={"text": "fixture", "usage": {"type": "duration", "seconds": 8}})

    monkeypatch.setattr(
        "app.speech_api.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler))
    )
    text = await transcribe_audio(
        ModelConfig(id="a", name="a", model="local", base_url="http://fixture/v1"), b"fixture", ".wav"
    )
    assert text == "fixture" and text.billed_seconds == Decimal(8)
