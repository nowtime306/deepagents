"""Bounded formal Ask/SSE/configuration observations for the fixed A01 task."""

import json
import re
from datetime import datetime
from uuid import UUID

from . import adapters
from .fixed_evidence import text_digest

NAMES = {"list_automation_agents", "create_automation_draft"}
TOTAL = ("calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens")
CALL = re.compile(r"^ae-[a-f0-9]{16}$")
EVENT = re.compile(r"[a-z][a-z0-9_.-]{0,127}")


def identifier(value):
    return str(UUID(str(value)))


def message(row, run_id):
    if row["run_id"] != run_id or row["role"] not in {"user", "assistant", "tool"}:
        raise adapters.Blocked("A01 message belongs to another root or role")
    content = row["content"]
    data = content.get("data", {})
    role = row["role"]
    text = content.get("text") if role == "user" else data.get("content")
    if not isinstance(text, str) or len(text) > 16000:
        raise adapters.Blocked("A complete bounded A01 message is required")
    result = {"role": role, "run_id": run_id, "text_digest": text_digest(text)}
    if role == "assistant":
        calls = data.get("tool_calls")
        if not isinstance(calls, list) or len(calls) > 1:
            raise adapters.Blocked("A01 assistant tool declaration is incomplete")
        result["calls"] = []
        for call in calls:
            if call["name"] not in NAMES or not CALL.fullmatch(call["id"]):
                raise adapters.Blocked("Unexpected A01 tool call")
            result["calls"].append({"name": call["name"], "call_id": call["id"]})
    elif role == "tool":
        name, call_id = data["name"], data["tool_call_id"]
        if name not in NAMES or not CALL.fullmatch(call_id) or data.get("status") != "success":
            raise adapters.Blocked("A01 tool outcome is incomplete")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise adapters.Blocked("A01 requires the real structured tool result")
        if name == "list_automation_agents":
            if not isinstance(value.get("items"), list) or len(value["items"]) != 1:
                raise adapters.Blocked("The fixed published Agent list is not complete")
            output = {"items": [{"id": identifier(item["id"]), "version": item["version"]} for item in value["items"]],
                      "current_time": value["current_time"]}
            datetime.fromisoformat(output["current_time"])
        else:
            if value.get("type") != "automation_draft" or value.get("enabled") is not False:
                raise adapters.Blocked("The real tool did not return a paused automation draft")
            output = {"id": identifier(value["id"]), "enabled": False, "type": "automation_draft"}
        result.update(name=name, call_id=call_id, output=output)
    return result


def events(platform, run_id):
    """Replay the existing terminal Ask SSE endpoint; retain no raw event/message payloads."""
    rows, ended, data = [], False, []
    with platform.http.stream("GET", f"/api/v1/runs/{run_id}/events", params={"after_event_id": 0}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if len(line) > 65536:
                raise adapters.Blocked("A01 event frame exceeds its bound")
            if line.startswith("data: "):
                data.append(line[6:])
            elif line == "" and data:
                if len(data) != 1:
                    raise adapters.Blocked("Unexpected multiline A01 event frame")
                value = json.loads(data.pop())
                if "event_id" not in value:
                    if value.get("event") == "stream.end":
                        ended = value.get("data", {}).get("status") == "SUCCEEDED"
                    elif value.get("event") == "stream.error":
                        raise adapters.Blocked("Formal Ask events could not be observed")
                    continue
                if (value.get("run_id") != run_id or type(value["event_id"]) is not int
                        or value["event_id"] != len(rows) + 1 or len(rows) >= 256
                        or not isinstance(value["event"], str) or not EVENT.fullmatch(value["event"])):
                    raise adapters.Blocked("Incomplete or foreign native Ask event sequence")
                projected = {"event_id": value["event_id"], "event": value["event"]}
                if value["event"] in {"tool.started", "tool.completed", "tool.interrupted"}:
                    payload = value["data"]
                    if payload["name"] not in NAMES or not CALL.fullmatch(payload["call_id"]):
                        raise adapters.Blocked("Unexpected native A01 tool event")
                    projected.update(name=payload["name"], call_id=payload["call_id"])
                rows.append(projected)
    if not ended or not rows or rows[-1]["event"] != "run.completed":
        raise adapters.Blocked("The terminal native Ask event tail was not observed")
    return {"complete": True, "items": rows}


def root(platform, fixture, specimen, run_id, conversation_id, *, occurrence_id=None):
    run_id, conversation_id = identifier(run_id), identifier(conversation_id)
    run = platform.get(f"/api/v1/runs/{run_id}")
    if (run["id"] != run_id or run["conversation_id"] != conversation_id
            or run["status"] not in adapters.TERMINAL or run["model_id"] != fixture["model_id"]
            or run["origin"] != ("schedule" if occurrence_id else "interactive")
            or run.get("automation_occurrence_id") != occurrence_id):
        raise adapters.Blocked("A01 root identity or terminal observation differs")
    response = platform.get(f"/api/v1/conversations/{conversation_id}/messages")
    if (not isinstance(response.get("items"), list) or not 1 <= len(response["items"]) <= 16
            or "next_before" not in response or response["next_before"] is not None):
        raise adapters.Blocked("Complete bounded A01 messages are required")
    messages = [message(row, run_id) for row in response["items"]]
    usage = platform.get(f"/api/v1/conversations/{conversation_id}/usage")
    total = adapters.known_usage({"observed": ["usage"], "usage": usage}, specimen)
    native = {key: total[key] for key in TOTAL}
    trace = platform.get(f"/api/v1/resource-runs/ask/{run_id}/configurations")
    if (trace["run_id"] != run_id or trace["mode"] != "ask"
            or trace["configuration_scope"] != {"mode": "ask", "run_id": run_id}
            or trace["business_version"].get("published_version_id") != fixture["agent_version_id"]
            or trace["business_version"].get("published_version") != 1):
        raise adapters.Blocked("The original Agent business publication is not observed")
    models = [row for row in trace["items"] if row["resource_kind"] == "model" and row["resource_id"] == fixture["model_id"]]
    if len(models) != 1:
        raise adapters.Blocked("Exactly one used controlled model snapshot is required")
    model = models[0]
    calls = [{key: row[key] for key in ("call_id", "run_id", "mode", "model_id", "model_revision", "status", "input_tokens", "output_tokens")}
             for row in model["calls"]]
    used = {"snapshot_id": identifier(model["snapshot_id"]), "revision": model["revision"], "used_at": model["used_at"],
            "temperature": model["snapshot"]["temperature"], "calls": calls}
    if used["used_at"] is None:
        raise adapters.Blocked("Resolved-only model metadata is not an actual model call")
    datetime.fromisoformat(used["used_at"])
    return {"run_id": run_id, "conversation_id": conversation_id, "origin": run["origin"], "status": run["status"],
            "occurrence_id": occurrence_id, "agent_version_id": fixture["agent_version_id"],
            "messages": messages, "events": events(platform, run_id), "usage": native, "model": used}


def schedules(platform, identifier_value=None):
    response = platform.get("/api/v1/automations")
    if not isinstance(response.get("items"), list) or len(response["items"]) != 1:
        raise adapters.Blocked("The fixed target must contain its single actual draft")
    row = response["items"][0]
    if identifier_value is not None and row["id"] != identifier_value:
        raise adapters.Blocked("Automation identity changed")
    fields = ("id", "agent_version_id", "mode", "kind", "starts_at", "enabled", "revision", "next_due_at", "completed_at", "budget",
              "missed_policy", "overlap_policy", "notify_channel_ids", "network_domains", "timezone")
    return {key: row[key] for key in fields} | {"goal_digest": text_digest(row["goal"])}


def occurrences(platform, automation_id):
    response = platform.get(f"/api/v1/automations/{automation_id}/occurrences")
    if (not isinstance(response.get("items"), list) or len(response["items"]) > 1
            or "next_cursor" not in response or response["next_cursor"] is not None):
        raise adapters.Blocked("Complete single-occurrence evidence is required")
    fields = ("id", "status", "ask_run_id", "work_run_id", "target_id", "result_status", "error_code")
    return [{key: row[key] for key in fields} for row in response["items"]]


def profile(platform, automation_id, source_run_id):
    return platform.get(f"/api/v1/agent-evaluations/profiles/automations/{automation_id}/observations",
                        params={"source_run_id": source_run_id})
