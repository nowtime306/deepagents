"""Evidence settings preserve dense recall and provide explicit source-free responses."""

from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client


def test_no_evidence_and_settings_are_explicit_and_validated(client):
    connection, app = client
    identifier = connection.post("/api/v1/capabilities/knowledge", json={"name": "证据边界"}, headers=ORIGIN).json()[
        "id"
    ]
    connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/documents",
        files={"file": ("资料.md", "平台服务价格为每月29元，API Key 不回显。".encode())},
        headers=ORIGIN,
    )
    assert (
        connection.put(
            f"/api/v1/capabilities/knowledge/{identifier}/retrieval", json={"keyword_min_coverage": 1.1}, headers=ORIGIN
        ).status_code
        == 422
    )
    assert (
        connection.put(
            f"/api/v1/capabilities/knowledge/{identifier}/retrieval",
            json={"keyword_min_coverage": 0.25},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    absent = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/search",
        json={"query": "平台创始人的身高是多少？"},
        headers=ORIGIN,
    ).json()
    assert absent["evidence_status"] == "no_evidence" and absent["items"] == []
    assert absent["keyword_filtered"] == 1 and "不得" in absent["guidance"]
    found = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/search", json={"query": "API Key"}, headers=ORIGIN
    ).json()
    assert found["evidence_status"] == "candidates" and found["items"][0]["keyword_coverage"] == 1
    assert found["score_type"] == "rrf" and found["items"][0]["vector_score"] is None


def test_dense_candidates_survive_zero_keyword_overlap(client):
    connection, app = client
    user = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (user["id"],))
    model = connection.post(
        "/api/v1/admin/models",
        json={
            "name": "本机向量替身-" + str(uuid4()),
            "model": "stub",
            "kind": "embedding",
            "base_url": "http://unused.invalid/v1",
        },
        headers=ORIGIN,
    ).json()["id"]

    async def embed(config, texts, *, meter=None):
        if meter:
            receipt = await meter.start(texts)
            await meter.finish(receipt, {"prompt_tokens": 6, "total_tokens": 6})
        return [[1.0, 0.0] for _ in texts]

    app.state.knowledge.embedder = embed
    identifier = connection.post(
        "/api/v1/capabilities/knowledge", json={"name": "语义证据", "embedding_model_id": model}, headers=ORIGIN
    ).json()["id"]
    try:
        connection.post(
            f"/api/v1/capabilities/knowledge/{identifier}/documents",
            files={"file": ("假期.txt", "年假申请需要经理批准".encode())},
            headers=ORIGIN,
        )
        connection.put(
            f"/api/v1/capabilities/knowledge/{identifier}/retrieval", json={"keyword_min_coverage": 1}, headers=ORIGIN
        )
        result = connection.post(
            f"/api/v1/capabilities/knowledge/{identifier}/search", json={"query": "vacation approval"}, headers=ORIGIN
        ).json()
        assert result["mode"] == "hybrid" and result["items"]
        assert result["items"][0]["keyword_coverage"] == 0
        assert result["items"][0]["keyword_score"] is None and result["items"][0]["vector_score"] > 0.9
    finally:
        connection.delete(f"/api/v1/capabilities/knowledge/{identifier}", headers=ORIGIN)
        connection.delete(f"/api/v1/admin/models/{model}", headers=ORIGIN)
