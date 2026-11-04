"""Private formal HTTP/ASGI and real DeepAgents delegation with controlled DB/SDK only."""

import base64
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import evaluation_target as target
import evaluation_team_audit as audit_module
import evaluation_team_observer as observer
import httpx
import pytest
from evaluation_runner import fixed_team_evidence as facts
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app import team_resource_context as bridge
from app import team_runtime as team
from app import work_shared_budget_guard as budget
from app.config import ModelConfig
from app.errors import APIError
from app.graphs import build_work
from app.resource_runtime import ResourceRun, active_resources
from app.team_context import active_member

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


fixture_module = load(
    "engineering_synthetic_team_http_fixture", HERE / "team_test_fixture.py"
)


def case(identifier):
    from evaluation_runner.contracts import Dataset

    return next(
        row
        for row in Dataset.model_validate_json(
            facts.DEFINITIONS_PATH.read_text(encoding="utf-8")
        ).cases
        if row.case_id == identifier
    )


@pytest.mark.parametrize("case_id", ["AET-W05", "AET-W06"])
def test_retained_native_facts_recompute_control_without_changing_raw_NEEDS_REVIEW(
    tmp_path, case_id
):
    from evaluation_runner.journal import Journal

    specimen = case(case_id)
    data, attempt, snapshot = fixture_module.build(specimen)
    result, evidence = facts.evaluate(snapshot, specimen, attempt)
    assert result[0].status == "PASS" and evidence["run"]["status"] == "NEEDS_REVIEW"
    assert evidence["usage_complete"] is (case_id == "AET-W06")
    journal = Journal(tmp_path / "results")
    try:
        journal.data = data
        facts.retain(journal, attempt, "team-baseline", snapshot["body"]["baseline"])
        facts.retain(
            journal, attempt, "team-facts", snapshot["body"], snapshot["usage"]
        )
        verified = facts.verified(journal.directory, data, attempt, specimen)
        assert verified["usage"]["calls"] == (3 if case_id == "AET-W05" else 7)
        assert facts.cleanup_ready(journal.directory, data, attempt, specimen) is False
        assert facts.cleanup(None, journal, attempt, specimen) == "NEEDS_REVIEW"
        assert attempt.get("cleanup_status") != "CLEAN"
    finally:
        journal.close()


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "goal",
        "task_revision",
        "raw_success",
        "member_status",
        "member_model",
        "member_revision",
        "model_snapshot",
        "missing_read",
        "lost_unknown",
        "audit_unknown",
        "provider_unobserved",
        "extra_owner_effect",
        "sum_missing",
        "noncontiguous",
    ],
)
@pytest.mark.parametrize("case_id", ["AET-W05", "AET-W06"])
def test_facts_reject_foreign_missing_or_reclassified_native_failure(case_id, fault):
    _data, attempt, snapshot = fixture_module.build(case(case_id))
    observation = snapshot["body"]["observation"]
    if fault == "owner":
        observation["owner_id"] = str(uuid4())
    if fault == "goal":
        observation["run"]["goal_digest"] = "0" * 64
    if fault == "task_revision":
        observation["run"]["task_revision"] = 1
    if fault == "raw_success":
        observation["run"]["status"] = "COMPLETED"
    if fault == "member_status":
        observation["members"][0]["status"] = (
            "FAILED" if case_id == "AET-W05" else "UNKNOWN"
        )
    if fault == "member_model":
        observation["ledger"][1]["model_id"] = "unsealed-same-name"
    if fault == "member_revision":
        observation["ledger"][1]["model_revision"] = 2
    if fault == "model_snapshot":
        snapshot["body"]["trace"]["items"][1]["snapshot"]["kind"] = "embedding"
    if fault == "missing_read":
        observation["audit"]["records"].pop(0)
    if fault == "lost_unknown":
        observation["provider"]["complete"] = not observation["provider"]["complete"]
    if fault == "audit_unknown":
        observation["audit"]["complete"] = False
    if fault == "provider_unobserved":
        observation["provider"]["observed"] = False
    if fault == "extra_owner_effect":
        observation["effects"]["owner_workflow_runs"] += 1
    if fault == "sum_missing":
        snapshot["usage"]["calls"] -= 1
    if fault == "noncontiguous":
        snapshot["body"]["events"][-1]["event_id"] += 1
    try:
        results, _ = facts.evaluate(snapshot, case(case_id), attempt)
    except (ValueError, facts.adapters.Blocked):
        return
    assert any(row.status != "PASS" for row in results)


@pytest.mark.parametrize(
    "fault",
    [
        "query_as_index",
        "query_empty",
        "query_fallback",
        "wrong_quote",
        "index_unknown",
        "denial_missing",
        "later_provider",
        "written_canary",
        "bool_provider",
        "bool_index",
    ],
)
def test_w06_requires_real_in_run_query_and_actual_post_budget_denial(fault):
    from evaluation_runner.fixed_evidence import text_digest

    _data, attempt, snapshot = fixture_module.build(case("AET-W06"))
    body, observation = snapshot["body"], snapshot["body"]["observation"]
    if fault == "query_as_index":
        observation["ledger"][3]["run_id"] = body["baseline"]["operation_id"]
    if fault == "query_empty":
        observation["audit"]["records"][1]["result"]["retrieval"]["items"] = []
    if fault == "query_fallback":
        observation["audit"]["records"][1]["result"]["retrieval"]["mode"] = "keyword"
    if fault == "wrong_quote":
        observation["audit"]["records"][1]["result"]["retrieval"]["items"][0][
            "text"
        ] = "not original"
    if fault == "index_unknown":
        body["baseline"]["operation"]["status"] = "UNKNOWN"
    if fault == "denial_missing":
        observation["audit"]["records"][-1]["status"] = "SUCCEEDED"
    if fault == "later_provider":
        observation["provider"]["calls"] += 1
    if fault == "written_canary":
        body["artifacts"][0]["path_sha256"] = text_digest("/budget-after.txt")
    if fault == "bool_provider":
        observation["provider"]["pending"] = False
    if fault == "bool_index":
        body["baseline"]["provider"]["controlled_model"]["provider_calls"] = True
    try:
        results, _ = facts.evaluate(snapshot, case("AET-W06"), attempt)
    except (ValueError, facts.adapters.Blocked):
        return
    assert any(row.status != "PASS" for row in results)


def test_any_evidence_save_failure_poison_survives_current_run_without_new_effect(
    tmp_path,
):
    from evaluation_runner.journal import Journal

    data, attempt, snapshot = fixture_module.build(case("AET-W05"))
    journal = Journal(tmp_path / "failed")
    journal.data = data

    def failed():
        raise RuntimeError("Controlled non-IO persistence failure")

    journal.save = failed
    try:
        with pytest.raises(RuntimeError):
            facts.retain(
                journal, attempt, "team-facts", snapshot["body"], snapshot["usage"]
            )
        with pytest.raises(facts.adapters.Blocked):
            facts.verified(journal.directory, data, attempt, case("AET-W05"))
        with pytest.raises(facts.adapters.Blocked):
            facts.baseline(None, journal, attempt, case("AET-W05"))
    finally:
        journal.close()


@pytest.mark.parametrize(
    "fault,expected",
    [
        (None, 200),
        ("foreign_owner", 404),
        ("unsealed", 403),
        ("foreign_namespace", 503),
    ],
)
async def test_real_target_ASGI_observer_is_closed_owned_and_does_not_return_snapshot_secrets(
    monkeypatch, fault, expected
):
    data, attempt, snapshot = fixture_module.build(case("AET-W06"))
    observation = snapshot["body"]["observation"]
    fixture = attempt["fixture"]
    actor = str(uuid4()) if fault == "foreign_owner" else data["owner_id"]
    proof = {
        "owner_id": actor,
        "environment_id": data["environment_id"],
        "sandbox_namespace": attempt["sandbox_namespace"],
    }

    async def environment(request):
        return proof

    monkeypatch.setattr(observer, "environment", environment)
    models = {
        "lead": {"owner_id": data["owner_id"], "case_id": "AET-W06", "role": "lead"},
        "child": {
            "owner_id": str(uuid4()) if fault == "unsealed" else data["owner_id"],
            "case_id": "AET-W06",
            "role": "reviewer",
        },
    }
    raw = {
        "goal": case("AET-W06").input,
        **deepcopy(observation["run"]),
        **deepcopy(observation["effects"]),
        "namespace": str(uuid4())
        if fault == "foreign_namespace"
        else attempt["sandbox_namespace"],
        "members": observation["members"],
        "ledger": observation["ledger"],
        "sessions": observation["sessions"],
        "agent_snapshot": {
            "id": fixture["agent_id"],
            "published_version_id": fixture["agent_version_id"],
            "instructions": "PRIVATE_INSTRUCTION_SENTINEL",
            "team_pins": [
                {
                    "name": "reviewer",
                    "version_id": fixture["reviewer_version_id"],
                    "snapshot": {
                        "id": fixture["reviewer_agent_id"],
                        "model_id": "child",
                        "instructions": "PRIVATE_CHILD_SENTINEL",
                    },
                }
            ],
        },
    }

    class DB:
        async def fetch(self, query, params):
            assert "WHERE r.id=%s AND r.owner_id=%s" in query
            assert tuple(map(str, params)) == (attempt["platform_root"]["id"], actor)
            return [deepcopy(raw)] if actor == data["owner_id"] else []

    app = FastAPI()

    @app.exception_handler(APIError)
    async def error(request, exception):
        return JSONResponse(
            {"error": {"code": exception.code}}, status_code=exception.status
        )

    app.state.db = DB()
    app.state.evaluation_profiles = SimpleNamespace(
        lock=Lock(), models=models, case_summary=lambda *_: observation["provider"]
    )
    app.state.evaluation_team_audit = SimpleNamespace(
        projection=lambda *_: observation["audit"]
    )
    observer.attach(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://asgi-test"
    ) as client:
        response = await client.get(
            "/api/v1/agent-evaluations/profiles/team-runs/"
            + attempt["platform_root"]["id"]
        )
    assert response.status_code == 404  # Only the exact fixed route is registered.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://asgi-test"
    ) as client:
        response = await client.get(
            "/api/v1/agent-evaluations/profiles/team-runs/"
            + attempt["platform_root"]["id"]
            + "/observations"
        )
    assert response.status_code == expected
    assert (
        "PRIVATE_" not in response.text
        and "lease_token" not in response.text
        and "SELECT" not in response.text
    )
    if expected == 200:
        assert response.json()["ledger"] == observation["ledger"]
        assert response.json()["run"]["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "expired",
        "foreign_member",
        "late_revoked",
        "child_kb_removed",
        "frozen_kb_missing",
        "child_kb_missing",
    ],
)
async def test_real_WorkKnowledge_HTTP_bridge_restores_verified_member_in_real_resource_run(
    monkeypatch, fault
):
    from app import work_resources as resource
    from app.knowledge import KnowledgeService
    from app.resource_runtime import current_scope

    owner, root, lease, member_id, version, parent, knowledge = [
        uuid4() for _ in range(7)
    ]
    selection = {
        "id": str(parent),
        "knowledge_ids": [str(knowledge)],
        "team_pins": [
            {
                "name": "reviewer",
                "version_id": str(version),
                "snapshot": {
                    "model_id": "child",
                    "knowledge_ids": []
                    if fault == "frozen_kb_missing"
                    else [str(knowledge)],
                },
            }
        ],
    }
    queries, starts = [], []

    class DB:
        member_reads = 0

        async def fetch(self, query, params):
            queries.append(query)
            if "FROM knowledge_bases" in query:
                return [{"id": knowledge, "owner_id": owner}]
            if "FROM team_calls t" in query:
                self.member_reads += 1
                assert params == (str(parent), member_id, root, owner, lease, lease)
                if (
                    fault == "foreign_member"
                    or fault == "late_revoked"
                    and self.member_reads > 1
                ):
                    return []
                return [
                    {
                        "id": member_id,
                        "role_name": "reviewer",
                        "version_id": version,
                        "model_id": "child",
                        "current_team": [
                            {"name": "reviewer", "version_id": str(version)}
                        ],
                        "child_knowledge_ids": []
                        if fault == "child_kb_missing"
                        or fault == "child_kb_removed"
                        and self.member_reads > 1
                        else [str(knowledge)],
                    }
                ]
            if "FROM work_queue_runs" in query:
                return (
                    []
                    if fault == "expired"
                    else [{"owner_id": owner, "agent_snapshot": selection}]
                )
            if "FROM agents" in query:
                return [
                    {
                        "knowledge_ids": [str(knowledge)],
                        "team": [{"name": "reviewer", "version_id": str(version)}],
                    }
                ]
            if "FROM run_resource_stages" in query:
                return [{"phase": 1}]
            raise AssertionError("Unexpected query type")

        async def execute(self, query, params):
            assert "INSERT INTO run_resource_stages" in query

    db = DB()

    class Knowledge:
        database = db
        search = KnowledgeService.search

        async def _search(
            self, identifier, owner_id, query, limit=5, *, usage_context=None
        ):
            await usage_context["guard"]()
            scope = current_scope()
            starts.append(
                {
                    "owner": str(scope.owner),
                    "mode": scope.mode,
                    "root": str(scope.run),
                    "member": dict(active_member.get()),
                }
            )
            return {"mode": "hybrid", "items": []}

    app = FastAPI()
    app.state.db, app.state.knowledge = db, Knowledge()

    @app.exception_handler(APIError)
    async def error(request, exception):
        return JSONResponse(
            {"error": {"code": exception.code}}, status_code=exception.status
        )

    @app.middleware("http")
    async def genuine_http_context_boundary(request, call_next):
        first, second = active_member.set(None), active_resources.set(None)
        try:
            return await call_next(request)
        finally:
            active_member.reset(first)
            active_resources.reset(second)

    app.include_router(resource.router)
    original = httpx.AsyncClient

    def client(**kwargs):
        return original(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(resource.httpx, "AsyncClient", client)
    target_context = active_member.set(
        {"id": member_id, "role_name": "reviewer", "model_id": "child", "revision": 1}
    )
    try:
        provider = resource.WorkKnowledge(
            db,
            {"id": root, "owner_id": owner, "lease_token": lease},
            "http://127.0.0.1:29991",
            "http://127.0.0.1:29991",
        )
        if fault:
            with pytest.raises(RuntimeError):
                await provider.search(knowledge, owner, "星桥 保修")
            assert starts == []
        else:
            result = await provider.search(knowledge, owner, "星桥 保修")
            assert result["mode"] == "hybrid"
            assert starts == [
                {
                    "owner": str(owner),
                    "mode": "work",
                    "root": str(root),
                    "member": {"id": member_id, "role_name": "reviewer"},
                }
            ]
            assert (
                db.member_reads == 2
            )  # Admission and actual native-resource authority both recheck live state.
        assert active_member.get()["id"] == member_id
    finally:
        active_member.reset(target_context)


@pytest.mark.parametrize("case_id", ["AET-W05", "AET-W06"])
def test_full_fixed_CLI_MockTransport_retains_review_facts_exact_four_audits_and_ready(
    tmp_path, case_id
):
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
    }
    env.update(
        {
            key: str(tmp_path)
            for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
        }
    )
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        LANGSMITH_TRACING="false",
        PYTHONPATH=os.pathsep.join(
            str(ROOT / path)
            for path in (
                "services/api",
                "services/evaluation/src",
                "services/evaluation/profile_support",
            )
        ),
    )
    result = subprocess.run(
        [sys.executable, str(HERE / "mock_cli_contract.py"), str(tmp_path), case_id],
        env=env,
        check=False,
        capture_output=True,
        timeout=60,
    )
    (tmp_path / "mock-cli-stdout.bin").write_bytes(result.stdout)
    (tmp_path / "mock-cli-stderr.bin").write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    value = json.loads(
        (tmp_path / "engineering-result.json").read_text(encoding="utf-8")
    )
    assert (
        value["sessions"] == 4
        and value["business_submissions"] == 1
        and value["ready_verified"] is True
    )
    assert value["gate"] == "NEEDS_REVIEW" and value["cleanup"] != "CLEAN"


def test_fixed_v2_definitions_require_exact_new_version_and_separate_work_index_budget():
    from evaluation_runner.contracts import Dataset

    for identifier in ("AET-W05", "AET-W06"):
        specimen = case(identifier)
        assert facts.supported(specimen)
        prior = next(
            row
            for row in Dataset.model_validate_json(
                (HERE / "legacy-team-definitions-v1.json").read_text(encoding="utf-8")
            ).cases
            if row.case_id == identifier
        )
        assert not facts.supported(prior)
        altered = specimen.model_copy(update={"input": "another task"})
        assert not facts.supported(altered)
    specimen = case("AET-W06")
    assert specimen.fixture_parameters["fixed_inputs"]["token_limit"] == 16000
    assert (
        specimen.budgets.max_tokens == 16007 and specimen.budgets.max_model_calls == 7
    )


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing_state",
        "unknown",
        "pending",
        "calls_exhausted",
        "tokens_exhausted",
        "roots_exhausted",
        "wrong_scope",
    ],
)
def test_case_readiness_uses_real_persistent_remaining_case_budget_before_fresh_submission(
    fault,
):
    state = {
        "observed": True,
        "complete": True,
        "calls": 1,
        "tokens": 7,
        "pending": 0,
        "unknown_roots": 0,
        "roots": [
            {"mode": "resource", "run_id": str(uuid4()), "calls": 1, "tokens": 7}
        ],
    }
    capabilities = {
        "team_profiles": {
            "protocol": "owned-team-native-effects-v2",
            "audit": "controlled-team-effects-v2",
            "native_guard": "native-work-model-tool-v1",
            "member_resource_context": "work-member-resource-v1",
            "definition_versions": {"AET-W06": 2},
            "case_state": {"AET-W06": state},
        },
        "case_limits": {
            "schema_version": 1,
            "enforcement": "controlled-case-reserve-v1",
            "scope": "evaluation-owner-and-case",
            "profiles": facts.LIMITS,
        },
    }
    if fault == "missing_state":
        capabilities["team_profiles"]["case_state"] = {}
    if fault == "unknown":
        state.update(complete=False, unknown_roots=1)
    if fault == "pending":
        state["pending"] = 1
    if fault == "calls_exhausted":
        state["calls"] = 7
    if fault == "tokens_exhausted":
        state["tokens"] = 16007
    if fault == "roots_exhausted":
        state["roots"].append({"mode": "work", "run_id": str(uuid4())})
    if fault == "wrong_scope":
        capabilities["case_limits"]["scope"] = "untrusted"
    if fault:
        with pytest.raises(facts.adapters.Blocked):
            facts.readiness(None, case("AET-W06"), capabilities)
    else:
        facts.readiness(None, case("AET-W06"), capabilities)


@pytest.mark.parametrize("case_id", ["AET-W05", "AET-W06"])
def test_formal_fixture_HTTP_provisions_real_parent_member_versions_and_bound_task(
    tmp_path, case_id
):
    from evaluation_runner.client import Platform
    from evaluation_runner.contracts import Target
    from evaluation_runner.fixed_fixtures import FixedFixtures
    from evaluation_runner.journal import Journal

    owner, environment, namespace = [str(uuid4()) for _ in range(3)]
    target_value = Target.model_validate(
        {
            "schema_version": 1,
            "environment_id": environment,
            "owner_id": owner,
            "api_base_url": "http://127.0.0.1:29991",
            "platform_commit": "a" * 40,
            "fixture_profile": {"id": "controlled-v1", "version": 1},
            "required_migrations": [],
            "resource_bindings": {},
        }
    )
    requests, agents, seals = [], {}, []

    def transport(request):
        path = request.url.path
        if path == "/api/v1/agent-evaluations/environment":
            return httpx.Response(
                200,
                json={
                    "database_is_test": True,
                    "environment_id": environment,
                    "owner_id": owner,
                    "platform_commit": "a" * 40,
                    "fixture_profile": {"id": "controlled-v1", "version": 1},
                    "schema_migrations": [],
                    "sandbox_namespace": namespace,
                    "permissions": [],
                },
            )
        body = (
            json.loads(request.content)
            if request.headers.get("content-type", "").startswith("application/json")
            else None
        )
        requests.append((request.method, path, body))
        if path == "/api/v1/agents":
            identifier = str(uuid4())
            agents[identifier] = body
            return httpx.Response(201, json={"id": identifier, "revision": 1})
        if path.endswith("/publish"):
            return httpx.Response(201, json={"id": str(uuid4()), "version": 1})
        if path.endswith("/model-bindings"):
            seals.append(body)
            return httpx.Response(200, json={"sealed": True})
        if path.endswith("/files"):
            return httpx.Response(201, json={"id": str(uuid4()), "revision": 2})
        if path.endswith("/documents"):
            return httpx.Response(201, json={"id": str(uuid4()), "revision": 1})
        return httpx.Response(201, json={"id": str(uuid4()), "revision": 1})

    platform = Platform(target_value, transport=httpx.MockTransport(transport))
    journal = Journal(tmp_path / "journal")
    attempt = {"attempt_id": str(uuid4())}
    try:
        fixtures = FixedFixtures(platform, journal, attempt)
        lead = fixtures.model(case_id)
        result = facts.initialize(fixtures, lead, case(case_id))
        assert {row["role"] for row in seals} == (
            {"lead", "reviewer", "embedding"}
            if case_id == "AET-W06"
            else {"lead", "reviewer"}
        )
        parent = agents[result["agent_id"]]
        child = agents[result["reviewer_agent_id"]]
        assert (
            parent["model_id"] == lead
            and child["model_id"] == result["reviewer_model_id"]
        )
        assert parent["team"][0]["version_id"] == result["reviewer_version_id"]
        assert parent["knowledge_ids"] == child["knowledge_ids"]
        assert result["revision"] == 2 and all(
            row["status"] == "CONFIRMED" for row in attempt["fixture_intents"].values()
        )
        assert not any(path.endswith("/runs") for _, path, _ in requests)
    finally:
        journal.close()
        platform.close()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "foreign_owner",
        "expired",
        "cancelled",
        "unpublished",
        "removed",
        "wrong_model",
    ],
)
async def test_internal_member_bridge_uses_real_call_ID_only_and_resets_context(fault):
    owner, root, lease, member_id, version, knowledge = [uuid4() for _ in range(6)]
    row = {
        "id": member_id,
        "role_name": "reviewer",
        "version_id": version,
        "model_id": "sealed-child",
    }
    selection = {
        "id": str(uuid4()),
        "team_pins": [
            {
                "name": "reviewer",
                "version_id": str(version),
                "snapshot": {
                    "model_id": "sealed-child",
                    "knowledge_ids": [str(knowledge)],
                },
            }
        ],
    }
    current = {"team": [{"name": "reviewer", "version_id": str(version)}]}

    class DB:
        async def fetch(self, query, params):
            assert params == (selection["id"], member_id, root, owner, lease, lease)
            for text in (
                "t.owner_id=%s",
                "lease_until>now()",
                "NOT r.cancel_requested",
                "v.active",
                "a.enabled",
                "NOT t.cancel_requested",
            ):
                assert text in query
            return (
                []
                if fault in {"foreign_owner", "expired", "cancelled", "unpublished"}
                else [row]
            )

    if fault == "removed":
        current["team"] = []
    if fault == "wrong_model":
        row["model_id"] = "same-name-unsealed-ID"
    row["current_team"] = current["team"]
    row["child_knowledge_ids"] = [str(knowledge)]

    async def use():
        async with bridge.member_context(
            DB(), owner, root, lease, selection, member_id, knowledge
        ):
            assert active_member.get() == {"id": member_id, "role_name": "reviewer"}
            raise RuntimeError("consumer fails after validated attribution")

    with pytest.raises(APIError if fault else RuntimeError):
        await use()
    assert active_member.get() is None


@pytest.mark.parametrize("case_id", ["AET-W05", "AET-W06"])
async def test_real_DeepAgents_parent_member_read_failure_or_shared_embedding_budget(
    tmp_path, monkeypatch, case_id
):
    from evaluation_embeddings import install_embeddings
    from langchain_core.tools import StructuredTool

    import app.resource_runtime as runtime
    import app.work_engine as engine

    owner, root, lease = uuid4(), uuid4(), uuid4()
    state = target.ProfileState(tmp_path / "profiles.json")
    state.register_model("lead", case_id, "lead", owner)
    state.register_model("child", case_id, "reviewer", owner)
    child_agent, child_version = str(uuid4()), str(uuid4())
    config = ModelConfig(
        id="child",
        name="renamed ordinary label",
        model="eval::" + case_id + "::v1",
        kind="chat",
        protocol="openai",
        base_url="http://127.0.0.1:29991",
        supports_tools=True,
        max_output_tokens=4096,
    )

    class DB:
        async def fetch(self, query, params):
            summary = state.root_summary(owner, "work", root)
            return [
                {
                    "token_limit": 16000 if case_id == "AET-W06" else 20000,
                    "used_tokens": summary.get("tokens") or 0,
                    "known_tokens": summary.get("tokens") or 0,
                    "usage_unknown": False,
                    "unsettled": 0,
                }
            ]

    embedding_calls = []
    knowledge_id = uuid4()

    class Meter:
        async def start(self, texts):
            member = active_member.get()
            embedding_calls.append(("start", member["role_name"] if member else None))
            return uuid4()

        async def finish(self, receipt, usage):
            embedding_calls.append(("finish", usage["total_tokens"]))

        async def error(self, receipt, error):
            raise AssertionError("Deterministic embedding must not fail")

    class Knowledge:
        embedder = None
        reranker = None

        async def search(self, identifier, owner_id, query, limit=5, **kwargs):
            return await self._search(identifier, owner_id, query, limit, **kwargs)

        async def _search(self, identifier, owner_id, query, limit=5, **kwargs):
            assert (
                case_id == "AET-W06"
                and identifier == knowledge_id
                and query == "星桥 保修"
            )
            assert active_member.get()["role_name"] == "reviewer"
            vectors = await self.embedder(
                config.model_copy(update={"id": "embedding", "kind": "embedding"}),
                [query],
                meter=Meter(),
            )
            assert len(vectors) == 1
            return {
                "mode": "hybrid",
                "items": [{"text": "星桥产品保修三年，须提供发票。"}],
                "warning": None,
            }

    db = DB()
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(data_dir=tmp_path), db=db, knowledge=Knowledge()
        )
    )
    restore = audit_module.install(app, state)
    restore_embeddings = install_embeddings(app, state)
    commands, finished = [], []

    async def sdk_command(command, **kwargs):
        body = json.loads(base64.b64decode(shlex.split(command)[-1]))
        commands.append(body)
        assert body == {"operation": "read", "path": "/input.txt"}
        raw = b"team-evidence" if case_id == "AET-W05" else b"bounded"
        return SimpleNamespace(
            exit_code=0,
            error=None,
            text=json.dumps({"data": base64.b64encode(raw).decode()}),
        )

    async def guard():
        pass

    async def freeze(kind, identifier, revision, value, **kwargs):
        return value

    monkeypatch.setattr(runtime, "freeze_resource", freeze)
    backend = engine.WorkBackend(
        SimpleNamespace(id=str(uuid4()), commands=SimpleNamespace(run=sdk_command)),
        guard,
    )

    class Models:
        vault = None

        async def get(self, identifier):
            assert identifier == "child"
            return config

    class Capabilities:
        async def for_owner(self, *args, **kwargs):
            if case_id == "AET-W05":
                return [], []

            async def search_knowledge(query: str) -> str:
                """Query only the fixed authorized knowledge source."""
                return json.dumps(
                    [await app.state.knowledge.search(knowledge_id, owner, query)]
                )

            return [StructuredTool.from_function(coroutine=search_knowledge)], []

    class RecordedTeam(team.TeamRun):
        async def check(self, selected):
            await guard()

        async def claim(self, selected, call, arguments):
            return {"id": uuid4()}

        async def finish(self, identifier, status, result, code=None):
            finished.append((status, code))

    snapshot = {
        "id": str(uuid4()),
        "web_enabled": False,
        "team_pins": [
            {
                "name": "reviewer",
                "version_id": child_version,
                "goal": "读取固定输入并审阅",
                "snapshot": {
                    "id": child_agent,
                    "model_id": "child",
                    "instructions": "固定成员审阅任务",
                    "knowledge_ids": [],
                    "skill_ids": [],
                    "mcp_ids": [],
                    "workflow_version_ids": [],
                    "memory_enabled": False,
                    "web_enabled": False,
                },
            }
        ],
    }

    def factory(conf):
        return target.model_factory(conf, state)

    team_run = RecordedTeam(
        db,
        Models(),
        Capabilities(),
        factory,
        root,
        owner,
        "work",
        lease,
        snapshot,
        guard,
    )
    token = active_resources.set(ResourceRun(None, owner, "work", root))
    try:
        if case_id == "AET-W06":
            state.register_model("embedding", case_id, "embedding", owner)
            index = active_resources.set(ResourceRun(None, owner, "resource", uuid4()))
            try:
                await app.state.knowledge.embedder(
                    config.model_copy(update={"id": "embedding", "kind": "embedding"}),
                    ["星桥产品保修三年，须提供发票。"],
                    meter=Meter(),
                )
            finally:
                active_resources.reset(index)
        lead = target.model_factory(config.model_copy(update={"id": "lead"}), state)
        middleware = await team_run.middleware(workspace=backend)
        graph = build_work(
            lead,
            InMemorySaver(),
            backend=backend,
            middleware=[
                budget.WorkSharedBudgetGuard(
                    db, {"id": root, "owner_id": owner, "lease_token": lease}
                ),
                *middleware,
            ],
        )
        with pytest.raises(APIError) as caught:
            await graph.ainvoke(
                {"messages": [HumanMessage(content=case(case_id).input)]},
                {"configurable": {"thread_id": str(root)}},
            )
        assert caught.value.code == (
            "TEAM_RESULT_UNKNOWN" if case_id == "AET-W05" else "WORK_BUDGET_EXCEEDED"
        ), (
            repr(caught.value.__context__),
            state.case_summary(owner, case_id),
            embedding_calls,
            app.state.evaluation_team_audit.projection(owner, root),
        )
        assert commands == [{"operation": "read", "path": "/input.txt"}]
        assert finished == (
            [("UNKNOWN", "TEAM_RESULT_UNKNOWN")]
            if case_id == "AET-W05"
            else [("SUCCEEDED", None)]
        )
        summary = state.case_summary(owner, case_id)
        assert summary["calls"] == (3 if case_id == "AET-W05" else 7)
        assert summary["unknown_roots"] == (1 if case_id == "AET-W05" else 0)
        projection = app.state.evaluation_team_audit.projection(owner, root)
        assert (
            projection["complete"] is True
            and projection["records"][0]["status"] == "SUCCEEDED"
        )
        if case_id == "AET-W06":
            assert summary["tokens"] == 16007
            assert [row["kind"] for row in projection["records"]] == [
                "member-read",
                "query",
                "budget-write",
            ]
            assert projection["records"][-1]["result"] == {
                "tool_call_id": "ae-budget-denied-write",
                "provider_calls_before": 6,
                "tokens_before": 16000,
            }
            assert embedding_calls == [
                ("start", None),
                ("finish", 7),
                ("start", "reviewer"),
                ("finish", 7),
            ]
    finally:
        active_resources.reset(token)
        restore_embeddings()
        restore()


async def test_internal_member_None_keeps_old_lead_path_without_member_queries():
    class DB:
        async def fetch(self, *args):
            raise AssertionError("Lead path must not query member authority")

    before = active_member.get()
    async with bridge.member_context(
        DB(), uuid4(), uuid4(), uuid4(), {}, None, uuid4()
    ) as authority:
        assert authority is None and active_member.get() is before
    assert active_member.get() is before


@pytest.mark.parametrize("position", range(6))
def test_evaluate_rejects_fixed_W06_per_call_token_shift_even_trace_and_totals_agree(
    position,
):
    specimen = case("AET-W06")
    _data, attempt, snapshot = fixture_module.build(specimen)
    calls = snapshot["body"]["observation"]["ledger"]
    donor = (position + 1) % len(calls)
    calls[position]["input_tokens"] += 1
    calls[donor]["input_tokens"] -= 1
    changed = {row["call_id"]: row for row in calls}
    for item in snapshot["body"]["trace"]["items"]:
        for row in item["calls"]:
            row["input_tokens"] = changed[row["call_id"]]["input_tokens"]
    with pytest.raises(ValueError):
        facts.evaluate(snapshot, specimen, attempt)


@pytest.mark.parametrize("position", range(6))
def test_evaluate_rejects_index_call_reused_by_any_work_call(position):
    specimen = case("AET-W06")
    _data, attempt, snapshot = fixture_module.build(specimen)
    calls = snapshot["body"]["observation"]["ledger"]
    snapshot["body"]["baseline"]["trace"]["items"][0]["calls"][0]["call_id"] = calls[
        position
    ]["call_id"]
    with pytest.raises(ValueError):
        facts.evaluate(snapshot, specimen, attempt)


def test_evaluate_rejects_W05_known_token_shift_without_reclassifying_unknown():
    specimen = case("AET-W05")
    _data, attempt, snapshot = fixture_module.build(specimen)
    calls = snapshot["body"]["observation"]["ledger"]
    calls[0]["input_tokens"] += 1
    calls[1]["input_tokens"] -= 1
    changed = {row["call_id"]: row for row in calls}
    for item in snapshot["body"]["trace"]["items"]:
        for row in item["calls"]:
            row["input_tokens"] = changed[row["call_id"]]["input_tokens"]
    with pytest.raises(ValueError):
        facts.evaluate(snapshot, specimen, attempt)


@pytest.mark.parametrize("case_id", ["AET-W05", "AET-W06"])
@pytest.mark.parametrize("missing", ["status", "input_tokens", "output_tokens"])
def test_evaluate_requires_trace_call_complete_native_tuple(case_id, missing):
    specimen = case(case_id)
    _data, attempt, snapshot = fixture_module.build(specimen)
    snapshot["body"]["trace"]["items"][0]["calls"][0].pop(missing)
    with pytest.raises(ValueError):
        facts.evaluate(snapshot, specimen, attempt)
