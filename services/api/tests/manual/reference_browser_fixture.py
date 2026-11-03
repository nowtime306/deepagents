"""Create and remove only recorded text fixtures, without external model calls."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

from app.config import Settings

META = Path(__file__).parents[2] / ".local" / "reference-browser-fixture.json"


def main():
    command = argparse.ArgumentParser()
    command.add_argument("action", choices=["create", "append", "bind", "check", "cleanup"])
    action = command.parse_args().action
    settings = Settings.from_env()
    with psycopg.connect(settings.database_url) as db:
        assert db.info.dbname == "owner_agent"
        owner = db.execute("SELECT id FROM users WHERE username='admin' AND is_admin").fetchone()[0]
        if action == "create":
            assert not META.exists(), "Clean the recorded fixture before creating another"
            label = "会话引用验收-" + str(time.time_ns())
            model = db.execute("SELECT id FROM model_configs WHERE enabled ORDER BY id LIMIT 1").fetchone()[0]
            sources = [str(uuid4()) for _ in range(2)]
            metadata = {"label": label, "sources": sources}
            with META.open("x", encoding="utf-8") as out:
                json.dump(metadata, out)
            for index, source in enumerate(sources):
                db.execute("INSERT INTO conversations(id,owner_id,title) VALUES(%s,%s,%s)",
                           (source, owner, label + str(index)))
                run = uuid4()
                db.execute("INSERT INTO task_runs(id,conversation_id,model_id,status,context_import,finished_at) "
                           "VALUES(%s,%s,%s,'SUCCEEDED',true,now())", (run, source, model))
                db.execute("INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) "
                           "VALUES(%s,%s,%s,'user',jsonb_build_object('text',%s::text))",
                           (uuid4(), source, run, "本人固定参考正文" + str(index)))
        else:
            metadata = json.loads(META.read_text(encoding="utf-8"))
            identifiers = [*metadata["sources"], *([metadata["target"]] if metadata.get("target") else [])]
            for identifier in identifiers:
                row = db.execute("SELECT owner_id,title FROM conversations WHERE id=%s", (identifier,)).fetchone()
                assert row and row[0] == owner and (row[1].startswith(metadata["label"])
                                                   or row[1] == "引用资料的新对话")
            if action in {"bind", "check"}:
                credentials = dict(line.split("=", 1) for line in
                                   (Path(__file__).parents[2] / ".env.admin").read_text().splitlines())
                with httpx.Client(base_url="http://127.0.0.1:8100", headers={"Origin": settings.web_origin}) as client:
                    login = client.post("/api/v1/auth/login", json={"username": credentials["ADMIN_USERNAME"],
                                                                  "password": credentials["ADMIN_PASSWORD"]})
                    assert login.status_code == 200
                    if action == "bind":
                        assert not metadata.get("target")
                        created = client.post("/api/v1/conversations", json={"title": metadata["label"] + "目标"})
                        assert created.status_code == 201
                        metadata["target"] = created.json()["id"]
                        META.write_text(json.dumps(metadata), encoding="utf-8")
                        reply = client.put(f"/api/v1/conversations/{metadata['target']}/references",
                                           json={"revision": 0, "source_ids": [metadata["sources"][0]]})
                        assert reply.status_code == 200
                    else:
                        reply = client.get(f"/api/v1/conversations/{metadata['target']}/references")
                        assert reply.status_code == 200
                        value = reply.json()
                        assert value["revision"] == 1 and len(value["items"]) == 1
                        assert value["items"][0]["source_id"] == metadata["sources"][0]
                        assert value["items"][0]["available"] and value["items"][0]["message_count"] == 1
            elif action == "append":
                source = metadata["sources"][0]
                run = db.execute("SELECT id FROM task_runs WHERE conversation_id=%s AND context_import",
                                 (source,)).fetchone()[0]
                db.execute("INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) "
                           "VALUES(%s,%s,%s,'assistant','{\"text\":\"显式更新后新增正文\"}')",
                           (uuid4(), source, run))
            else:
                assert not db.execute("SELECT 1 FROM task_runs WHERE conversation_id=ANY(%s::uuid[]) "
                                      "AND NOT context_import", (identifiers,)).fetchone(), "Never remove real calls"
                db.execute("DELETE FROM conversations WHERE id=ANY(%s::uuid[])", (identifiers,))
                db.commit()
                META.unlink()
        print(json.dumps({"fixture": action, "metadata": str(META)}))


if __name__ == "__main__":
    main()
