"""Native Anthropic requests and streams are tested without contacting a provider."""

import json

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from app.config import ModelConfig
from app.errors import APIError
from app.graphs import resolve_model
from app.models import ModelInput


@pytest.mark.parametrize("field,value", [("kind", "unknown"), ("provider", "unknown"), ("protocol", "unknown")])
def test_unrecognized_model_classification_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ModelInput(**{"name": "test", "model": "test", "base_url": "http://localhost/v1", field: value})


def test_unadapted_native_protocol_is_not_sent_to_chat_completions() -> None:
    with pytest.raises(APIError):
        resolve_model(ModelConfig(id="test", name="test", model="test", base_url="http://localhost", protocol="custom"))


async def test_anthropic_uses_native_headers_endpoint_and_sse(monkeypatch) -> None:
    message = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "protocol-test",
        "content": [{"type": "text", "text": "原生协议已接通"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 5},
    }
    observed = []

    def response(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        body = json.loads(request.content)
        assert request.url.path == "/v1/messages"
        assert request.headers["authorization"] == "Bearer native-test-secret"
        assert "anthropic-version" in request.headers
        assert body["model"] == "protocol-test"
        if not body.get("stream"):
            return httpx2.Response(200, json=message)
        events = [
            {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "原生协议已接通"}},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
            {"type": "message_stop"},
        ]
        text = "".join(f"event: {item['type']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n" for item in events)
        return httpx2.Response(200, text=text, headers={"content-type": "text/event-stream"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as client:
        monkeypatch.setattr("langchain_anthropic.chat_models._get_default_async_httpx_client", lambda **kwargs: client)
        config = ModelConfig(
            id="native",
            name="原生协议",
            model="protocol-test",
            provider="anthropic",
            protocol="anthropic",
            base_url="http://local.test",
            api_key=SecretStr("native-test-secret"),
            max_output_tokens=32,
        )
        model = resolve_model(config)
        assert (await model.ainvoke("你好")).content == "原生协议已接通"
        chunks = [chunk async for chunk in model.astream("你好")]
        assert "原生协议已接通" in str([chunk.content for chunk in chunks])
        assert len(observed) == 2


async def test_openai_image_content_is_sent_as_image_url():
    import httpx
    from langchain_openai import ChatOpenAI

    observed = []

    async def handler(request):
        observed.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "image-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "已读取图片"}}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = ChatOpenAI(
            model="test", base_url="https://unit.invalid/v1", api_key=SecretStr("test"), http_async_client=client
        )
        result = await model.ainvoke(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
                    ],
                }
            ]
        )
    assert result.content == "已读取图片"
    assert observed[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_anthropic_converts_image_url_into_native_image_block(monkeypatch):
    observed = []

    async def handler(request):
        observed.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "image-test",
                "type": "message",
                "role": "assistant",
                "model": "test",
                "content": [{"type": "text", "text": "已读取图片"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        monkeypatch.setattr("langchain_anthropic.chat_models._get_default_async_httpx_client", lambda **kwargs: client)
        model = resolve_model(
            ModelConfig(
                id="test",
                name="test",
                model="test",
                base_url="https://unit.invalid",
                protocol="anthropic",
                api_key=SecretStr("test"),
                supports_images=True,
            )
        )
        result = await model.ainvoke(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
                    ],
                }
            ]
        )
    assert result.content == "已读取图片"
    assert observed[0]["messages"][0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="},
    }
