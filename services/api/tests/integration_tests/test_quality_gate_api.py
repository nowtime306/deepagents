"""Private labels and publication gates using the actual API and isolated PostgreSQL."""

from uuid import UUID, uuid4

import pytest
from psycopg.errors import LockNotAvailable
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.knowledge_pins import snapshot


def test_gate_requires_complete_real_labels_and_blocks_changed_sources(client, monkeypatch):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    kb = connection.post("/api/v1/capabilities/knowledge", headers=ORIGIN,
                         json={"name": "质量门禁-"+uuid4().hex}).json()["id"]
    base = f"/api/v1/capabilities/knowledge/{kb}"
    document = connection.post(base+"/documents", headers=ORIGIN,
        files={"file": ("evidence.txt", "产品保修三年。".encode())}).json()["id"]
    config = {"enforce_publish": True, "minimum_questions": 50, "recall_target": .9,
              "citation_target": .95, "no_answer_target": .9, "revision": 0}
    saved = connection.put(base+"/quality/policy", headers=ORIGIN, json=config)
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert connection.put(base+"/quality/policy", headers=ORIGIN, json=config).status_code == 409
    created = connection.post("/api/v1/agents", headers=ORIGIN,
        json={"name": "质量助手-"+uuid4().hex, "model_id": "test", "knowledge_ids": [kb]}).json()["id"]
    publication = f"/api/v1/agents/{created}/publish"
    assert connection.post(publication, headers=ORIGIN, json={"revision": 1}).status_code == 409
    questions = [{"id": str(i), "question": "是否有证据"+str(i), "category": "no_answer" if i == 49 else "fact",
                  "unanswerable": i == 49, "expected": [] if i == 49 else
                  [{"document_id": document, "revision": 1, "chunk_ids": []}]} for i in range(50)]
    results = [{"id": str(i), "recall_at_10": None if i == 49 else 1,
                "no_answer_correct": True if i == 49 else None} for i in range(50)]
    async def pins():
        knowledge = await app.state.knowledge.owned(UUID(kb), UUID(owner))
        return await snapshot(app.state.db, knowledge)
    exp = uuid4()
    connection.portal.call(app.state.db.execute,
        "INSERT INTO knowledge_experiments(id,request_id,request_digest,owner_id,knowledge_id,dataset_snapshot,"
        "resource_snapshot,status,results) VALUES(%s,%s,'local',%s,%s,%s,%s,'SUCCEEDED',%s)",
        (exp, uuid4(), owner, kb, Jsonb({"name": "受控本机标签，不是真实业务验收", "dataset_type": "user",
         "questions": questions, "revision": 1}), Jsonb(connection.portal.call(pins)), Jsonb(results)))
    assert not connection.get(base+f"/quality/check/{exp}").json()["passed"]
    for i in range(50):
        response = connection.put(base+f"/quality/reviews/{exp}/{i}", headers=ORIGIN,
            json={"answer": "不具备证据，应拒答" if i == 49 else "根据文档保修三年。",
                  "cited_documents": [] if i == 49 else [document],
                  "citation_correct": None if i == 49 else True,
                  "refusal_correct": True if i == 49 else None, "revision": 0})
        assert response.status_code == 200, response.text
    checked = connection.get(base+f"/quality/check/{exp}").json()
    assert checked["passed"] and checked["reviewed_answers"] == 50 and checked["citation_denominator"] == 49
    from app import quality_gate

    original_guard = quality_gate.publication_guard

    async def guarded(transaction, *args):
        report = await original_guard(transaction, *args)
        # Independent writers cannot change policy, source or human labels between decision and commit.
        for table, key, value in (("knowledge_bases", "id", kb), ("knowledge_experiments", "id", exp),
                                  ("documents", "id", document)):
            async with app.state.db.pool.connection() as other:
                with pytest.raises(LockNotAvailable):
                    async with other.transaction():
                        await other.execute(f"SELECT {key} FROM {table} WHERE {key}=%s FOR UPDATE NOWAIT", (value,))
        return report

    monkeypatch.setattr(quality_gate, "publication_guard", guarded)
    assert connection.post(publication, headers=ORIGIN, json={"revision": 1}).status_code == 201
    assert connection.put(base+"/retrieval", headers=ORIGIN, json={"keyword_min_coverage": .4}).status_code == 200
    assert not connection.get(base+f"/quality/check/{exp}").json()["passed"]
    assert connection.post(publication, headers=ORIGIN, json={"revision": 1}).status_code == 409
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id='viewer' WHERE id=%s", (owner,))
    assert connection.put(base+"/quality/policy", headers=ORIGIN, json={**config, "revision": 1}).status_code == 403


def test_quality_policy_and_annotation_reject_invalid_scope(client):
    connection, _ = client
    unknown = f"/api/v1/capabilities/knowledge/{uuid4()}"
    assert connection.get(unknown+"/quality/policy").status_code == 404
    assert connection.get(unknown+f"/quality/reviews/{uuid4()}").status_code == 404
    response = connection.put(unknown+"/quality/policy", headers=ORIGIN,
                              json={"minimum_questions": 1, "enforce_publish": True})
    assert response.status_code == 422
