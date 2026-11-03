"""Current-revision retrieval, immutable provenance and atomic incremental updates."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client

from app.models import ModelInput


@pytest.fixture
def vector_model(client):
    connection, app = client
    identifier = connection.portal.call(
        app.state.models.save,
        ModelInput(
            name="文档向量替身-" + uuid4().hex[:12],
            model="local-vector",
            base_url="http://unused.invalid/v1",
            kind="embedding",
        ),
    )
    try:
        yield identifier
    finally:
        connection.portal.call(
            app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (identifier,)
        )


def setup(connection):
    result = connection.post("/api/v1/capabilities/knowledge", json={"name": "版本资料"}, headers=ORIGIN)
    assert result.status_code == 201
    return "/api/v1/capabilities/knowledge/" + result.json()["id"]


def upload(connection, path, content, *, revision=None):
    if revision is None:
        return connection.post(path + "/documents", files={"file": ("source.txt", content.encode())}, headers=ORIGIN)
    return connection.put(
        path, files={"file": ("source.txt", content.encode())}, data={"revision": revision}, headers=ORIGIN
    )


def test_chunk_policy_deduplication_and_permission_boundaries(client):
    connection, app = client
    base = setup(connection)
    assert (
        connection.put(
            base + "/processing", json={"chunk_size": 200, "chunk_overlap": 101, "revision": 1}, headers=ORIGIN
        ).status_code
        == 422
    )
    saved = connection.put(
        base + "/processing", json={"chunk_size": 200, "chunk_overlap": 40, "revision": 1}, headers=ORIGIN
    )
    assert saved.status_code == 200 and saved.json()["processing_revision"] == 2
    assert (
        connection.put(base + "/processing", json={"chunk_size": 300, "revision": 1}, headers=ORIGIN).status_code == 409
    )
    result = upload(connection, base, "数据来源" * 130)
    assert result.status_code == 201 and result.json()["chunks"] > 1
    document = result.json()["id"]
    duplicated = upload(connection, base, "数据来源" * 130)
    assert duplicated.json()["id"] == document and duplicated.json()["duplicate"]
    chunks = connection.get(f"{base}/documents/{document}/chunks").json()["items"]
    assert all(len(row["content"]) <= 200 for row in chunks)
    assert connection.get(base + "/documents").json()["items"][0]["chunks"] == len(chunks)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE knowledge_bases SET owner_id=(SELECT id FROM users WHERE id<>%s LIMIT 1) WHERE id=%s",
        (owner, base.rsplit("/", 1)[-1]),
    )
    assert connection.get(f"{base}/documents/{document}/chunks").status_code == 404
    assert connection.get(f"{base}/documents/{document}/revisions").status_code == 404
    assert upload(connection, f"{base}/documents/{document}", "伪造", revision=1).status_code == 404


def test_atomic_replace_keeps_history_excludes_old_retrieval_and_preserves_on_parse_failure(client):
    connection, _ = client
    base = setup(connection)
    document = upload(connection, base, "redidentifier 的价格是200元").json()["id"]
    target = f"{base}/documents/{document}"
    old = connection.get(target + "/chunks").json()["items"][0]
    updated = upload(connection, target, "blueidentifier 的价格是300元", revision=1)
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert upload(connection, target, "并发覆盖", revision=1).status_code == 409
    assert connection.post(base + "/search", json={"query": "redidentifier"}, headers=ORIGIN).json()["items"] == []
    latest = connection.post(base + "/search", json={"query": "blueidentifier"}, headers=ORIGIN).json()["items"][0]
    assert latest["document_revision"] == 2 and "300" in latest["text"]
    assert connection.get(target + "/chunks?revision=1").json()["items"][0] == old
    assert [row["revision"] for row in connection.get(target + "/revisions").json()["items"]] == [2, 1]
    failed = connection.put(target, files={"file": ("broken.txt", b"\xff\xfe")}, data={"revision": 2}, headers=ORIGIN)
    assert failed.status_code == 422
    saved = connection.get(base + "/documents").json()["items"][0]
    assert saved["revision"] == 2 and saved["status"] == "READY" and saved["last_parse_error"]
    assert "300" in connection.get(target + "/chunks").json()["items"][0]["content"]


def test_concurrent_updates_commit_only_one_revision(client):
    connection, _ = client
    base = setup(connection)
    document = upload(connection, base, "原始资料").json()["id"]
    target = f"{base}/documents/{document}"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda value: upload(connection, target, value, revision=1).status_code, ["新版甲", "新版乙"])
        )
    assert sorted(results) == [200, 409]
    revisions = connection.get(target + "/revisions").json()["items"]
    assert [row["revision"] for row in revisions] == [2, 1]


def test_model_revision_change_never_queries_old_vector_collection(client, vector_model):
    connection, app = client
    model = vector_model
    base_id = connection.post(
        "/api/v1/capabilities/knowledge", json={"name": "向量修订", "embedding_model_id": model}, headers=ORIGIN
    ).json()["id"]
    base = "/api/v1/capabilities/knowledge/" + base_id
    requests = []

    async def embedding(config, texts, *, meter=None):
        requests.append(config.revision)
        if meter:
            receipt = await meter.start(texts)
            await meter.finish(receipt, {"prompt_tokens": 6, "total_tokens": 6})
        return [[1.0, 0.0] for _ in texts]

    app.state.knowledge.embedder = embedding
    document = upload(connection, base, "适配文档").json()["id"]
    assert connection.post(base + "/search", json={"query": "semantic"}, headers=ORIGIN).json()["mode"] == "hybrid"
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET revision=revision+1 WHERE id=%s", (model,))
    changed = connection.post(base + "/search", json={"query": "适配文档"}, headers=ORIGIN).json()
    assert changed["mode"] == "keyword" and changed["warning"] and requests == [1, 1]
    assert connection.post(f"{base}/documents/{document}/reindex", headers=ORIGIN).status_code == 200
    assert requests[-1] == 2
    assert connection.post(base + "/search", json={"query": "semantic"}, headers=ORIGIN).json()["mode"] == "hybrid"
    assert connection.delete(base, headers=ORIGIN).status_code == 200
    collections = connection.portal.call(app.state.knowledge.vectors.get_collections)
    assert not any(row.name.startswith("kb_" + base_id) for row in collections.collections)
