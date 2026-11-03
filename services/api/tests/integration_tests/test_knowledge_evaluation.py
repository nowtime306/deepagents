"""Private immutable evaluations, durable leases and real local resource billing."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client
from test_knowledge_usage import provider as provider
from test_knowledge_usage import upload

from app.evaluation_worker import EvaluationWorker


def dataset(connection, knowledge, document, *, name="检索对照题集"):
    body = {
        "name": name,
        "dataset_type": "user",
        "questions": [
            {
                "id": "price",
                "question": "产品价格",
                "category": "fact",
                "expected": [{"document_id": document, "revision": 1}],
            },
            {"id": "empty", "question": "absentidentifier", "category": "no_answer", "unanswerable": True},
        ],
    }
    result = connection.post(f"/api/v1/capabilities/knowledge/{knowledge}/datasets", json=body, headers=ORIGIN)
    assert result.status_code == 201, result.text
    return result.json()["id"], body


def enqueue(connection, knowledge, dataset, **extra):
    body = {"dataset_id": dataset, "dataset_revision": 1, "request_id": str(uuid4()), **extra}
    result = connection.post(f"/api/v1/capabilities/knowledge/{knowledge}/experiments", json=body, headers=ORIGIN)
    return result, body


async def perform(app, identifier):
    token = uuid4()
    row = (
        await app.state.db.fetch(
            "UPDATE knowledge_experiments SET status='RUNNING',lease_token=%s,lease_until=now()+interval '90 seconds' "
            "WHERE id=%s AND status='PENDING' RETURNING *",
            (token, identifier),
        )
    )[0]
    await EvaluationWorker(app.state.db, app.state.knowledge).perform(row)


def keyword(connection):
    knowledge = connection.post("/api/v1/capabilities/knowledge", json={"name": "关键词资料"}, headers=ORIGIN).json()[
        "id"
    ]
    document = upload(connection, knowledge)["id"]
    return knowledge, document


def test_keyword_metrics_snapshots_idempotency_and_cancelled_queue(client):
    connection, app = client
    knowledge, document = keyword(connection)
    source, body = dataset(connection, knowledge, document)
    result, request = enqueue(connection, knowledge, source)
    assert result.status_code == 202
    experiment = result.json()["id"]
    duplicate = connection.post(f"/api/v1/capabilities/knowledge/{knowledge}/experiments", json=request, headers=ORIGIN)
    assert duplicate.json()["id"] == experiment and duplicate.json()["duplicate"]
    conflict = connection.post(
        f"/api/v1/capabilities/knowledge/{knowledge}/experiments",
        json={**request, "dataset_revision": 2},
        headers=ORIGIN,
    )
    assert conflict.status_code == 409
    connection.portal.call(perform, app, experiment)
    record = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").json()["items"][0]
    assert record["status"] == "SUCCEEDED" and record["metrics"]["complete"]
    assert record["metrics"]["recall_at_10"] == 1 and record["metrics"]["no_answer_correct"] == 1
    assert record["metrics"]["citation_correctness"] is None
    assert record["metrics"]["categories"]["scanned"]["questions"] == 0
    assert record["metrics"]["categories"]["scanned"]["recall_at_10"] is None
    assert record["usage"] is None
    updated = connection.put(
        f"/api/v1/capabilities/knowledge/{knowledge}/datasets/{source}",
        json={**body, "name": "新版题集", "revision": 1},
        headers=ORIGIN,
    )
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    original = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").json()["items"][0]
    assert original["dataset_snapshot"]["name"] == body["name"] and original["dataset_snapshot"]["revision"] == 1
    assert (
        connection.delete(f"/api/v1/capabilities/knowledge/{knowledge}/datasets/{source}", headers=ORIGIN).status_code
        == 200
    )
    assert (
        connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").json()["items"][0]["dataset_id"]
        is None
    )


def test_sources_cannot_change_silently_or_cross_owners(client):
    connection, app = client
    knowledge, document = keyword(connection)
    source, _ = dataset(connection, knowledge, document)
    started, _ = enqueue(connection, knowledge, source)
    assert (
        connection.put(
            f"/api/v1/capabilities/knowledge/{knowledge}/processing",
            json={"chunk_size": 400, "chunk_overlap": 50, "revision": 1},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    connection.portal.call(perform, app, started.json()["id"])
    record = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").json()["items"][0]
    assert record["status"] == "FAILED" and record["error_code"] == "EVALUATION_SOURCE_CHANGED"
    assert record["completed_questions"] == 0 and not record["metrics"]["complete"]
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE knowledge_bases SET owner_id=(SELECT id FROM users WHERE id<>%s LIMIT 1) WHERE id=%s",
        (owner, knowledge),
    )
    assert connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/datasets").status_code == 404
    assert connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").status_code == 404


def test_concurrent_request_admission_and_expired_lease_never_replays(client):
    connection, app = client
    knowledge, document = keyword(connection)
    source, _ = dataset(connection, knowledge, document)
    payload = {"dataset_id": source, "dataset_revision": 1, "request_id": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: connection.post(
                    f"/api/v1/capabilities/knowledge/{knowledge}/experiments", json=payload, headers=ORIGIN
                ),
                range(2),
            )
        )
    assert all(response.status_code == 202 for response in responses)
    identifier = responses[0].json()["id"]
    assert responses[1].json()["id"] == identifier
    workers = [EvaluationWorker(app.state.db, app.state.knowledge) for _ in range(2)]

    async def claim():
        rows = await asyncio.gather(*(worker.claim() for worker in workers))
        assert sum(row is not None for row in rows) == 1
        return next(row for row in rows if row)

    run = connection.portal.call(claim)
    assert str(run["id"]) == identifier
    connection.portal.call(
        app.state.db.execute,
        "UPDATE knowledge_experiments SET lease_until=now()-interval '1 second' WHERE id=%s",
        (identifier,),
    )
    connection.portal.call(workers[0].reconcile)
    assert connection.portal.call(workers[1].claim) is None
    connection.portal.call(workers[0].finish, run, "SUCCEEDED", [])
    record = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").json()["items"][0]
    assert record["status"] == "UNKNOWN" and record["error_code"] == "EVALUATION_INTERRUPTED"
    new, _ = enqueue(connection, knowledge, source)
    assert (
        connection.post(
            f"/api/v1/capabilities/knowledge/{knowledge}/experiments/{new.json()['id']}/cancel", headers=ORIGIN
        ).status_code
        == 200
    )
    assert connection.portal.call(workers[1].claim) is None


def test_actual_model_experiment_counts_all_questions_and_stops_unknown(provider):
    connection, app, knowledge, _, requests, behavior = provider
    document = upload(connection, knowledge)["id"]
    source, body = dataset(connection, knowledge, document)
    # Keep this model experiment to one annotated question; keyword negative metrics remain separately tested.
    updated = connection.put(
        f"/api/v1/capabilities/knowledge/{knowledge}/datasets/{source}",
        json={**body, "questions": body["questions"][:1], "revision": 1},
        headers=ORIGIN,
    )
    assert updated.status_code == 200
    rejected, _ = enqueue(connection, knowledge, source, dataset_revision=2)
    assert rejected.status_code == 422 and len(requests) == 1
    admitted, _ = enqueue(connection, knowledge, source, dataset_revision=2, acknowledge_model_costs=True)
    connection.portal.call(perform, app, admitted.json()["id"])
    record = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").json()["items"][0]
    assert (
        record["status"] == "SUCCEEDED"
        and record["usage"]["input_tokens"] == 156
        and record["usage"]["output_tokens"] == 10
    )
    behavior["usage"] = False
    again, _ = enqueue(connection, knowledge, source, dataset_revision=2, acknowledge_model_costs=True)
    before = len(requests)
    connection.portal.call(perform, app, again.json()["id"])
    unknown = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/experiments").json()["items"][0]
    assert unknown["status"] == "UNKNOWN" and unknown["usage"]["unknown_calls"] == 1
    assert len(requests) == before + 1 and unknown["completed_questions"] == 0
