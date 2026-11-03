"""A concurrent edit cannot combine one model revision with another revision's capabilities."""

from uuid import UUID, uuid4

from test_api import client as client

from app.models import ModelInput, ModelRegistry
from app.resource_runtime import resource_run


def test_concurrent_model_and_tool_declaration_update_returns_one_committed_configuration(client):
    connection, app = client
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])

    async def scenario():
        identifier = await app.state.models.save(ModelInput(
            name="atomic-" + uuid4().hex, model="offline", base_url="http://controlled.invalid", supports_tools=True,
        ))

        class ConcurrentEdit:
            updated = False

            async def fetch(self, query, params=()):
                rows = await app.state.db.fetch(query, params)
                if "FROM model_configs" in query and not self.updated:
                    self.updated = True
                    # Commit a whole edit immediately after the reader's first SQL snapshot.
                    # With separate reads the old implementation returned revision 1 + supports_tools=False.
                    async with app.state.db.pool.connection() as writer:
                        await writer.execute("UPDATE model_configs SET temperature=0.8,revision=revision+1 WHERE id=%s",
                                             (identifier,))
                        await writer.execute(
                            "UPDATE model_capability_declarations SET supports_tools=false WHERE model_id=%s",
                            (identifier,),
                        )
                return rows

        run = uuid4()
        async with resource_run(app.state.db, owner, "resource", run):
            loaded = await ModelRegistry(ConcurrentEdit(), app.state.models.vault).get(identifier)
        latest = await app.state.models.get(identifier)
        assert loaded.revision == 1 and loaded.temperature is None and loaded.supports_tools is True
        assert latest.revision == 2 and latest.temperature == 0.8 and latest.supports_tools is False
        recorded = (await app.state.db.fetch(
            "SELECT revision,snapshot FROM run_resource_snapshots WHERE run_id=%s", (run,),
        ))[0]
        assert recorded["revision"] == "1" and recorded["snapshot"]["supports_tools"] is True

    connection.portal.call(scenario)
