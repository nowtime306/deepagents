"""Check rerank request and reject incomplete provider responses."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import ModelConfig
from app.errors import APIError
from app.knowledge import rerank_texts


async def test_rerank_sends_query_and_documents_without_exposing_key(monkeypatch) -> None:
    def handle(request):
        assert request.url.path == "/v1/rerank"
        assert request.headers["authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert body["query"] == "保修" and len(body["documents"]) == 2
        return httpx.Response(
            200, json={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}
        )

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        "app.knowledge.httpx.AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(handle))
    )
    config = ModelConfig(
        id="test", name="test", model="test", kind="rerank", base_url="http://localhost/v1", api_key=SecretStr("secret")
    )
    assert await rerank_texts(config, "保修", ["天气", "保修三年"]) == [(1, 0.9), (0, 0.1)]

    def invalid(request):
        return httpx.Response(200, json={"results": [{"index": 1, "relevance_score": 0.9}]})

    monkeypatch.setattr(
        "app.knowledge.httpx.AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(invalid))
    )
    with pytest.raises(APIError):
        await rerank_texts(config, "保修", ["天气", "保修三年"])
