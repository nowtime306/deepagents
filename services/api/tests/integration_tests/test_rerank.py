"""Knowledge reranking preserves source IDs and fallback behavior."""

from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client


def test_knowledge_rerank_and_fallback(client) -> None:
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    model = connection.post(
        "/api/v1/admin/models",
        json={"name": f"重排测试-{uuid4()}", "model": "test", "kind": "rerank", "base_url": "http://local.test/v1"},
        headers=ORIGIN,
    ).json()["id"]
    knowledge = connection.post("/api/v1/capabilities/knowledge", json={"name": "重排知识"}, headers=ORIGIN).json()[
        "id"
    ]
    try:
        for name, contents in [("a.txt", "产品价格为200元"), ("b.txt", "产品保修三年")]:
            assert (
                connection.post(
                    f"/api/v1/capabilities/knowledge/{knowledge}/documents",
                    files={"file": (name, contents.encode())},
                    headers=ORIGIN,
                ).status_code
                == 201
            )
        assert (
            connection.put(
                f"/api/v1/capabilities/knowledge/{knowledge}/rerank", json={"rerank_model_id": model}, headers=ORIGIN
            ).status_code
            == 200
        )

        async def reverse(config, query, documents, *, meter=None):
            if meter:
                receipt = await meter.start([query, *documents])
                await meter.finish(receipt, {"meta": {"tokens": {"input_tokens": 8, "output_tokens": 0}}})
            # A provider may return indexed results out of score order.
            return [(index, float(index)) for index in range(len(documents))]

        app.state.knowledge.reranker = reverse
        result = connection.post(
            f"/api/v1/capabilities/knowledge/{knowledge}/search", json={"query": "产品"}, headers=ORIGIN
        ).json()
        assert result["reranked"] and len(result["items"]) == 2
        assert result["items"][0]["score"] == 1.0
        assert all(item["chunk_id"] and item["name"] for item in result["items"])

        async def unavailable(config, query, documents, *, meter=None):
            raise RuntimeError("offline")

        app.state.knowledge.reranker = unavailable
        result = connection.post(
            f"/api/v1/capabilities/knowledge/{knowledge}/search", json={"query": "产品"}, headers=ORIGIN
        ).json()
        assert not result["reranked"] and "重排暂不可用" in result["warning"] and len(result["items"]) == 2
    finally:
        connection.delete(f"/api/v1/capabilities/knowledge/{knowledge}", headers=ORIGIN)
        connection.delete(f"/api/v1/admin/models/{model}", headers=ORIGIN)
