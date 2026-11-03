"""OpenAI native web search uses Responses and preserves citations."""

import json
from uuid import uuid4

import httpx
import pytest
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from test_api import ORIGIN
from test_api import client as client

from app.graphs import build_ask


@pytest.mark.asyncio
async def test_native_search_graph_requests_responses_and_preserves_citations():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "test",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "新闻内容。",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.org/news",
                                        "title": "来源",
                                        "start_index": 0,
                                        "end_index": 4,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        model = ChatOpenAI(
            model="test", api_key=SecretStr("test-secret"), base_url="https://example.test/v1", http_async_client=http
        )
        graph = build_ask(model, InMemorySaver(), tools=[{"type": "web_search"}])
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "新闻"}]}, {"configurable": {"thread_id": "1"}}
        )
    assert calls[0].url.path == "/v1/responses"
    assert {tool["type"] for tool in json.loads(calls[0].content)["tools"]} >= {"web_search", "function"}
    assert "https://example.org/news" in str(result["messages"][-1].content)
    assert result["messages"][-1].content[0]["type"] == "text"


def test_native_mode_validation_and_snapshot(client):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol,provider) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "test", "http://unused", "", "chat", "openai", "openai"),
    )
    body = {"name": f"原生联网-{uuid4()}", "model_id": model_id, "web_enabled": True, "web_mode": "openai_native"}
    assert connection.post("/api/v1/agents", json={**body, "model_id": "test"}, headers=ORIGIN).status_code == 422
    result = connection.post("/api/v1/agents", json=body, headers=ORIGIN)
    assert result.status_code == 201
    identifier = result.json()["id"]
    assert (
        next(a for a in connection.get("/api/v1/agents").json()["items"] if a["id"] == identifier)["web_mode"]
        == "openai_native"
    )
    # An older client that omits web_mode preserves the setting.
    assert (
        connection.put(
            f"/api/v1/agents/{identifier}",
            json={k: v for k, v in {**body, "revision": 1}.items() if k != "web_mode"},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert (
        next(a for a in connection.get("/api/v1/agents").json()["items"] if a["id"] == identifier)["web_mode"]
        == "openai_native"
    )
    conversation = connection.post("/api/v1/conversations", json={"agent_id": identifier}, headers=ORIGIN).json()["id"]
    # A deterministic graph double covers snapshot persistence without contacting OpenAI.
    response = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"message": "查新闻", "model_id": model_id}, headers=ORIGIN
    )
    assert response.status_code == 202
    snapshot = connection.portal.call(
        app.state.db.fetch, "SELECT agent_snapshot FROM task_runs WHERE id=%s", (response.json()["run_id"],)
    )[0]["agent_snapshot"]
    assert snapshot["web_mode"] == "openai_native"
    connection.get(f"/api/v1/runs/{response.json()['run_id']}/events")
    assert connection.delete(f"/api/v1/conversations/{conversation}", headers=ORIGIN).status_code == 200
    assert connection.delete(f"/api/v1/agents/{identifier}", headers=ORIGIN).status_code == 200
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))
