"""Run 50 annotated synthetic questions through the real private knowledge service."""

import json
from pathlib import Path
from uuid import UUID, uuid4

from test_api import ORIGIN
from test_api import client as client

from app.rag_evaluation import evaluate


def test_annotated_retrieval_baseline(client):
    connection, app = client
    fixture = json.loads((Path(__file__).parents[4] / "docs/evals/rag-fixture.json").read_text(encoding="utf-8"))
    kb = connection.post(
        "/api/v1/capabilities/knowledge", json={"name": "回归语料-" + str(uuid4())}, headers=ORIGIN
    ).json()["id"]
    owner = connection.portal.call(app.state.db.fetch, "SELECT owner_id FROM knowledge_bases WHERE id=%s", (kb,))[0][
        "owner_id"
    ]
    documents = {}
    try:
        for document in fixture["documents"]:
            result = connection.post(
                f"/api/v1/capabilities/knowledge/{kb}/documents",
                files={"file": (document["name"], document["text"].encode())},
                headers=ORIGIN,
            )
            assert result.status_code == 201
            documents[document["id"]] = result.json()["id"]

        async def run():
            async def search(query):
                return await app.state.knowledge.search(UUID(kb), owner, query, limit=10)

            return await evaluate(search, fixture["questions"], documents)

        report = connection.portal.call(run)
        assert report["questions"] == 50 and report["citation_correctness"] is None
        assert report["recall_at_10"] >= 0.9
        assert report["no_answer_retrieval_accuracy"] >= 0.6
        assert connection.put(
            f"/api/v1/capabilities/knowledge/{kb}/retrieval",
            json={"keyword_min_coverage": 0.4}, headers=ORIGIN,
        ).status_code == 200
        strict = connection.portal.call(run)
        assert strict["no_answer_retrieval_accuracy"] == 1
        assert strict["recall_at_10"] < report["recall_at_10"]
        report["keyword_min_coverage"] = 0
        report["strict_comparison"] = {"keyword_min_coverage": 0.4, **strict}
        (app.state.settings.data_dir / "rag-keyword-baseline.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        connection.delete(f"/api/v1/capabilities/knowledge/{kb}", headers=ORIGIN)
