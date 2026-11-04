"""Explicit isolated PG/ASGI contracts. Synthetic Work metadata, never a real F05 run."""

import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import evaluation_f05_deadline_guard as guard_module
import evaluation_flow_code as code
import evaluation_parent_guard as parent_module
import evaluation_target as target_module
import f05_deadline_contract as proof
import pytest
import test_agent_evaluation_platform as platform_contract
from app import durable_sandboxes as durable
from app.agent_api import AgentInput
from app.agent_api import router as agents
from app.capabilities import CapabilityService
from app.models import CredentialVault, ModelInput, ModelRegistry
from app.resource_runtime import ResourceRun, active_resources
from app.security import COOKIE, identity
from app.workflow_api import router as workflows
from app.workflow_definition import Definition
from evaluation_runner import fixed_case_deadline as parent_clock
from evaluation_runner import fixed_fixtures as fixtures
from evaluation_runner.contracts import Dataset, Target
from evaluation_runner.journal import Journal, atomic_json, digest
from f05_support import ROOT
from fastapi import FastAPI
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

h = platform_contract.h
migration = platform_contract.migration


@pytest.fixture
def f05(h, request, monkeypatch):
    name = conninfo_to_dict(platform_contract.URL).get("dbname", "")
    matched = re.fullmatch(r"owner_agent_evaluation_([0-9a-f]{32})_test", name)
    if not matched:
        pytest.skip("Guard requires a newly owned owner_agent_evaluation_<uuidhex>_test database")
    if h.sql(
        "SELECT 1 FROM work_queue_runs UNION ALL SELECT 1 FROM model_usage "
        "UNION ALL SELECT 1 FROM work_sandbox_sessions LIMIT 1"
    ):
        pytest.skip("An independent empty synthetic Work namespace is required; existing data is retained")
    failures_before = request.session.testsfailed
    child = Path(platform_contract.DATA) / uuid4().hex / "AET-F05-r1-contract"
    environment = str(UUID(matched[1]))
    namespace = str(h.sql("SELECT id FROM work_sandbox_namespace")[0]["id"])
    data = proof.data_directory(child, environment)
    data.mkdir(parents=True)
    dataset = Dataset.model_validate_json(
        (ROOT / "services/evaluation/datasets/a20-fixed.json").read_text(encoding="utf-8")
    )
    specimen = next(case for case in dataset.cases if case.case_id == "AET-F05")
    commit = "3" * 40  # Explicit synthetic identity, not a claim of the current candidate commit.
    context = {
        "schema_version": 1,
        "suite_id": str(uuid4()),
        "catalog_digest": digest(dataset.model_dump(mode="json")),
        "case_definition_digest": digest(specimen.model_dump(mode="json")),
        "repeat_index": 1,
        "case_id": "AET-F05",
        "candidate_commit": commit,
        "rules_id": "controlled-a20-v1",
    }
    assert len(context) == 8  # The actual suite writer has eight fields, not describe's extra quote fields.
    target = Target(
        environment_id=environment,
        owner_id=str(h.owner),
        platform_commit=commit,
        api_base_url="http://127.0.0.1:18429",
        required_migrations=[],
        fixture_profile={"id": "controlled-v1", "version": 1},
    )
    atomic_json(child / "suite-context.json", context)
    atomic_json(child / "target/target-manifest.json", target.model_dump())
    pins = {key: context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id")} | {
        "definition_version": 2
    }
    required = proof.initialize_target(child, environment, str(h.owner), namespace, commit, pins)
    marker = {
        "environment_id": environment,
        "owner_id": str(h.owner),
        "sandbox_namespace": namespace,
        "platform_commit": commit,
        **required,
    }
    atomic_json(child / "target/target-state.json", marker)
    atomic_json(data / "evaluation-instance.json", marker)
    settings = SimpleNamespace(
        data_dir=data,
        database_url=platform_contract.URL,
        evaluation_instance_id=environment,
        evaluation_platform_commit=commit,
    )
    guard = guard_module.create(settings, required["f05_anchor_sha256"])
    parent, profiles = parent_module.ParentGuard(settings), target_module.ProfileState()
    # The h app is already started. Hook installation uses a separate app sharing the real DB,
    # not an invented complete target lifespan or middleware/TCP acceptance claim.
    hook_app = FastAPI()
    hook_app.state.db = h.db
    parent.attach(hook_app, before_effect=guard.before_submission)
    guard.registered.add("submission")
    parent.install_runtime(hook_app, profiles)
    guard.install_runtime(hook_app, profiles, parent)
    assert guard.capability()["installed"] and guard.capability()["phase"] == "AWAITING_RECORD"
    parent_clock.seal(child, target, context, namespace)
    assert guard.capability()["phase"] == "ACTIVE"
    models = ModelRegistry(h.db, CredentialVault(data / "contract-vault.key"))
    model = h.http.portal.call(
        models.save,
        ModelInput(
            name="F05 contract " + uuid4().hex,
            model="eval::AET-F05::v1",
            base_url="http://127.0.0.1:18429/never-called",
            max_output_tokens=4096,
            supports_tools=True,
        ),
    )
    profiles.register_model(model, "AET-F05", "lead", h.owner)
    h.app.state.models = models
    h.app.state.capabilities = CapabilityService(h.db, None, None, model_registry=models)

    def forbidden_model(*args, **kwargs):
        pytest.fail("No provider/model execution is permitted")

    async def forbidden_sdk(*args, **kwargs):
        pytest.fail("No SDK, manager or remote sandbox operation is permitted")

    async def high_budget():
        return 30 * 1024**3  # Controlled scalar, never represented as a real host sample.

    monkeypatch.setattr(durable, "resource_budget", high_budget)
    monkeypatch.setattr(durable, "budget_observation", lambda **kwargs: None)
    monkeypatch.setattr(durable.Sandbox, "create", forbidden_sdk)
    monkeypatch.setattr(durable.Sandbox, "connect", forbidden_sdk)
    monkeypatch.setattr(durable.SandboxManager, "create", forbidden_sdk)
    h.app.state.runtime = SimpleNamespace(model_factory=forbidden_model)
    h.app.include_router(agents)
    h.app.include_router(workflows)
    h.execute(
        "UPDATE platform_roles SET permissions=permissions||%s WHERE id=%s",
        (
            Jsonb(
                ["agents.read", "workflows.read", "workflows.manage", "workflows.execute", "work.read", "work.manage"]
            ),
            h.role,
        ),
    )
    workflow = h.http.post(
        "/api/v1/workflows",
        json={"name": "F05 synthetic " + uuid4().hex, "model_id": model, "definition": code.definition()},
    )
    assert workflow.status_code == 201, workflow.text
    workflow_id = workflow.json()["id"]
    service = code.CodeSandboxes(
        h.db,
        SimpleNamespace(api_key="synthetic-unused-key", use_server_proxy=True),
        data / "bounded-state.json",
        profiles,
    )
    limits = {
        "schema_version": 1,
        "enforcement": "controlled-model-factory-v1",
        "scope": "resource-run-root",
        "profiles": {"AET-F05": {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}},
    }
    assert service.activate(guard, limits, reviewed={"sandbox_bounds": {"AET-F05": 10}, "f05_deadline": pins})
    h.app.state.evaluation_profiles, h.app.state.evaluation_bounded_sandboxes = profiles, service
    owned = {"runs": [], "tasks": [], "workflow_runs": [], "workflows": [UUID(workflow_id)]}

    async def scoped(function, *args):
        token = active_resources.set(ResourceRun(h.db, h.owner, "work", uuid4()))
        try:
            return await function(*args)
        finally:
            active_resources.reset(token)

    def call(function, *args):
        return h.http.portal.call(scoped, function, *args)

    def root(*, graph=None, owner=None):
        owner = owner or h.owner
        run, task, flow, lease = uuid4(), uuid4(), uuid4(), uuid4()
        saved = h.sql("SELECT definition FROM workflows WHERE id=%s", (workflow_id,))[0]["definition"]
        snapshot = {"definition": saved if graph is None else graph, "revision": 1, "model_id": model}
        h.execute(
            "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) VALUES(%s,%s,'RUNNING','seed',%s)",
            (flow, workflow_id, Jsonb(snapshot)),
        )
        h.execute(
            "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,'synthetic F05','no execution')", (task, owner)
        )
        h.execute(
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,model_revision,"
            "status,lease_token,lease_until) VALUES(%s,%s,%s,'synthetic F05','no execution',1,%s,1,"
            "'RUNNING',%s,now()+interval '1 hour')",
            (run, task, owner, model, lease),
        )
        h.execute("INSERT INTO work_run_workflows(run_id,workflow_run_id) VALUES(%s,%s)", (run, flow))
        for key, value in (("runs", run), ("tasks", task), ("workflow_runs", flow)):
            owned[key].append(value)
        return run, lease

    def release_local(session):
        h.execute(
            "UPDATE work_sandbox_sessions SET state='RELEASED' WHERE id=%s AND sandbox_id IS NULL AND state='CREATING'",
            (session["id"],),
        )

    value = SimpleNamespace(
        h=h,
        child=child,
        data=data,
        model=model,
        target=target,
        namespace=namespace,
        guard=guard,
        service=service,
        profiles=profiles,
        workflow=workflow_id,
        call=call,
        root=root,
        release_local=release_local,
    )
    yield value
    if request.session.testsfailed != failures_before:
        return  # Preserve this newly owned DB fixture and proof files; never clean a failing history.
    assert h.sql("SELECT count(*) AS n FROM model_usage")[0]["n"] == 0
    assert h.sql("SELECT count(*) AS n FROM operation_executions")[0]["n"] == 0
    assert not h.sql("SELECT id FROM work_sandbox_sessions WHERE sandbox_id IS NOT NULL")
    h.execute("DELETE FROM work_sandbox_sessions WHERE run_id=ANY(%s)", (owned["runs"],))
    h.execute("DELETE FROM work_queue_runs WHERE id=ANY(%s)", (owned["runs"],))
    h.execute("DELETE FROM work_tasks WHERE id=ANY(%s)", (owned["tasks"],))
    h.execute("DELETE FROM workflow_runs WHERE id=ANY(%s)", (owned["workflow_runs"],))
    h.execute("DELETE FROM workflows WHERE owner_id=ANY(%s)", ([h.owner, h.other],))
    h.execute("DELETE FROM agents WHERE owner_id=ANY(%s)", ([h.owner, h.other],))
    h.execute("DELETE FROM model_configs WHERE id=%s", (model,))


def test_real_agent_input_stable_name_owner_uniqueness_and_fixture_no_replay(f05):
    c = f05
    attempt = {"attempt_id": str(uuid4())}
    journal = Journal(c.data / "fixture-journal")
    journal.data["attempts"].append(attempt)
    platform = SimpleNamespace(
        http=c.h.http,
        attest=lambda: c.guard.check("submission"),
        effect_confirmation_guard=lambda: c.guard.check("submission"),
    )
    try:
        fixture = fixtures.FixedFixtures(platform, journal, attempt)
        agent = fixture.agent("AET-F05", c.model)
        assert fixture.agent("AET-F05", c.model) == agent
        row = c.h.sql("SELECT owner_id,name,model_id FROM agents WHERE id=%s", (agent,))[0]
        assert row == {"owner_id": c.h.owner, "name": f"AE AET-F05 {fixture.prefix} {c.model}", "model_id": c.model}
        assert c.h.sql("SELECT count(*) AS n FROM agents WHERE owner_id=%s", (c.h.owner,))[0]["n"] == 1
        body = AgentInput(name=row["name"], model_id=c.model).model_dump(mode="json")
        assert c.h.http.post("/api/v1/agents", json=body).status_code == 409
        assert c.h.http.post("/api/v1/agents", json={**body, "name": "x" * 101}).status_code == 422
        c.h.http.cookies.set(COOKIE, c.h.tokens[1])
        assert c.h.http.put(f"/api/v1/agents/{agent}", json={**body, "revision": 1}).status_code == 404
        assert c.h.http.post("/api/v1/agents", json=body).status_code == 201
    finally:
        journal.close()


def test_public_schema_save_publish_gate_and_version_projection_only(f05):
    c = f05
    expected = Definition.model_validate(code.definition()).model_dump()
    assert expected == code.definition()
    saved = c.h.http.get("/api/v1/workflows", params={"identifier": c.workflow})
    assert saved.status_code == 200, saved.text
    matches = [row for row in saved.json()["items"] if row["id"] == c.workflow]
    assert len(matches) == 1 and matches[0]["definition"] == expected
    invalid = deepcopy(expected)
    invalid["execution"] = "readonly"
    response = c.h.http.post(
        "/api/v1/workflows", json={"name": "invalid work nodes", "model_id": c.model, "definition": invalid}
    )
    assert response.status_code == 422
    assert c.h.http.post(f"/api/v1/workflows/{c.workflow}/publish", json={"revision": 1}).status_code == 409
    # Explicit synthetic state witness: no node was executed. Only the real publish gate/projection is tested.
    c.h.execute("UPDATE workflows SET tested_revision=revision WHERE id=%s AND owner_id=%s", (c.workflow, c.h.owner))
    published = c.h.http.post(f"/api/v1/workflows/{c.workflow}/publish", json={"revision": 1})
    assert published.status_code == 201 and published.json()["version"] == 1
    versions = c.h.http.get(f"/api/v1/workflows/{c.workflow}/versions").json()
    assert versions["current_published_version"] == 1 and len(versions["items"]) == 1
    assert versions["items"][0]["definition"] == expected and versions["items"][0]["draft_revision"] == 1
    c.h.http.cookies.set(COOKIE, c.h.tokens[1])
    assert c.h.http.get(f"/api/v1/workflows/{c.workflow}/versions").status_code == 404


def test_native_join_missing_association_and_expired_lease_refuse_without_sdk(f05):
    c = f05
    run, lease = c.root()
    session = c.call(c.service.reserve, run, lease, "coding")
    assert session["run_id"] == run and session["owner_id"] == c.h.owner and session["sandbox_id"] is None
    c.release_local(session)
    c.h.execute("DELETE FROM work_run_workflows WHERE run_id=%s", (run,))
    before = c.h.sql("SELECT count(*) AS n FROM work_sandbox_sessions")[0]["n"]
    with pytest.raises(ValueError, match="exact fixed graph"):
        c.call(c.service.reserve, run, lease, "coding")
    assert c.h.sql("SELECT count(*) AS n FROM work_sandbox_sessions")[0]["n"] == before
    c.h.execute("UPDATE work_queue_runs SET lease_until=now()-interval '1 second' WHERE id=%s", (run,))
    assert c.call(c.service.reserve, run, lease, "coding") is None
    assert c.service.attempts == 1
    events = c.h.sql("SELECT data FROM work_run_events WHERE run_id=%s AND event='sandbox.capacity_refused'", (run,))
    assert events[-1]["data"]["reason"] == "LEASE_PREDICATE_REJECTED" and str(lease) not in json.dumps(events)


def test_native_owner_and_exact_graph_do_not_downgrade_to_ordinary_profile(f05):
    c = f05
    run, lease = c.root(owner=c.h.other)
    with pytest.raises((ValueError, RuntimeError)):
        c.call(c.service.reserve, run, lease, "coding")
    graph = deepcopy(code.definition())
    graph["node_budget"] += 1
    other_run, other_lease = c.root(graph=graph)
    with pytest.raises(ValueError, match="exact fixed graph"):
        c.call(c.service.reserve, other_run, other_lease, "coding")
    assert not c.h.sql("SELECT id FROM work_sandbox_sessions") and c.service.attempts == 0


def test_real_three_root_bound_accepts_three_and_rejects_the_fourth(f05):
    c = f05
    roots = [c.root() for _ in range(3)]
    for run, lease in roots:
        session = c.call(c.service.reserve, run, lease, "coding")
        assert session["run_id"] == run and session["sandbox_id"] is None
        c.release_local(session)
    extra, lease = c.root()
    with pytest.raises(ValueError, match="three-root"):
        c.call(c.service.reserve, extra, lease, "coding")
    assert c.service.attempts == 3
    assert c.h.sql("SELECT count(*) AS n FROM work_sandbox_sessions")[0]["n"] == 3


def test_observer_real_session_owner_foreign_admin_and_live_permission(f05, monkeypatch):
    import evaluation_java_observer

    c = f05
    run, _lease = c.root()
    witnesses = []

    async def environment_witness(request):
        # Substitute only the pre-attested target proof; real identity/session and feature permissions remain in use.
        user = await identity(request)
        return {"environment_id": c.target.environment_id, "owner_id": user["id"]}

    async def inventory_witness(service, namespace):
        assert service is c.service and namespace == c.namespace
        witnesses.append(namespace)
        return {"complete": True, "count": 0}  # Known no SDK sent; not an actual manager inventory query.

    monkeypatch.setattr(code, "environment", environment_witness)
    monkeypatch.setattr(evaluation_java_observer, "inventory", inventory_witness)
    code.attach(c.h.app)
    path = f"/api/v1/agent-evaluations/profiles/code-workflows/{run}/observations"
    response = c.h.http.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == str(run) and body["owner_id"] == str(c.h.owner) and body["namespace"] == c.namespace
    assert body["counts"]["model_calls"] == 0 and body["provider"]["calls"] == 0
    assert body["sessions_complete"] and body["sessions"] == [] and body["sandbox"]["inventory"]["complete"]
    assert not any(
        secret in response.text for secret in ["lease_token", "synthetic-unused-key", "contract-vault", str(c.data)]
    )
    c.h.http.cookies.set(COOKIE, c.h.tokens[1])
    assert c.h.sql("SELECT is_admin FROM users WHERE id=%s", (c.h.other,))[0]["is_admin"]
    assert c.h.http.get(path).status_code == 404 and len(witnesses) == 1
    c.h.http.cookies.set(COOKIE, c.h.tokens[0])
    c.h.execute("UPDATE platform_roles SET permissions=permissions-%s WHERE id=%s", ("work.read", c.h.role))
    assert c.h.http.get(path).status_code == 403 and len(witnesses) == 1


def test_real_signed_owned_cancellation_leaves_zero_native_sessions_and_usage(f05):
    c = f05
    run, lease = c.root()
    original = (c.child / "case-deadline.json").read_bytes()
    result = c.child / "result"
    result.mkdir()
    (result / "cancel.request").write_bytes(b"owned synthetic cancellation")

    async def effects():
        for boundary in ("provider", "tool", "sandbox", "submission"):
            with pytest.raises(RuntimeError, match="not active"):
                c.guard.check(boundary)

    c.call(effects)
    with pytest.raises(RuntimeError, match="not active"):
        c.call(c.service.reserve, run, lease, "coding")
    assert not c.h.sql("SELECT id FROM work_sandbox_sessions") and c.service.attempts == 0
    assert not c.h.sql("SELECT call_id FROM model_usage") and not c.h.sql("SELECT id FROM operation_executions")
    assert (c.child / "case-deadline.json").read_bytes() == original
