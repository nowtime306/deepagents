"""Prepare and remove a named restore fixture without any model or external-service calls."""

import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
from time import time_ns
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.config import Settings

PATH = Path(__file__).parents[2] / ".local" / "backup-reference-fixture.json"


async def prepare_branches(client, data):
    """Seed clearly labeled imported history; do not request any model."""
    source, original_files = data["conversation"], list(data["attachments"])
    owner = (await client.get("/auth/me")).json()["id"]
    model = (await client.get("/models")).json()["items"][0]["id"]
    run, message = uuid4(), uuid4()
    async with await AsyncConnection.connect(Settings.from_env().database_url) as connection:
        assert connection.info.dbname == "owner_agent"
        files = await (
            await connection.execute(
                "SELECT id,name,media_type FROM conversation_attachments "
                "WHERE conversation_id=%s AND owner_id=%s ORDER BY name",
                (source, owner),
            )
        ).fetchall()
        await connection.execute(
            "INSERT INTO task_runs(id,conversation_id,model_id,status,finished_at,context_import) "
            "VALUES(%s,%s,%s,'SUCCEEDED',now(),true)",
            (run, source, model),
        )
        await connection.execute(
            "INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) VALUES(%s,%s,%s,'user',%s)",
            (
                message,
                source,
                run,
                Jsonb(
                    {
                        "text": data["name"],
                        "context_import": True,
                        "attachments": [{"id": str(row[0]), "name": row[1], "media_type": row[2]} for row in files],
                    }
                ),
            ),
        )
        await connection.execute(
            "UPDATE conversation_attachments SET used_at=now() WHERE conversation_id=%s", (source,)
        )
    data["branches"] = []
    for mode in ["fork", "edit"]:
        response = await client.post(
            f"/conversations/{source}/branches",
            json={
                "message_id": str(message),
                "request_id": str(uuid4()),
                "mode": mode,
                **({"edit_text": "重启后仍应保留的分支草稿"} if mode == "edit" else {}),
            },
        )
        response.raise_for_status()
        identifier = response.json()["id"]
        data["branches"].append({"id": identifier, "mode": mode})
        PATH.write_text(json.dumps(data, ensure_ascii=False))
        history = (await client.get(f"/conversations/{identifier}/messages")).json()
        attachments = (
            history["pending_attachments"] if mode == "edit" else history["items"][0]["content"]["attachments"]
        )
        for file in attachments:
            raw = await client.get(f"/attachments/{file['id']}/download")
            raw.raise_for_status()
            data["attachments"].append(
                {"id": file["id"], "size": len(raw.content), "digest": hashlib.sha256(raw.content).hexdigest()}
            )
        PATH.write_text(json.dumps(data, ensure_ascii=False))
    response = await client.delete(f"/conversations/{source}")
    response.raise_for_status()
    data["source_deleted"], data["conversation"] = source, None
    original_ids = {file["id"] for file in original_files}
    data["attachments"] = [file for file in data["attachments"] if file["id"] not in original_ids]
    PATH.write_text(json.dumps(data, ensure_ascii=False))


async def run(command):
    values = dict(
        line.split("=", 1) for line in (PATH.parent.parent / ".env.admin").read_text().splitlines() if "=" in line
    )
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8100/api/v1", headers={"Origin": "http://localhost:3100"}, timeout=20
    ) as client:
        login = await client.post(
            "/auth/login", json={"username": values["ADMIN_USERNAME"], "password": values["ADMIN_PASSWORD"]}
        )
        login.raise_for_status()
        if command == "create":
            if PATH.exists():
                raise ValueError("A restore fixture is already recorded; inspect or clean it first")
            data = {
                "name": "备份恢复验收-" + str(time_ns()),
                "conversation": None,
                "knowledge": None,
                "attachments": [],
            }
            PATH.write_text(json.dumps(data, ensure_ascii=False))
            response = await client.post("/conversations", json={"title": data["name"]})
            response.raise_for_status()
            data["conversation"] = response.json()["id"]
            PATH.write_text(json.dumps(data, ensure_ascii=False))
            with Image.new("RGB", (300, 100), "white") as image:
                ImageDraw.Draw(image).text((20, 40), "RESTORE TEST", fill="black", font_size=24)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
            for name, content in [
                ("恢复验证.txt", "这是本机备份恢复验收样本。".encode()),
                ("恢复验证.png", buffer.getvalue()),
            ]:
                response = await client.post(
                    f"/conversations/{data['conversation']}/attachments", params={"name": name}, content=content
                )
                response.raise_for_status()
                data["attachments"].append(
                    {"id": response.json()["id"], "size": len(content), "digest": hashlib.sha256(content).hexdigest()}
                )
                PATH.write_text(json.dumps(data, ensure_ascii=False))
            response = await client.post("/capabilities/knowledge", json={"name": data["name"]})
            response.raise_for_status()
            data["knowledge"] = response.json()["id"]
            PATH.write_text(json.dumps(data, ensure_ascii=False))
            response = await client.post(
                f"/capabilities/knowledge/{data['knowledge']}/documents",
                files={"file": ("revision.txt", "第一修订".encode())},
            )
            response.raise_for_status()
            document = response.json()["id"]
            response = await client.put(
                f"/capabilities/knowledge/{data['knowledge']}/documents/{document}",
                files={"file": ("revision.txt", "第二修订".encode())},
                data={"revision": 1},
            )
            response.raise_for_status()
            data["document"] = document
            PATH.write_text(json.dumps(data, ensure_ascii=False))
            await prepare_branches(client, data)
            print(
                "Prepared four copied branch attachments, deleted source, persistent draft "
                "and two document revisions; no model requested"
            )
        else:
            data = json.loads(PATH.read_text())
            if not data["name"].startswith("备份恢复验收-") or not data["name"].removeprefix("备份恢复验收-").isdigit():
                raise ValueError("Restore fixture identity mismatch")
            for attachment in data["attachments"]:
                response = await client.get(f"/attachments/{attachment['id']}/download")
                response.raise_for_status()
                if (
                    len(response.content) != attachment["size"]
                    or hashlib.sha256(response.content).hexdigest() != attachment["digest"]
                ):
                    raise ValueError("Attachment changed across service restart")
            if data.get("knowledge") and data.get("document"):
                source = f"/capabilities/knowledge/{data['knowledge']}/documents/{data['document']}"
                response = await client.get(source + "/revisions")
                response.raise_for_status()
                if [row["revision"] for row in response.json()["items"]] != [2, 1]:
                    raise ValueError("Document revision history changed across restart")
                response = await client.get(source + "/chunks")
                response.raise_for_status()
                if response.json()["items"][0]["content"] != "第二修订":
                    raise ValueError("Current document source changed across restart")
            for branch in data.get("branches", []):
                response = await client.get(f"/conversations/{branch['id']}/messages")
                response.raise_for_status()
                history = response.json()
                if branch["mode"] == "fork" and len(history["items"]) != 1:
                    raise ValueError("Imported branch history changed across restart")
                if branch["mode"] == "edit" and history["saved_draft"] != "重启后仍应保留的分支草稿":
                    raise ValueError("Edited branch draft changed across restart")
                listing = (await client.get("/conversations")).json()["items"]
                if not any(row["id"] == branch["id"] and row["title"] == "分支 · " + data["name"] for row in listing):
                    raise ValueError("Branch fixture identity mismatch")
                response = await client.delete(f"/conversations/{branch['id']}")
                response.raise_for_status()
            for key, route, label in [
                ("conversation", "/conversations", "title"),
                ("knowledge", "/capabilities/knowledge", "name"),
            ]:
                if not data.get(key):
                    continue
                response = await client.get(route)
                response.raise_for_status()
                existing = next((row for row in response.json()["items"] if row["id"] == data[key]), None)
                if existing and existing[label] != data["name"]:
                    raise ValueError("Recorded fixture name mismatch")
                response = await client.delete(route + "/" + data[key])
                if response.status_code not in {200, 404}:
                    response.raise_for_status()
            PATH.unlink()
            print("Removed only the recorded restore fixture through the owned APIs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "cleanup"])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run(parser.parse_args().command))
