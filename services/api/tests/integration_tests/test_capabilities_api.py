"""Real database and Qdrant tests, with in-process protocol doubles where appropriate."""

from contextlib import asynccontextmanager
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from test_api import ORIGIN
from test_api import client as client


def test_knowledge_upload_search_binding_delete_and_isolation(client) -> None:
    connection, app = client
    created = connection.post("/api/v1/capabilities/knowledge", json={"name": "产品文档"}, headers=ORIGIN)
    assert created.status_code == 201
    identifier = created.json()["id"]
    uploaded = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/documents",
        files={"file": ("产品.md", "产品价格为 200 元，保修三年。".encode(), "text/markdown")},
        headers=ORIGIN,
    )
    assert uploaded.status_code == 201
    found = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/search", json={"query": "价格"}, headers=ORIGIN
    )
    assert found.json()["mode"] == "keyword"
    assert found.json()["items"][0]["name"] == "产品.md"
    assert "200" in found.json()["items"][0]["text"]
    assert (
        connection.put(
            "/api/v1/capabilities/bindings", json={"knowledge_ids": [identifier]}, headers=ORIGIN
        ).status_code
        == 200
    )
    conversation = connection.post("/api/v1/conversations", json={"title": "绑定测试"}, headers=ORIGIN).json()["id"]
    tools, middleware = connection.portal.call(app.state.capabilities.for_conversation, conversation)
    assert not middleware
    results = connection.portal.call(tools[0].ainvoke, {"query": "价格"})
    assert "200" in results
    document = uploaded.json()["id"]
    assert (
        connection.delete(
            f"/api/v1/capabilities/knowledge/{identifier}/documents/{document}", headers=ORIGIN
        ).status_code
        == 200
    )
    assert not connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/search", json={"query": "价格"}, headers=ORIGIN
    ).json()["items"]
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE knowledge_bases SET owner_id=(SELECT id FROM users WHERE id<>%s LIMIT 1) WHERE id=%s",
        (owner, identifier),
    )
    assert connection.get(f"/api/v1/capabilities/knowledge/{identifier}/documents").status_code == 404


def test_native_skill_middleware_is_loaded_and_reads_selected_resource(client) -> None:
    connection, app = client
    source = b"---\nname: source-skill\ndescription: Explain a report\n---\n\n# Use this procedure\n"
    uploaded = connection.post("/api/v1/capabilities/skills", files={"file": ("SKILL.md", source)}, headers=ORIGIN)
    assert uploaded.status_code == 201
    identifier = uploaded.json()["id"]

    class SkillModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    app.state.runtime.model_factory = lambda _: SkillModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_file", "args": {"file_path": "/skills/source-skill/SKILL.md"}, "id": "read-skill"}
                ],
            ),
            AIMessage(content="技能读取完成"),
        ]
    )
    assert (
        connection.put("/api/v1/capabilities/bindings", json={"skill_ids": [identifier]}, headers=ORIGIN).status_code
        == 200
    )
    conversation = connection.post("/api/v1/conversations", json={"title": "技能测试"}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"message": "使用技能说明", "model_id": "test"},
        headers=ORIGIN,
    ).json()["run_id"]
    events = connection.get(f"/api/v1/runs/{run}/events").text
    assert "run.completed" in events
    checkpoint = connection.portal.call(
        app.state.runtime.saver.aget_tuple, {"configurable": {"thread_id": conversation}}
    )
    metadata = checkpoint.checkpoint["channel_values"]["skills_metadata"]
    assert metadata[0]["name"] == "source-skill"
    messages = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
    assert any(item["role"] == "tool" and "Use this procedure" in str(item["content"]) for item in messages)


def test_mcp_discovery_enable_and_real_tool_adapter(client) -> None:
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    result = connection.post(
        "/api/v1/capabilities/mcp",
        json={
            "name": f"本地-{uuid4()}",
            "url": "http://127.0.0.1:8791/mcp",
            "allow_private": True,
            "token": "test-secret",
        },
        headers=ORIGIN,
    )
    assert result.status_code == 201
    identifier = result.json()["id"]

    class Session:
        async def list_tools(self):
            return ListToolsResult(
                tools=[
                    Tool(
                        name="lookup",
                        description="读取资料",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                        annotations=ToolAnnotations(read_only_hint=True),
                    ),
                    Tool(name="delete", input_schema={"type": "object"}),
                ]
            )

        async def call_tool(self, name, arguments):
            return CallToolResult(content=[TextContent(type="text", text=f"结果：{arguments['query']}")])

    @asynccontextmanager
    async def session(server):
        yield Session()

    app.state.mcp.session = session
    discovered = connection.post(f"/api/v1/capabilities/mcp/{identifier}/discover", headers=ORIGIN)
    assert discovered.status_code == 200
    assert (
        connection.put(
            f"/api/v1/capabilities/mcp/{identifier}/tools", json={"names": ["delete"]}, headers=ORIGIN
        ).status_code
        == 422
    )
    assert (
        connection.put(
            f"/api/v1/capabilities/mcp/{identifier}/tools", json={"names": ["lookup"]}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert (
        connection.put("/api/v1/capabilities/bindings", json={"mcp_ids": [identifier]}, headers=ORIGIN).status_code
        == 200
    )
    conversation = connection.post("/api/v1/conversations", json={"title": "工具绑定"}, headers=ORIGIN).json()["id"]
    tools, _ = connection.portal.call(app.state.capabilities.for_conversation, conversation)
    response = connection.portal.call(tools[0].ainvoke, {"query": "产品"})
    assert "产品" in response
    listing = connection.get("/api/v1/capabilities/mcp").text
    assert "test-secret" not in listing
    connection.delete(f"/api/v1/capabilities/mcp/{identifier}", headers=ORIGIN)


def test_qdrant_hybrid_indexing_with_deterministic_embedding(client) -> None:
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    identifier = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO knowledge_bases(id,owner_id,name,embedding_model_id) VALUES (%s,%s,'向量测试','test')",
        (identifier, owner),
    )

    async def embedding(config, texts, *, meter=None):
        if meter:
            receipt = await meter.start(texts)
            await meter.finish(receipt, {"prompt_tokens": 6, "total_tokens": 6})
        return [[1.0, 0.0] if "产品" in text else [0.0, 1.0] for text in texts]

    app.state.knowledge.embedder = embedding
    uploaded = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/documents",
        files={"file": ("vector.txt", "产品保修三年".encode())},
        headers=ORIGIN,
    )
    assert uploaded.status_code == 201
    result = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/search", json={"query": "产品保障"}, headers=ORIGIN
    ).json()
    assert result["mode"] == "hybrid" and result["items"]
    connection.delete(f"/api/v1/capabilities/knowledge/{identifier}", headers=ORIGIN)


def test_official_mcp_sdk_http_transport_roundtrip(client) -> None:
    import httpx2
    from mcp.server.mcpserver import MCPServer
    from psycopg.types.json import Jsonb

    connection, app = client
    server = MCPServer("protocol-test")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def lookup(query: str) -> str:
        return f"协议查询结果：{query}"

    identifier = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO mcp_servers(id,name,url,allow_private) VALUES (%s,%s,'http://127.0.0.1:8792/mcp',true)",
        (identifier, f"wire-{identifier}"),
    )
    protocol_app = server.streamable_http_app(stateless_http=True, json_response=True)
    original = app.state.mcp.http_client_factory

    async def exercise():
        async with protocol_app.router.lifespan_context(protocol_app):
            app.state.mcp.http_client_factory = lambda headers: httpx2.AsyncClient(
                headers=headers, timeout=5, transport=httpx2.ASGITransport(app=protocol_app)
            )
            row = (await app.state.db.fetch("SELECT * FROM mcp_servers WHERE id=%s", (identifier,)))[0]
            tools = await app.state.mcp.discover(row)
            assert tools[0]["name"] == "lookup" and tools[0]["read_only"]
            tools[0]["enabled"] = True
            await app.state.db.execute("UPDATE mcp_servers SET tools=%s WHERE id=%s", (Jsonb(tools), identifier))
            result = await app.state.mcp.invoke(identifier, "lookup", {"query": "产品"})
            assert "协议查询结果" in result and "产品" in result

    try:
        connection.portal.call(exercise)
    finally:
        app.state.mcp.http_client_factory = original
        connection.portal.call(app.state.db.execute, "DELETE FROM mcp_servers WHERE id=%s", (identifier,))


def test_xlsx_retrieval_preserves_sheet_and_cell_evidence(client):
    import io

    from openpyxl import Workbook

    connection, _ = client
    book = Workbook()
    book.active.title = "报价"
    book.active.append(["产品", "价格"])
    book.active.append(["测试设备", 1288])
    book.active.append(["公式说明", "=B2*2"])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    kb = connection.post("/api/v1/capabilities/knowledge", json={"name": "表格验收"}, headers=ORIGIN).json()["id"]
    response = connection.post(
        f"/api/v1/capabilities/knowledge/{kb}/documents",
        files={"file": ("报价.xlsx", stream.getvalue())},
        headers=ORIGIN,
    )
    assert response.status_code == 201
    result = connection.post(f"/api/v1/capabilities/knowledge/{kb}/search", json={"query": "测试设备"}, headers=ORIGIN)
    assert result.status_code == 200
    assert "报价" in result.text and "B2: 1288" in result.text
    assert "第 2 行" in result.text
    connection.delete(f"/api/v1/capabilities/knowledge/{kb}", headers=ORIGIN)
