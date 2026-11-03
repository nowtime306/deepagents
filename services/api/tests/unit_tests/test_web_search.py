"""Network search uses explicit authorization and returns bounded sources."""

import json
from uuid import uuid4

import httpx
import pytest

from app.errors import APIError
from app.models import CredentialVault
from app.web_search import search_tool


async def test_search_sends_request_and_honors_revocation(monkeypatch, tmp_path) -> None:
    vault = CredentialVault(tmp_path / "key")

    class Database:
        allowed = True

        async def fetch(self, sql, params=()):
            if "FROM agents" in sql:
                return [{"ok": 1}] if self.allowed else []
            return [{"api_key_ciphertext": vault.encrypt("search-test-secret")}]

    db = Database()
    sent = []

    def response(request):
        sent.append(request)
        assert request.url == "https://api.tavily.com/search"
        assert request.headers["authorization"] == "Bearer search-test-secret"
        assert json.loads(request.content)["query"] == "产品动态"
        return httpx.Response(
            200, json={"results": [{"title": "来源", "url": "https://example.com", "content": "公开摘要"}]}
        )

    factory = httpx.AsyncClient
    monkeypatch.setattr(
        "app.web_search.httpx.AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(response))
    )
    tool = search_tool(db, vault, uuid4(), uuid4())
    output = await tool.ainvoke({"query": "产品动态"})
    assert "https://example.com" in output and "公开摘要" in output and "search-test-secret" not in output
    db.allowed = False
    with pytest.raises(APIError):
        await tool.ainvoke({"query": "产品动态"})
    assert len(sent) == 1
