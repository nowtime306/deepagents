"""Engineering-only MockTransport CLI contract. No target, DB, manager or provider is used."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx

HERE = Path(__file__).parent
ROOT = next(
    parent
    for parent in HERE.resolve().parents
    if (parent / "services/api/app").is_dir()
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(directory, identifier):
    helpers = load("private_cli_helpers", HERE / "test_formal_team.py")
    module = load(
        "evaluation_runner.fixed_run",
        ROOT / "services/evaluation/src/evaluation_runner/fixed_run.py",
    )
    module.CATALOG_PATH = ROOT / "services/evaluation/datasets/team-definitions-v2.json"
    specimen = helpers.case(identifier)
    data, seed, snapshot = helpers.fixture_module.build(specimen)
    body, fixture = snapshot["body"], seed["fixture"]
    owner, environment, root = (
        data["owner_id"],
        data["environment_id"],
        seed["platform_root"]["id"],
    )
    target = {
        "schema_version": 1,
        "environment_id": environment,
        "owner_id": owner,
        "api_base_url": "http://127.0.0.1:29991",
        "platform_commit": "c" * 40,
        "fixture_profile": {"id": "controlled-v1", "version": 1},
        "required_migrations": [],
        "resource_bindings": {},
    }
    target_file = directory / "target.json"
    target_file.write_text(json.dumps(target), encoding="utf-8")
    output = directory / "result"
    requests, submissions = [], []
    permissions = [
        "evaluation.read",
        "evaluation.manage",
        "evaluation.run",
        "work.read",
        "work.manage",
        "work.execute",
        "agents.manage",
        "knowledge.read",
        "knowledge.manage",
        "knowledge.search",
    ]

    def response(code, value):
        return httpx.Response(code, json=value)

    def handler(request):
        path = request.url.path
        value = (
            json.loads(request.content)
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        requests.append((request.method, path))
        if path == "/api/v1/auth/login":
            return response(200, {"ok": True})
        if path == "/api/v1/auth/me":
            return response(200, {"is_admin": True})
        if path == "/api/v1/agent-evaluations/environment":
            return response(
                200,
                {
                    "database_is_test": True,
                    "environment_id": environment,
                    "owner_id": owner,
                    "platform_commit": "c" * 40,
                    "fixture_profile": {"id": "controlled-v1", "version": 1},
                    "schema_migrations": [],
                    "permissions": permissions,
                    "sandbox_namespace": seed["sandbox_namespace"],
                },
            )
        if path == "/api/v1/work-capabilities":
            return response(200, {"enabled": True})
        if path == "/api/v1/agent-evaluations/profiles/capabilities":
            return response(
                200,
                {
                    "observers": ["root-model-context-v1"],
                    "case_limits": {
                        "schema_version": 1,
                        "enforcement": "controlled-case-reserve-v1",
                        "scope": "evaluation-owner-and-case",
                        "profiles": helpers.facts.LIMITS,
                    },
                    "team_profiles": {
                        "protocol": "owned-team-native-effects-v2",
                        "audit": "controlled-team-effects-v2",
                        "native_guard": "native-work-model-tool-v1",
                        "member_resource_context": "work-member-resource-v1",
                        "definition_versions": {identifier: 2},
                        "case_state": {
                            identifier: {
                                "observed": True,
                                "complete": True,
                                "calls": 0,
                                "tokens": 0,
                                "pending": 0,
                                "unknown_roots": 0,
                                "roots": [],
                            }
                        },
                    },
                    "work_manager": {"ready": True},
                    "sandbox_limits": {
                        "enforcement": "controlled-sandbox-reserve-v1",
                        "scope": "test-environment",
                        "max_created": 2,
                        "attempts": len(submissions),
                        "storage_healthy": True,
                    },
                },
            )
        if path == "/api/v1/admin/models":
            role = (
                "embedding"
                if value["kind"] == "embedding"
                else "child"
                if " reviewer " in value["name"]
                else "lead"
            )
            return response(201, {"id": role, "revision": 1})
        if path.endswith("/model-bindings"):
            return response(200, {"sealed": True})
        if path == "/api/v1/agents":
            return response(
                201,
                {
                    "id": fixture["reviewer_agent_id"]
                    if value["model_id"] == "child"
                    else fixture["agent_id"],
                    "revision": 1,
                },
            )
        if path.endswith("/publish"):
            return response(
                201,
                {
                    "id": fixture["reviewer_version_id"]
                    if fixture["reviewer_agent_id"] in path
                    else fixture["agent_version_id"],
                    "version": 1,
                },
            )
        if path == "/api/v1/work-tasks":
            return response(201, {"id": fixture["id"], "revision": 1})
        if path.endswith("/files"):
            return response(201, {"id": str(uuid4()), "revision": 2})
        if path.endswith("/runs") and request.method == "POST":
            assert (
                value["agent_id"] == fixture["agent_id"]
                and value["agent_version_id"] == fixture["agent_version_id"]
            )
            assert value["token_limit"] == (16000 if identifier == "AET-W06" else 20000)
            assert value["step_limit"] == (6 if identifier == "AET-W06" else 10)
            submissions.append(value)
            return response(202, {"id": root, "status": "PENDING"})
        work = "/api/v1/work-runs/" + root
        if path == work:
            return response(
                200, {**body["observation"]["run"], "environment": "coding"}
            )
        if path == work + "/usage":
            return response(200, {"total": body["native_work"]})
        if path == work + "/operations":
            return response(200, {"items": []})
        if path == work + "/artifacts":
            return response(
                200,
                {
                    "items": [
                        {**item, "path": "/input.txt"} for item in body["artifacts"]
                    ]
                },
            )
        if path == work + "/events":
            after = int(request.url.params.get("after", "0"))
            return response(
                200,
                {
                    "items": body["events"] if after == 0 else [],
                    "next_after": len(body["events"]),
                },
            )
        if (
            path
            == "/api/v1/agent-evaluations/profiles/team-runs/" + root + "/observations"
        ):
            assert len(submissions) == 1
            return response(200, body["observation"])
        if path == "/api/v1/resource-runs/work/" + root + "/configurations":
            return response(
                200,
                {
                    **body["trace"],
                    "configuration_scope": {"mode": "work", "run_id": root},
                },
            )
        baseline = body["baseline"]
        if baseline:
            base = helpers.facts.BASE
            kb, document, operation = (
                fixture["knowledge_id"],
                fixture["document_id"],
                baseline["operation_id"],
            )
            if path == base:
                return (
                    response(201, {"id": kb})
                    if request.method == "POST"
                    else response(200, {"items": [baseline["knowledge"]]})
                )
            if path == f"{base}/{kb}/documents":
                return (
                    response(201, {"id": document, "revision": 1})
                    if request.method == "POST"
                    else response(200, {"items": [baseline["document"]]})
                )
            if path.endswith("/chunks"):
                return response(
                    200,
                    {
                        "items": [
                            {
                                "id": baseline["chunk_id"],
                                "content": baseline["content"],
                                "document_revision": 1,
                            }
                        ]
                    },
                )
            if path.endswith("/revisions"):
                return response(200, {"items": baseline["revisions"]})
            if path == f"{base}/{kb}/operations":
                return response(
                    200,
                    {"items": [{**baseline["operation"], "usage": baseline["usage"]}]},
                )
            if path == f"/api/v1/resource-runs/resource/{operation}/configurations":
                return response(
                    200,
                    {
                        **baseline["trace"],
                        "configuration_scope": {
                            "mode": "resource",
                            "run_id": operation,
                        },
                    },
                )
            if (
                path
                == f"/api/v1/agent-evaluations/profiles/runs/resource/{operation}/observations"
            ):
                return response(200, baseline["provider"])
        raise AssertionError(
            "Unexpected fixed contract request: " + request.method + " " + path
        )

    original = module.Platform

    class MockPlatform(original):
        def __init__(self, target_value):
            super().__init__(target_value, transport=httpx.MockTransport(handler))

    module.Platform = MockPlatform
    os.environ["EVALUATION_USERNAME"] = "synthetic-private-test"
    os.environ["EVALUATION_PASSWORD"] = "synthetic-private-test-not-a-real-credential"
    exits = []
    for action in ("preflight", "init", "run", "cleanup"):
        sys.argv = [
            "fixed_run",
            action,
            "--case-id",
            identifier,
            "--target-manifest",
            str(target_file),
            "--output",
            str(output),
        ]
        exits.append(module.main())
    from evaluation_runner.fixed_audit import _validate_ready

    ready = _validate_ready(output)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    journal = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    index = json.loads(
        (output / "network-audit-index.json").read_text(encoding="utf-8")
    )
    assert exits == [0, 0, 1, 1]
    assert len(submissions) == 1 and ready["gate"] == report["gate"] == "NEEDS_REVIEW"
    assert report["attempts"][0]["platform_status"] == "NEEDS_REVIEW"
    assert report["attempts"][0]["fact_results"][0]["status"] == "PASS"
    assert report["attempts"][0]["cleanup_status"] != "CLEAN"
    assert len(index["sessions"]) == len(ready["network_audit_refs"]) == 4
    prior_count = len(requests)
    helpers.facts.execute(
        None, SimpleJournal(output, journal), journal["attempts"][0], specimen
    )
    assert (
        len(requests) == prior_count
    )  # Repeated read uses sealed evidence; no new business POST or GET.
    result = {
        "scope": "MockTransport CLI engineering only; not a real target/provider/DB",
        "case_id": identifier,
        "exits": exits,
        "gate": ready["gate"],
        "sessions": 4,
        "business_submissions": len(submissions),
        "cleanup": report["attempts"][0]["cleanup_status"],
        "ready_verified": True,
    }
    (directory / "engineering-result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


class SimpleJournal:
    def __init__(self, directory, data):
        self.directory, self.data = directory, data


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]), sys.argv[2])))
