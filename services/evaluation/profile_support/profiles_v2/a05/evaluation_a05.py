"""Opt-in, dedicated-target A05 v2 controller. Never imported by production app."""

import asyncio
import json
import os
import secrets
import socket
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import uvicorn
from app.agent_evaluation_environment import environment, read_marker
from app.backup import private_directory
from app.errors import fail
from app.permissions import require_account
from app.resource_runtime import current_scope
from app.security import administrator
from fastapi import Request
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator

from evaluation_runner.profiles_v2.a05.protocol import (
    BASE,
    CASE,
    ROLES,
    SCENES,
    action,
    declared_lookup,
    definition_digest,
)
from profiles_v2.a05.a05_receiver import Ledger, application


class DeferredLedger:
    def __init__(self):
        self.bound = None

    def effect(self, kind):
        if self.bound is None:
            raise ValueError("An actual Ask root must be bound before receiver effects")
        self.bound.effect(kind)

    def observe(self):
        if self.bound is None:
            raise ValueError("An actual Ask root must be bound before observations")
        return self.bound.observe()


def scoped_application(ledger, token):
    delegated = application(ledger, token)

    async def app(scope, receive, send):
        if scope["type"] == "http" and ledger.bound is not None:
            headers = dict(scope.get("headers", []))
            wanted = ledger.bound.scope
            if any(
                headers.get(("x-a05-" + key.replace("_", "-")).encode()) != value.encode()
                for key, value in wanted.items()
            ):
                await send({"type": "http.response.start", "status": 403, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
        await delegated(scope, receive, send)

    return app


class Controller:
    def __init__(self, app, state):
        self.app, self.state = app, state
        marker = read_marker(app.state.settings.data_dir)
        self.owner, self.environment, self.namespace = marker.owner_id, marker.environment_id, marker.sandbox_namespace
        self.directory = app.state.settings.data_dir / "a05-v2"
        private_directory(self.directory)
        self.path = self.directory / "control.json"
        self.records, self.live, self.failed, self.deadline = {}, {}, False, None
        self.lock = asyncio.Lock()
        self.original_client_factory = app.state.mcp.http_client_factory
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size > 16384:
                raise ValueError("Invalid A05 retained state")
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved["definition_digest"] != definition_digest():
                raise ValueError("A05 definition changed; retain original state")
            self.records, self.deadline = saved["records"], saved["deadline"]
            # No automatic rebind/restart of lost listeners, requests, roots or permission effects.
            self.failed = bool(self.records)
        if not self.records and any(row["case_id"] == CASE for row in state.models.values()):
            self.failed = True  # An old A05 account/budget cannot be adopted into this new receiver batch.

    def client_factory(self, headers):
        scope = current_scope()
        merged = {key: value for key, value in (headers or {}).items() if not key.lower().startswith("x-a05-")}
        if scope is not None:
            identity = {
                "environment_id": str(self.environment),
                "owner_id": str(scope.owner),
                "root_id": str(scope.run),
                "namespace": str(self.namespace),
            }
            merged.update({"X-A05-" + key.replace("_", "-"): value for key, value in identity.items()})
        return self.original_client_factory(merged)

    def save(self):
        body = {"definition_digest": definition_digest(), "records": self.records, "deadline": self.deadline}
        try:
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(body, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except BaseException:
            self.failed = True
            raise

    def guard(self):
        if self.failed or self.state.storage_failed:
            raise ValueError("A05 controller evidence is unavailable; preserve state")
        if any(
            row.get("status") in {"INITIALIZING", "CLOSING"} or row["revocation"] == "PENDING"
            for row in self.records.values()
        ):
            raise ValueError("An earlier fixture effect is pending or unknown; do not dispatch")
        if self.deadline and datetime.now(UTC) >= datetime.fromisoformat(self.deadline):
            raise ValueError("A05 whole-case deadline elapsed; no new effect")

    async def initialize(self, scene, model_id):
        if scene not in SCENES:
            raise ValueError("Unknown fixed scene")
        async with self.lock:
            self.guard()
            with self.state.lock:
                binding = self.state.models.get(model_id)
            if binding != {"case_id": CASE, "role": ROLES[scene], "owner_id": str(self.owner)}:
                raise ValueError("Only the sealed owned v2 scene model can initialize its receiver")
            if scene in self.records:
                record = self.records[scene]
                if record["model_id"] != model_id or record["status"] != "READY" or scene not in self.live:
                    raise ValueError("Prior receiver effect requires reconciliation; do not replay")
                return record["server_id"]
            if self.deadline is None:
                self.deadline = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
            server_id = str(uuid4())
            record = {
                "server_id": server_id,
                "model_id": model_id,
                "status": "INITIALIZING",
                "root_id": None,
                "conversation_id": None,
                "revocation": "NONE",
            }
            self.records[scene] = record
            self.save()  # Before socket or database effects; a lost init never creates a second receiver.
            ledger, token = DeferredLedger(), secrets.token_urlsafe(32)
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sock.listen(128)
            sock.setblocking(False)
            url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
            server = uvicorn.Server(
                uvicorn.Config(scoped_application(ledger, token), access_log=False, log_level="error")
            )
            task = asyncio.create_task(server.serve(sockets=[sock]))
            self.live[scene] = (ledger, sock, server, task)
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        task.result()
                        raise ValueError("Receiver failed to start")
                    await asyncio.sleep(0.01)
            db, vault = self.app.state.db, self.app.state.models.vault
            await db.execute(
                "INSERT INTO mcp_servers(id,name,url,token_ciphertext,allow_private) VALUES(%s,%s,%s,%s,true)",
                (UUID(server_id), "A05-v2-" + scene + "-" + server_id, url, vault.encrypt(token)),
            )
            config = (await db.fetch("SELECT * FROM mcp_servers WHERE id=%s", (UUID(server_id),)))[0]
            tools = await self.app.state.mcp.discover(config)
            if {row["name"]: row["read_only"] for row in tools} != {"ae_lookup": True, "ae_write": False}:
                raise ValueError("Receiver declarations do not match the fixed contract")
            for row in tools:
                row["enabled"] = row["name"] == "ae_lookup"
            await db.execute(
                "UPDATE mcp_servers SET tools=%s,status='available' WHERE id=%s", (Jsonb(tools), UUID(server_id))
            )
            record["status"] = "READY"
            self.save()
            return server_id

    async def bind(self, scene, model_id):
        scope = current_scope()
        if scope is None or scope.mode != "ask" or str(scope.owner) != str(self.owner):
            raise ValueError("Actual owned Ask ResourceRun required")
        async with self.lock:
            self.guard()
            record = self.records.get(scene)
            if not record or record["model_id"] != model_id or record["status"] != "READY" or scene not in self.live:
                raise ValueError("Original receiver/model binding is required")
            root = str(scope.run)
            rows = await self.app.state.db.fetch(
                "SELECT r.id,r.model_id,r.status,r.conversation_id,r.agent_snapshot,c.owner_id "
                "FROM task_runs r JOIN conversations c ON c.id=r.conversation_id WHERE r.id=%s AND c.owner_id=%s",
                (scope.run, self.owner),
            )
            row = rows[0] if rows else None
            if (
                not row
                or row["status"] != "RUNNING"
                or str(row["model_id"]) != model_id
                or row["agent_snapshot"].get("mcp_ids") != [record["server_id"]]
            ):
                raise ValueError("The actual root must use the exact original model and MCP resource")
            if record["root_id"] is not None and record["root_id"] != root:
                raise ValueError("A scene may not acquire a replacement or third root")
            if self.deadline is None:
                self.deadline = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
            record["root_id"], record["conversation_id"] = root, str(row["conversation_id"])
            self.save()
            ledger = self.live[scene][0]
            if ledger.bound is None:
                ledger.bound = Ledger(
                    self.directory / (scene + ".sqlite"),
                    {
                        "environment_id": str(self.environment),
                        "owner_id": str(self.owner),
                        "root_id": root,
                        "namespace": str(self.namespace),
                    },
                )
            return scope

    async def revoke(self, scene, root):
        if scene != "revocation":
            raise ValueError("Only the fixed revocation scene can revoke its own lookup")
        async with self.lock:
            self.guard()
            record = self.records[scene]
            if record["root_id"] != str(root) or record["revocation"] != "NONE":
                raise ValueError("Permission effect is foreign, repeated or unknown")
            ledger = self.live[scene][0].observe()
            if ledger["lookup_count"] != 1 or ledger["write_count"] != 0:
                raise ValueError("The original receiver lookup must already be observed")
            record["revocation"] = "PENDING"
            record["revocation_control"] = {
                "root_id": str(root),
                "server_id": record["server_id"],
                "boundary": 2,
                "lookup_count": 1,
                "before": True,
                "after": None,
            }
            self.save()
            # Fixed trusted fixture control. It changes the real permission metadata; no tool mocks.
            rows = await self.app.state.db.fetch(
                "SELECT tools FROM mcp_servers WHERE id=%s AND enabled", (UUID(record["server_id"]),)
            )
            if not rows or {row["name"] for row in rows[0]["tools"]} != {"ae_lookup", "ae_write"}:
                raise ValueError("Original receiver metadata is unavailable")
            if next(row for row in rows[0]["tools"] if row["name"] == "ae_lookup")["enabled"] is not True:
                raise ValueError("Cannot attribute an already revoked lookup to this fixture")
            with self.state.lock:
                root_budget = self.state.roots[(str(self.owner), "ask", str(root))]
                if root_budget["calls"] != 2 or root_budget["pending"] != 1 or root_budget["unknown"]:
                    raise ValueError("Revocation must occur at the original second generation boundary")
            tools = [{**row, "enabled": False} for row in rows[0]["tools"]]
            await self.app.state.db.execute(
                "UPDATE mcp_servers SET tools=%s WHERE id=%s", (Jsonb(tools), UUID(record["server_id"]))
            )
            after = await self.app.state.db.fetch(
                "SELECT tools FROM mcp_servers WHERE id=%s", (UUID(record["server_id"]),)
            )
            if not after or after[0]["tools"] != tools:
                raise ValueError("Original exact revocation was not confirmed")
            record["revocation_control"]["after"] = False
            record["revocation"] = "CONFIRMED"
            self.save()

    async def observe(self, scene):
        record = self.records[scene]
        if not record["root_id"]:
            raise ValueError("A genuine root has not been bound")
        rows = await self.app.state.db.fetch(
            "SELECT r.id,r.status,r.error_code AS error FROM task_runs r "
            "JOIN conversations c ON c.id=r.conversation_id "
            "WHERE r.id=%s AND c.owner_id=%s",
            (UUID(record["root_id"]), self.owner),
        )
        if not rows:
            raise ValueError("Original root disappeared")
        scope = {
            "environment_id": str(self.environment),
            "owner_id": str(self.owner),
            "root_id": record["root_id"],
            "namespace": str(self.namespace),
        }
        ledger = Ledger(self.directory / (scene + ".sqlite"), scope)
        messages = await self.app.state.db.fetch(
            "SELECT role,run_id,content FROM conversation_messages "
            "WHERE conversation_id=%s ORDER BY created_at,id LIMIT 7",
            (UUID(record["conversation_id"]),),
        )
        events = await self.app.state.db.fetch(
            "SELECT event_id,event,data FROM run_events WHERE run_id=%s ORDER BY event_id LIMIT 129",
            (UUID(record["root_id"]),),
        )
        if (
            len(events) > 128
            or [row["event_id"] for row in events] != list(range(1, len(events) + 1))
            or len(messages) > 5
        ):
            raise ValueError("Bounded complete original events/messages required")
        from app.usage_api import summaries, totals

        usage = totals(await summaries(self.app.state.db, self.owner, [UUID(record["root_id"])]))
        return {
            "scene": scene,
            "definition_version": 2,
            "definition_digest": definition_digest(),
            "scope": scope,
            "server_id": record["server_id"],
            "model_id": record["model_id"],
            "conversation_id": record["conversation_id"],
            "receiver": ledger.observe(),
            "provider": self.state.root_summary(self.owner, "ask", record["root_id"]),
            "run": {key: str(value) if key == "id" else value for key, value in rows[0].items()},
            "revocation": record["revocation"],
            "closed": record["status"] == "CLOSED",
            "revocation_control": record.get("revocation_control"),
            "usage": usage,
            "messages": [{**row, "run_id": str(row["run_id"])} for row in messages],
            "tool_events": [
                {"event": row["event"], "name": row["data"]["name"], "call_id": row["data"]["call_id"]}
                for row in events
                if row["event"].startswith("tool.")
            ],
            "terminal_event": events[-1]["event"] if events else None,
            "complete": not self.failed
            and record["revocation"] != "PENDING"
            and record["status"] not in {"INITIALIZING", "CLOSING"},
        }

    async def close_scene(self, scene):
        record = self.records[scene]
        value = await self.observe(scene)
        if (
            value["complete"] is not True
            or value["run"]["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}
            or value["provider"].get("complete") is not True
        ):
            raise ValueError("Do not close an active/unknown receiver or provider root")
        pending = await self.app.state.db.fetch(
            "SELECT count(*) AS n FROM model_usage WHERE owner_id=%s AND run_id=%s AND status IN ('PENDING','UNKNOWN')",
            (self.owner, UUID(record["root_id"])),
        )
        if pending[0]["n"]:
            raise ValueError("Do not close a root with unsettled native usage")
        if record["status"] == "CLOSED":
            return value
        if scene not in self.live:
            raise ValueError("Lost listener cleanup needs manual reconciliation")
        record["status"] = "CLOSING"
        self.save()
        await self.stop_listener(scene)
        await self.app.state.db.execute(
            "UPDATE mcp_servers SET enabled=false,token_ciphertext='' WHERE id=%s",
            (UUID(record["server_id"]),),
        )
        cleared = await self.app.state.db.fetch(
            "SELECT enabled,token_ciphertext FROM mcp_servers WHERE id=%s", (UUID(record["server_id"]),)
        )
        if not cleared or cleared[0]["enabled"] is not False or cleared[0]["token_ciphertext"] != "":
            raise ValueError("Own receiver credential clearing was not confirmed")
        record["status"] = "CLOSED"
        self.save()
        return await self.observe(scene)

    async def stop_listener(self, scene):
        _, sock, server, task = self.live[scene]
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), 10)
        except BaseException:
            self.failed = True
            raise
        finally:
            if task.done():
                sock.close()
        del self.live[scene]

    async def close(self):
        self.app.state.mcp.http_client_factory = self.original_client_factory
        results = await asyncio.gather(
            *(self.stop_listener(scene) for scene in tuple(self.live)), return_exceptions=True
        )
        if any(isinstance(result, BaseException) for result in results):
            self.failed = True
            raise RuntimeError("A05 receiver shutdown is incomplete; preserve retained state")


class ControlledA05(BaseChatModel):
    scene: str
    model_id: str
    _state: object = PrivateAttr()
    _controller: Controller = PrivateAttr()
    _lookup: str | None = PrivateAttr(default=None)

    @property
    def _llm_type(self):
        return "agent-evaluation-a05-v2"

    def bind_tools(self, tools, **kwargs):
        self._lookup = declared_lookup(tools, self._controller.records[self.scene]["server_id"])
        return self

    def _generate(self, *args, **kwargs):
        raise ValueError("A05 v2 only supports the real asynchronous Ask runtime")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self._lookup is None:
            raise ValueError("Genuine tool declarations are required")
        scope = await self._controller.bind(self.scene, self.model_id)
        key = self._state.reserve(CASE, ROLES[self.scene], messages)
        try:
            reply = action(self.scene, self._lookup, messages)
            if self.scene == "revocation" and any(isinstance(message, ToolMessage) for message in messages):
                await self._controller.revoke(self.scene, scope.run)
            reply.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            self._state.complete(key)
            return ChatResult(generations=[ChatGeneration(message=reply)])
        except BaseException:
            self._state.mark_unknown(key)
            raise


class Init(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_id: str

    @field_validator("model_id")
    @classmethod
    def canonical_model(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Canonical model UUID required")
        return value


async def install(app, state):
    controller = Controller(app, state)
    app.state.evaluation_a05_v2 = controller
    app.state.mcp.http_client_factory = controller.client_factory

    async def authorized(request):
        proof = await environment(request)
        user = await administrator(request)
        if str(user["id"]) != str(controller.owner) or str(proof["environment_id"]) != str(controller.environment):
            fail(403, "A05_SCOPE_MISMATCH", "A05独立测试归属不符")
        for permission in ("evaluation.run", "tools.use", "chat.run"):
            await require_account(app.state.db, user["id"], permission)

    @app.get(BASE + "/capabilities")
    async def capability(request: Request):
        await authorized(request)
        return {
            "case_id": CASE,
            "definition_version": 2,
            "definition_digest": definition_digest(),
            "limits": controller.state.case_limits[CASE],
            "per_root": {
                "max_model_calls": controller.state.limits[CASE][0],
                "max_tokens": controller.state.limits[CASE][1],
            },
            "timeout_seconds": 120,
            "healthy": not controller.failed,
        }

    @app.post(BASE + "/receivers/{scene}", status_code=201)
    async def initialize(scene: Literal["injection", "revocation"], body: Init, request: Request):
        await authorized(request)
        return {"id": await controller.initialize(scene, str(UUID(body.model_id)))}

    @app.get(BASE + "/receivers/{scene}/observations")
    async def observe(scene: Literal["injection", "revocation"], request: Request):
        await authorized(request)
        return await controller.observe(scene)

    @app.post(BASE + "/receivers/{scene}/close")
    async def close(scene: Literal["injection", "revocation"], request: Request):
        await authorized(request)
        return await controller.close_scene(scene)

    return controller


def model_factory(config, state):
    if config.model != "eval::AET-A05::v2" or not hasattr(state, "a05_v2"):
        raise ValueError("A05 v2 must be explicitly activated on a dedicated candidate target")
    scope = current_scope()
    binding = state.models.get(config.id)
    if not scope or scope.mode != "ask" or not binding or binding["owner_id"] != str(scope.owner):
        raise ValueError("Owned genuine A05 model binding required")
    scenes = [scene for scene, role in ROLES.items() if role == binding["role"] and binding["case_id"] == CASE]
    if len(scenes) != 1:
        raise ValueError("Exact sealed A05 scene role required")
    model = ControlledA05(scene=scenes[0], model_id=config.id)
    model._controller, model._state = state.a05_v2, state
    return model
