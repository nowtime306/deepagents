"""A05 v2 fixed formal-HTTP handler. No automatic CLI dispatch or readiness registration."""

import time
from datetime import UTC, datetime, timedelta

from evaluation_runner import adapters
from evaluation_runner.fixed_evidence import binding, load, store
from evaluation_runner.fixed_fixtures import FixedFixtures
from evaluation_runner.journal import UnknownSubmission
from evaluation_runner.profiles_v2.a05.protocol import (
    BASE,
    CASE,
    LIMITS,
    PROMPTS,
    ROLES,
    SCENES,
    definition_digest,
    identifier,
    validate_scene,
)


def check_specimen(specimen):
    if (
        specimen.case_id != CASE
        or specimen.mode != "ask"
        or specimen.fixture_profile.version != 2
        or specimen.fixture_profile.id != "revoked-tool-injection"
        or specimen.input != PROMPTS["injection"]
        or specimen.metrics
        or specimen.repeats != 1
        or specimen.approval_policy != "none"
        or specimen.budgets.model_dump() != {"max_model_calls": 5, "max_tokens": 75, "timeout_seconds": 120}
        or specimen.fixture_parameters != {"definition_digest": definition_digest()}
        or [row.model_dump() for row in specimen.validators]
        != [{"registered_id": "forbidden_effect_count", "version": 1, "parameters": {}, "required": True}]
    ):
        raise ValueError("Only the separately versioned original A05 v2 definition is supported")


def admission(platform):
    proof = platform.attest()
    platform.expected_sandbox_namespace = identifier(proof["sandbox_namespace"])
    platform.attest()
    user = platform.get("/api/v1/auth/me")
    capability = platform.get(BASE + "/capabilities")
    required = {
        "evaluation.run",
        "evaluation.read",
        "evaluation.manage",
        "agents.manage",
        "tools.use",
        "chat.run",
        "chat.read",
        "chat.manage",
    }
    enforcement = proof.get("execution_limits", {})
    root_limit = enforcement.get("profiles", {}).get(CASE)
    if (
        user.get("is_admin") is not True
        or enforcement.get("schema_version") != 1
        or enforcement.get("enforcement") != "controlled-model-factory-v1"
        or enforcement.get("scope") != "resource-run-root"
        or not required <= set(proof["permissions"])
        or capability
        != {
            "case_id": CASE,
            "definition_version": 2,
            "definition_digest": definition_digest(),
            "limits": LIMITS,
            "per_root": {"max_model_calls": 3, "max_tokens": 45},
            "timeout_seconds": 120,
            "healthy": True,
        }
        or root_limit != {"mode": "ask", "max_model_calls": 3, "max_tokens": 45}
    ):
        raise adapters.Blocked("Exact owned A05 v2 provider, receiver and budgets are required")
    return proof


def initialize(platform, journal, attempt, specimen):
    check_specimen(specimen)
    proof = admission(platform)
    if attempt.get("a05_definition_digest", definition_digest()) != definition_digest():
        raise ValueError("Do not replace an earlier fixed definition")
    attempt["a05_definition_digest"] = definition_digest()
    attempt["sandbox_namespace"] = identifier(proof["sandbox_namespace"])
    if "a05_deadline" not in attempt:
        attempt["a05_deadline"] = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
    if datetime.now(UTC) >= datetime.fromisoformat(attempt["a05_deadline"]):
        raise adapters.Blocked("The original initialization deadline elapsed; do not restart it")
    journal.save()
    fixtures = FixedFixtures(platform, journal, attempt)
    scenes = {}
    for scene in SCENES:
        role = ROLES[scene]
        model = fixtures.create(
            "a05-model-" + scene,
            "/api/v1/admin/models",
            {
                "name": "AE A05 v2 " + scene + " " + fixtures.prefix,
                "model": "eval::AET-A05::v2",
                "base_url": platform.target.api_base_url + "/controlled-model",
                "kind": "chat",
                "provider": "custom",
                "protocol": "openai",
                "enabled": True,
                "supports_tools": True,
                "max_output_tokens": 4096,
            },
        )
        fixtures.mutate(
            "a05-seal-" + scene,
            "POST",
            "/api/v1/agent-evaluations/profiles/model-bindings",
            body={"model_id": model, "case_id": CASE, "role": role},
        )
        receiver = fixtures.mutate(
            "a05-receiver-" + scene, "POST", BASE + "/receivers/" + scene, body={"model_id": model}
        )
        server = identifier(receiver["id"])
        resource = {"kind": "mcp-server", "id": server, "path": "/api/v1/capabilities/mcp/" + server}
        if resource not in attempt.setdefault("created_resources", []):
            attempt["created_resources"].append(resource)
            journal.save()
        agent = fixtures.published_agent(CASE, model, mcp=[server])
        conversation = fixtures.create(
            "a05-conversation-" + scene,
            "/api/v1/conversations",
            {
                "title": "AE A05 v2 " + scene + " " + fixtures.prefix,
                **agent,
            },
        )
        scenes[scene] = {
            "model_id": identifier(model),
            "server_id": identifier(server),
            "conversation_id": identifier(conversation),
            **agent,
        }
    attempt["fixture"] = {"scenes": scenes}
    journal.save()
    return attempt["fixture"]


def expected(platform, attempt, scene):
    row = attempt["fixture"]["scenes"][scene]
    return {
        "scene": scene,
        "environment_id": platform.target.environment_id,
        "owner_id": platform.target.owner_id,
        "namespace": attempt["sandbox_namespace"],
        "root_id": row["root_id"],
        "server_id": row["server_id"],
        "model_id": row["model_id"],
        "conversation_id": row["conversation_id"],
    }


def retained(journal, attempt, scene, value):
    body = {
        "schema_version": 1,
        "kind": "a05-v2-scene",
        "binding": binding(journal.data, attempt),
        "definition_digest": definition_digest(),
        "scene": scene,
        "body": value,
    }
    reference = store(journal.directory, "a05-v2-scene", body)
    attempt.setdefault("a05_scene_refs", {})[scene] = reference
    attempt.setdefault("evidence_refs", []).append(reference)
    journal.save()


def verified(platform, journal, attempt, scene):
    reference = attempt["a05_scene_refs"][scene]
    if attempt["evidence_refs"].count(reference) != 1:
        raise ValueError("Original unique scene evidence is required")
    value = load(journal.directory, reference, "a05-v2-scene")
    if (
        value["binding"] != binding(journal.data, attempt)
        or value["definition_digest"] != definition_digest()
        or value["scene"] != scene
    ):
        raise ValueError("Foreign retained A05 evidence")
    validate_scene(value["body"], expected(platform, attempt, scene))
    return value["body"]


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    check_specimen(specimen)
    admission(platform)
    if not attempt.get("fixture") or attempt.get("a05_definition_digest") != definition_digest():
        raise adapters.Blocked("Original confirmed v2 initialization required")
    intents = [*attempt.get("fixture_intents", {}).values(), *attempt.get("submission_intents", {}).values()]
    if any(row["status"] != "CONFIRMED" for row in intents):
        raise UnknownSubmission("An earlier fixture/submission outcome is unknown; do not dispatch another root")
    platform.expected_sandbox_namespace = attempt["sandbox_namespace"]
    if "a05_deadline" not in attempt:
        attempt["a05_deadline"] = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
        journal.save()
    deadline = datetime.fromisoformat(attempt["a05_deadline"])
    for scene in SCENES:
        if scene in attempt.get("a05_scene_refs", {}):
            verified(platform, journal, attempt, scene)
            continue
        if cancelled() or datetime.now(UTC) >= deadline:
            raise adapters.Blocked("Cancelled/deadline; no subsequent root dispatched")
        row = attempt["fixture"]["scenes"][scene]
        if "root_id" not in row:
            response = journal.post(
                platform,
                attempt,
                f"/api/v1/conversations/{row['conversation_id']}/runs",
                {"message": PROMPTS[scene], "model_id": row["model_id"]},
                "a05-submit-" + scene,
            )
            row["root_id"] = identifier(response["run_id"])
            if len(
                {value.get("root_id") for value in attempt["fixture"]["scenes"].values() if value.get("root_id")}
            ) != sum("root_id" in value for value in attempt["fixture"]["scenes"].values()):
                raise ValueError("Two scenes must have distinct genuine roots")
            journal.save()
        while True:
            platform.attest()
            native = platform.get("/api/v1/runs/" + row["root_id"])
            if native["status"] in adapters.TERMINAL:
                break
            if cancelled() or datetime.now(UTC) >= deadline:
                journal.post(platform, attempt, "/api/v1/runs/" + row["root_id"] + "/cancel", {}, "a05-cancel-" + scene)
                raise adapters.Blocked("Stop requested; terminal cleanup has not been confirmed")
            time.sleep(0.1)
        value = platform.get(BASE + "/receivers/" + scene + "/observations")
        validate_scene(value, expected(platform, attempt, scene))
        retained(journal, attempt, scene, value)  # Known first-root facts persist before second dispatch.
    bodies = [verified(platform, journal, attempt, scene) for scene in SCENES]
    attempt["usage"] = {key: sum(value["usage"][key] for value in bodies) for key in bodies[0]["usage"]}
    attempt["usage_completeness"] = "KNOWN"
    attempt["task_outcome"] = "PASS"
    attempt["a05_roots"] = [value["run"] for value in bodies]  # Preserve expected FAILED, never relabel it successful.
    journal.save()
    return {"task_outcome": "PASS", "roots": attempt["a05_roots"], "usage": attempt["usage"]}


def cleanup(platform, journal, attempt, specimen):
    check_specimen(specimen)
    if set(attempt.get("a05_scene_refs", {})) != set(SCENES):
        return "NEEDS_REVIEW"
    intents = [*attempt.get("fixture_intents", {}).values(), *attempt.get("submission_intents", {}).values()]
    if any(row["status"] != "CONFIRMED" for row in intents):
        return "NEEDS_REVIEW"
    for scene in SCENES:
        before = verified(platform, journal, attempt, scene)
        platform.attest()
        now = platform.get(BASE + "/receivers/" + scene + "/observations")
        validate_scene(now, expected(platform, attempt, scene))
        if {key: value for key, value in before.items() if key != "closed"} != {
            key: value for key, value in now.items() if key != "closed"
        }:
            raise ValueError("Root or effects changed after original evidence retention")
        journal.post(platform, attempt, BASE + "/receivers/" + scene + "/close", {}, "a05-close-" + scene)
        after = platform.get(BASE + "/receivers/" + scene + "/observations")
        validate_scene(after, expected(platform, attempt, scene))
        if after.get("closed") is not True or {key: value for key, value in after.items() if key != "closed"} != {
            key: value for key, value in before.items() if key != "closed"
        }:
            raise ValueError("Receiver shutdown or preserved effects were not confirmed")
    attempt["retained_resources"] = [
        {**row, "reason": "A05 v2 evidence retained; closed receiver token cleared"}
        for row in attempt.get("created_resources", [])
    ]
    journal.save()
    return "CLEAN"
