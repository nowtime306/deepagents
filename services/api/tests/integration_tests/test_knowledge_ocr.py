"""Real bounded rasterization, durable OCR drafts, explicit review and live revocation."""

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from PIL import Image, ImageDraw
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from test_api import ORIGIN
from test_api import client as client

from app.ask_queue import conversation_lock
from app.ask_worker import AskWorker
from app.knowledge_ocr import render
from app.models import ModelInput
from tests.unit_tests.test_harness import ToolModel


def image(format="PNG"):
    with Image.new("RGB", (600, 180), "white") as source:
        ImageDraw.Draw(source).text((30, 70), "INVOICE TOTAL 200", fill="black", font_size=28)
        target = io.BytesIO()
        source.save(target, format=format)
        return target.getvalue()


@pytest.fixture
def ocr(client):
    connection, app = client
    model = connection.portal.call(
        app.state.models.save,
        ModelInput(
            name="OCR替身-" + uuid4().hex[:12],
            model="local-vision",
            kind="vision",
            supports_images=True,
            base_url="http://unused.invalid/v1",
        ),
    )
    knowledge = connection.post("/api/v1/capabilities/knowledge", json={"name": "待校对资料"}, headers=ORIGIN).json()[
        "id"
    ]
    app.state.settings.ask_worker_enabled = True
    try:
        yield connection, app, model, knowledge
    finally:
        connection.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (model,))


def submit(connection, model, knowledge, data=None, **fields):
    return connection.post(
        f"/api/v1/capabilities/knowledge/{knowledge}/ocr",
        headers=ORIGIN,
        data={"model_id": model, **fields},
        files={"file": ("invoice.png", data if data is not None else image())},
    )


async def perform(app, run, text, observed):
    class Vision(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            observed.append(messages)
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def factory(_):
        return Vision(
            responses=[
                AIMessage(content=text, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
            ]
        )

    worker = AskWorker(app.state.db, app.state.models, app.state.settings, factory)
    root = (
        await app.state.db.fetch(
            "SELECT r.conversation_id,c.owner_id FROM task_runs r "
            "JOIN conversations c ON c.id=r.conversation_id WHERE r.id=%s",
            (run,),
        )
    )[0]
    async with await AsyncConnection.connect(
        app.state.settings.database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as session:
        await session.execute("SELECT pg_advisory_lock(710020,%s)", (conversation_lock(root["conversation_id"]),))
        row = await worker.queue.claim(UUID(run), worker.id)
        assert row is not None
        row["owner_id"] = root["owner_id"]
        await worker.execute(row, session)


def test_real_pdf_subprocess_rasterization_and_bounds(tmp_path):
    pages = render(image("PDF"), 1, 1)
    assert len(pages) == 1 and pages[0]["page"] == 1
    encoded = base64.b64decode(pages[0]["data"])
    with Image.open(io.BytesIO(encoded)) as raster:
        assert 1 <= raster.width <= 1800 and raster.height <= 1800
    path = Path(__file__).parents[2] / ".local" / "ocr-render-verification.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    from app.errors import APIError

    with pytest.raises(APIError):
        render(image("PDF"), 1, 2)
    with pytest.raises(APIError):
        render(b"broken", 1, 1)


def test_ocr_draft_review_import_tokens_and_source_cleanup(ocr):
    connection, app, model, knowledge = ocr
    source = image()
    started = submit(connection, model, knowledge, source)
    assert started.status_code == 202, started.text
    job = started.json()
    repeated = submit(connection, model, knowledge, source).json()
    assert repeated["duplicate"] and repeated["id"] == job["id"]
    assert connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/documents").json()["items"] == []
    observed = []
    text = json.dumps({"pages": [{"page": 1, "text": "产品价格200元"}]}, ensure_ascii=False)
    connection.portal.call(perform, app, job["run_id"], text, observed)
    rows = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/ocr").json()["items"]
    assert (
        rows[0]["status"] == "SUCCEEDED"
        and rows[0]["usage"]["input_tokens"] == 10
        and rows[0]["usage"]["output_tokens"] == 5
    )
    assert len(observed) == 1 and any(
        isinstance(message.content, list) and any(block.get("type") == "image_url" for block in message.content)
        for message in observed[0]
    )
    internal = rows[0]["internal_conversation_id"]
    previews = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/ocr/{job['id']}/pages").json()["items"]
    preview = connection.get(previews[0]["url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert internal not in {row["id"] for row in connection.get("/api/v1/conversations").json()["items"]}
    assert (
        connection.post(
            f"/api/v1/conversations/{internal}/runs", json={"model_id": model, "message": "越过校对"}, headers=ORIGIN
        ).status_code
        == 409
    )
    target = f"/api/v1/capabilities/knowledge/{knowledge}/ocr/{job['id']}/import"
    body = {"pages": [{"page": 1, "text": "产品价格250元（人工校对）"}], "revision": 1}
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: connection.post(target, json=body, headers=ORIGIN), range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]
    imported = next(result for result in results if result.status_code == 200)
    assert imported.status_code == 200 and imported.json()["revision"] == 1
    assert connection.post(target, json=body, headers=ORIGIN).status_code == 409
    found = connection.post(
        f"/api/v1/capabilities/knowledge/{knowledge}/search", json={"query": "产品价格"}, headers=ORIGIN
    ).json()["items"]
    assert "250" in found[0]["text"] and "OCR经用户校对" in found[0]["location"]
    files = connection.portal.call(
        app.state.db.fetch, "SELECT id FROM conversation_attachments WHERE conversation_id=%s", (internal,)
    )
    assert connection.delete(f"/api/v1/capabilities/knowledge/{knowledge}", headers=ORIGIN).status_code == 200
    assert all(not (app.state.settings.data_dir / "attachments" / str(row["id"])).exists() for row in files)
    assert not connection.portal.call(app.state.db.fetch, "SELECT id FROM conversations WHERE id=%s", (internal,))


def test_invalid_draft_and_cancel_never_import_or_repeat_model(ocr):
    connection, app, model, knowledge = ocr
    failed = submit(connection, model, knowledge, b"broken")
    assert failed.status_code == 422
    started = submit(connection, model, knowledge).json()
    observed = []
    connection.portal.call(perform, app, started["run_id"], '{"pages":[{"page":2,"text":"伪造页码"}]}', observed)
    jobs = connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/ocr").json()["items"]
    drafted = next(row for row in jobs if row["id"] == started["id"])
    assert drafted["status"] == "FAILED" and drafted["error_code"] == "OCR_OUTPUT_INVALID"
    assert connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/documents").json()["items"] == []
    assert submit(connection, model, knowledge).json()["duplicate"] and len(observed) == 1
    assert submit(connection, model, knowledge, retry_of=started["id"]).status_code == 422
    retried = submit(connection, model, knowledge, retry_of=started["id"], acknowledge_duplicate=True)
    assert retried.status_code == 202 and not retried.json()["duplicate"]
    assert submit(connection, model, knowledge, retry_of=started["id"], acknowledge_duplicate=True).json()["duplicate"]
    assert (
        connection.post(
            f"/api/v1/capabilities/knowledge/{knowledge}/ocr/{retried.json()['id']}/cancel", headers=ORIGIN
        ).status_code
        == 200
    )
    # A distinct image makes a new explicit job; cancelling it must stop before its first model call.
    new = submit(connection, model, knowledge, image("JPEG")).json()
    assert connection.delete(f"/api/v1/capabilities/knowledge/{knowledge}", headers=ORIGIN).status_code == 409
    assert (
        connection.post(
            f"/api/v1/capabilities/knowledge/{knowledge}/ocr/{new['id']}/cancel", headers=ORIGIN
        ).status_code
        == 200
    )
    worker = AskWorker(app.state.db, app.state.models, app.state.settings)
    connection.portal.call(worker.queue.reconcile)
    assert connection.get(f"/api/v1/runs/{new['run_id']}").json()["status"] == "CANCELLED"
    assert connection.delete(f"/api/v1/capabilities/knowledge/{knowledge}", headers=ORIGIN).status_code == 200


def test_revoked_knowledge_owner_stops_queued_ocr_before_model(ocr):
    connection, app, model, knowledge = ocr
    started = submit(connection, model, knowledge).json()
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE knowledge_bases SET owner_id=(SELECT id FROM users WHERE id<>%s LIMIT 1) WHERE id=%s",
        (owner, knowledge),
    )
    observed = []
    connection.portal.call(perform, app, started["run_id"], '{"pages":[{"page":1,"text":"不应执行"}]}', observed)
    state = connection.get(f"/api/v1/runs/{started['run_id']}").json()
    assert state["status"] == "FAILED" and state["error_code"] == "OCR_REVOKED" and observed == []
    assert connection.get(f"/api/v1/capabilities/knowledge/{knowledge}/ocr").status_code == 404
