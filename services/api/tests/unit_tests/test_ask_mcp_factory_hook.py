"""Instance-local MCP transport injection preserves ordinary Ask defaults."""
from types import SimpleNamespace

from app.ask_worker import AskWorker


def test_factory_is_local_to_the_selected_worker():
    database = SimpleNamespace()
    models = SimpleNamespace(vault=object())
    settings = SimpleNamespace(restricted_execution=False)
    def factory(headers):
        return None
    normal = AskWorker(database, models, settings)
    selected = AskWorker(database, models, settings, mcp_client_factory=factory)
    assert normal.mcp_client_factory is None
    assert normal.create_mcp().http_client_factory is not factory
    assert selected.create_mcp().http_client_factory is factory
