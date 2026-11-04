"""Real permit HTTP + PG revocation; synthetic queue, no target, publication or business tool."""

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_agent_evaluation_worker_pg import actor, claim, no_business_effects, seed, state

from app.agent_evaluation_platform_worker import PermitServer

pytest_plugins = ["test_agent_evaluation_platform"]
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())


@pytest.mark.parametrize("revocation", ["cancel", "permission", "expiry"])
def test_real_permission_client_checks_pg_again_after_revocation(h, monkeypatch, revocation):
    """A previous real HTTP grant cannot authorize the next effect after SQL revocation."""
    monkeypatch.syspath_prepend(str(ROOT / "services/evaluation/profile_support"))
    effects = importlib.import_module("evaluation_platform_effects")
    assert (
        Path(effects.__file__).resolve()
        == (ROOT / "services/evaluation/profile_support/evaluation_platform_effects.py").resolve()
    )
    seed(h)  # Confirms the actual public application is still BLOCKED before synthetic row insertion.
    worker = actor(h)
    row = claim(h, worker)
    worker.admission = SimpleNamespace(
        inspect=lambda _: {"status": "READY"}
    )  # Local object only; no bridge/publication.
    environment = str(uuid4())
    allowed_effects = []

    async def exercise():
        async with PermitServer(lambda payload: worker.permitted(row, payload)) as server:
            monkeypatch.setenv(effects.ORIGIN, server.origin)
            monkeypatch.setenv(effects.SECRET, server.secret)

            async def guarded_probe():
                await asyncio.to_thread(effects.require_permit, environment, "AET-A02", "ask", "tool", required=True)
                allowed_effects.append("synthetic-local-probe")

            await guarded_probe()
            assert allowed_effects == ["synthetic-local-probe"] and server.requests == 1
            if revocation == "cancel":
                await h.db.execute(
                    "UPDATE agent_evaluation_runs SET status='CANCEL_REQUESTED',cancel_requested=true "
                    "WHERE id=%s AND owner_id=%s",
                    (row["id"], h.owner),
                )
            elif revocation == "permission":
                await h.db.execute(
                    "UPDATE platform_roles SET permissions=%s WHERE id=%s", (Jsonb(["evaluation.read"]), h.role)
                )
            else:
                await h.db.execute(
                    "UPDATE agent_evaluation_runs SET lease_until=now()-interval '1 second' "
                    "WHERE id=%s AND owner_id=%s",
                    (row["id"], h.owner),
                )
            with pytest.raises(RuntimeError, match="permission|lease"):
                await guarded_probe()
            assert allowed_effects == ["synthetic-local-probe"] and server.requests == 1
        assert server.server is not None and not server.server.is_serving()

    h.http.portal.call(exercise)
    result = state(h, row)
    assert result["samples"][0]["status"] == "PENDING" and result["reports"] == [] and result["events"] == []
    no_business_effects(h)
