"""Real isolated database observations, private rules and incident lifecycle."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.monitoring import tick, transition

PREFIX = "/api/v1/operations/monitoring"


def admin(connection, app):
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    return owner


def rule(**extra):
    return {"name": "本机告警-"+uuid4().hex, "scope": "system", "metric": "service_offline", "target": "work",
            "threshold": 1, "duration_seconds": 0, "cooldown_seconds": 300, **extra}


def test_monitor_scope_unknown_and_rule_validation(client):
    connection, app = client
    assert connection.get(PREFIX).status_code == 403
    assert connection.post(PREFIX+"/alerts/rules", json=rule(), headers=ORIGIN).status_code == 403
    own = connection.post(PREFIX+"/alerts/rules", headers=ORIGIN,
        json=rule(scope="personal", metric="failure_rate", target="", threshold="0.3"))
    assert own.status_code == 201
    assert [r["id"] for r in connection.get(PREFIX+"/alerts/rules").json()["items"]] == [own.json()["id"]]
    admin(connection, app)
    assert connection.get(PREFIX+"?hours=2").status_code == 422
    assert connection.post(PREFIX+"/alerts/rules", headers=ORIGIN, json=rule(target="unknown")).status_code == 422
    assert connection.post(PREFIX+"/alerts/rules", headers=ORIGIN, json=rule(threshold="NaN")).status_code == 422
    absent = connection.post(PREFIX+"/alerts/events/"+str(uuid4())+"/acknowledge", headers=ORIGIN)
    assert absent.status_code == 404


def test_independent_metrics_auth_and_missing_observations(client):
    connection, app = client
    admin(connection, app)
    token = app.state.settings.data_dir / "monitoring/metrics.token"
    token.parent.mkdir(exist_ok=True)
    token.unlink(missing_ok=True)
    endpoint = "/api/v1/internal/observability/metrics"
    assert connection.get(endpoint).status_code == 503
    token.write_text("x"*48)
    assert connection.get(endpoint).status_code == 403
    assert connection.get(endpoint, headers={"Authorization": "Bearer wrong"}).status_code == 403
    snapshot = {"services": [], "queues": [], "host": {"memory_total": None, "cpu_percent": None},
                "usage": [{"status": "KNOWN", "calls": 1, "input_tokens": "12", "output_tokens": "3"}],
                "costs": [{"currency": "USD", "estimated_cost": None, "unknown_calls": 1}], "environments": []}
    stamp = datetime.now(UTC)+timedelta(seconds=1)
    connection.portal.call(app.state.db.execute, "INSERT INTO monitor_samples VALUES(%s,%s)", (stamp, Jsonb(snapshot)))
    result = connection.get(endpoint, headers={"Authorization": "Bearer "+"x"*48})
    assert result.status_code == 200
    assert 'owner_agent_model_tokens_sum{direction="input"} 12.0' in result.text
    assert "host_cpu_percent" not in result.text and "cost_estimate_sum" not in result.text
    assert 'owner_agent_sample_available{signal="host"} 0.0' in result.text
    assert "owner_agent_cost_unknown_calls 1.0" in result.text
    assert "username" not in result.text and "owner_id" not in result.text
    assert connection.get(PREFIX).json()["snapshot"]["host"]["cpu_percent"] is None
    connection.portal.call(app.state.db.execute, "DELETE FROM monitor_samples WHERE sampled_at=%s", (stamp,))
    token.unlink()


async def step(db, identifier, value, now):
    async with db.pool.connection() as c:
        row = await (await c.execute("SELECT * FROM monitor_rules WHERE id=%s FOR UPDATE", (identifier,))).fetchone()
        await transition(c, row, value, now)


def test_durable_duration_acknowledgement_recovery_and_cooldown(client):
    connection, app = client
    owner = admin(connection, app)
    created = connection.post(PREFIX+"/alerts/rules", json=rule(duration_seconds=60), headers=ORIGIN).json()
    identifier, now = created["id"], datetime.now(UTC)
    connection.portal.call(step, app.state.db, identifier, 1, now)
    assert connection.get(PREFIX+"/alerts/events").json()["items"] == []
    connection.portal.call(step, app.state.db, identifier, 1, now+timedelta(seconds=61))
    fired = connection.get(PREFIX+"/alerts/events").json()["items"][0]
    assert fired["status"] == "FIRING" and fired["rule_revision"] == 1
    ack = connection.post(PREFIX+f"/alerts/events/{fired['id']}/acknowledge", headers=ORIGIN).json()
    assert ack["status"] == "FIRING" and ack["acknowledged_at"]
    connection.portal.call(step, app.state.db, identifier, 1, now+timedelta(seconds=65))
    assert len(connection.get(PREFIX+"/alerts/events").json()["items"]) == 1
    connection.portal.call(step, app.state.db, identifier, None, now+timedelta(seconds=70))
    assert connection.get(PREFIX+"/alerts/events").json()["items"][0]["status"] == "FIRING"
    connection.portal.call(step, app.state.db, identifier, 0, now+timedelta(seconds=80))
    assert connection.get(PREFIX+"/alerts/events").json()["items"][0]["status"] == "RECOVERED"
    connection.portal.call(step, app.state.db, identifier, 1, now+timedelta(seconds=90))
    connection.portal.call(step, app.state.db, identifier, Decimal(1), now+timedelta(seconds=151))
    assert len(connection.get(PREFIX+"/alerts/events").json()["items"]) == 2
    notices = connection.portal.call(app.state.db.fetch,
        "SELECT id FROM notifications WHERE owner_id=%s AND target_kind='monitoring'", (owner,))
    assert len(notices) == 1  # Second incident is persisted, but external/local notification remains in cooldown.


def test_global_visibility_requires_explicit_permission_and_cursor_scope(client):
    connection, app = client
    owner = admin(connection, app)
    created = connection.post(PREFIX+"/alerts/rules", json=rule(), headers=ORIGIN).json()
    connection.portal.call(step, app.state.db, created["id"], 1, datetime.now(UTC))
    foreign = uuid4()
    connection.portal.call(app.state.db.execute,
        "INSERT INTO users(id,username,password_hash,role_id) VALUES(%s,%s,'none','member')", (foreign, str(foreign)))
    connection.portal.call(app.state.db.execute, "UPDATE monitor_rules SET owner_id=%s WHERE id=%s",
                           (foreign, created["id"]))
    connection.portal.call(app.state.db.execute, "UPDATE monitor_alerts SET owner_id=%s WHERE rule_id=%s",
                           (foreign, created["id"]))
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=false WHERE id=%s", (owner,))
    assert connection.get(PREFIX+"/alerts/rules").json()["items"] == []
    assert connection.get(PREFIX+"/alerts/events").json()["items"] == []
    assert connection.get(PREFIX+"/alerts/events?before=bad").status_code == 422


def test_real_sampler_persists_retains_and_does_not_depend_on_dashboard(client):
    connection, app = client
    admin(connection, app)
    old = datetime.now(UTC)-timedelta(days=8)
    connection.portal.call(app.state.db.execute, "DELETE FROM monitor_samples")
    connection.portal.call(app.state.db.execute, "INSERT INTO monitor_samples VALUES(%s,'{}')", (old,))
    connection.portal.call(tick, app.state.db, app.state.settings)
    response = connection.get(PREFIX).json()
    assert not response["stale"] and response["snapshot"]["host"]["disk_total"] > 0
    assert response["runs"]["duration_samples"] >= 0
    samples = connection.portal.call(app.state.db.fetch, "SELECT * FROM monitor_samples")
    assert len(samples) == 1 and samples[0]["sampled_at"] > old
    connection.portal.call(tick, app.state.db, app.state.settings)
    assert len(connection.portal.call(app.state.db.fetch, "SELECT * FROM monitor_samples")) == 1
    assert connection.get(PREFIX+"/series?hours=168").json()["items"]


def test_revoked_system_manager_cannot_downgrade_or_acknowledge(client):
    c, app = client
    owner = admin(c, app)
    created = c.post(PREFIX+"/alerts/rules", json=rule(), headers=ORIGIN).json()
    c.portal.call(step, app.state.db, created["id"], 1, datetime.now(UTC))
    c.portal.call(app.state.db.execute, "UPDATE users SET is_admin=false WHERE id=%s", (owner,))
    changed = rule(scope="personal", metric="failure_rate", target="", threshold=.3,
                   revision=created["revision"], enabled=False)
    assert c.put(PREFIX+f"/alerts/rules/{created['id']}", json=changed, headers=ORIGIN).status_code == 403
    event = c.get(PREFIX+"/alerts/events").json()["items"][0]
    assert event["can_ack"] is False
    assert c.post(PREFIX+f"/alerts/events/{event['id']}/acknowledge", headers=ORIGIN).status_code == 404


def test_rule_limit_serializes_concurrent_creates_and_cursor_is_bounded(client):
    c, app = client
    for _ in range(49):
        assert c.post(PREFIX+"/alerts/rules", headers=ORIGIN,
                      json=rule(scope="personal", metric="failure_rate", target="")).status_code == 201
    def create(_):
        return c.post(PREFIX+"/alerts/rules", headers=ORIGIN,
                      json=rule(scope="personal", metric="failure_rate", target="")).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [201, 409]
    listed = c.get(PREFIX+"/alerts/rules").json()
    assert len(listed["items"]) == 50 and listed["next_cursor"] is None
    assert c.get(PREFIX+"/alerts/rules?before=bad").status_code == 422
