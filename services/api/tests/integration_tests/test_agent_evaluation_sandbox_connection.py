"""Dedicated evaluation connections never fall back to a personal/global manager key."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from opensandbox.config import ConnectionConfig
from test_api import DATABASE, ORIGIN, TEST_DATA, add_user, migrate, run_async, seed_model

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("invalid", ["missing_instance", "database", "directory"])
def test_explicit_connection_rejects_nonisolated_settings_before_database_io(tmp_path, monkeypatch, invalid):
    def forbidden_io(*args, **kwargs):
        raise AssertionError("Isolation rejection must happen before Database construction or I/O")

    monkeypatch.setattr("app.main.Database", forbidden_io)
    settings = Settings(database_url="postgresql://unused:unused@127.0.0.1:1/evaluation_test",
                        data_dir=tmp_path / "evaluation_test", evaluation_instance_id=str(uuid4()))
    if invalid == "missing_instance":
        settings.evaluation_instance_id = None
    elif invalid == "database":
        settings.database_url = "postgresql://unused:unused@127.0.0.1:1/not_a_test_database"
    else:
        settings.data_dir = tmp_path / "not_an_isolated_directory"
    explicit = ConnectionConfig(domain="127.0.0.1:18999", api_key="private-test-key", use_server_proxy=True)
    with pytest.raises(ValueError, match="isolated test instance"):
        create_app(settings, evaluation_sandbox_connection=explicit)


@pytest.mark.parametrize("mode", ["explicit_evaluation", "evaluation_without_connection", "legacy_default"])
def test_connection_selection_and_default_http_compatibility(tmp_path, monkeypatch, mode):
    if not DATABASE or not DATABASE.rsplit("/", 1)[-1].endswith("_test"):
        pytest.skip("Requires an isolated _test database")
    run_async(migrate(DATABASE))
    run_async(seed_model(DATABASE))
    username = "evaluation-connection-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    directory = tmp_path / "connection_test"
    directory.mkdir()
    # This key belongs only to the current isolated test DB, never a production or manager credential.
    (directory / "model.key").write_bytes((TEST_DATA / "model.key").read_bytes())
    is_evaluation = mode != "legacy_default"
    settings = Settings(database_url=DATABASE, data_dir=directory, work_enabled=True,
                        evaluation_instance_id=str(uuid4()) if is_evaluation else None)
    selected, global_key_access = [], []

    class ObservedWorkbench:
        def __init__(self, database, vault, config, artifacts):
            selected.append(config)

        async def serve(self):
            await asyncio.Event().wait()

        async def close(self):
            pass

    original_is_file, original_read_text = Path.is_file, Path.read_text

    def is_global_key(path):
        return path.as_posix().endswith("/sandbox/.local/manager.key")

    def is_file(path):
        if is_global_key(path):
            global_key_access.append("is_file")
            if is_evaluation:
                raise AssertionError("Evaluation startup must not probe the global manager credential")
            return True
        return original_is_file(path)

    def read_text(path, *args, **kwargs):
        if is_global_key(path):
            global_key_access.append("read_text")
            if is_evaluation:
                raise AssertionError("Evaluation startup must not read the global manager credential")
            return "controlled-legacy-key"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr("app.main.BrowserWorkbench", ObservedWorkbench)
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "read_text", read_text)
    explicit = ConnectionConfig(domain="127.0.0.1:18999", api_key="private-explicit-key", use_server_proxy=True)
    application = create_app(settings,
                             evaluation_sandbox_connection=explicit if mode == "explicit_evaluation" else None)
    with TestClient(application, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as connection:
        response = connection.post("/api/v1/auth/login", headers=ORIGIN,
            json={"username": username, "password": "integration-password"})
        assert response.status_code == 200
        assert connection.get("/api/v1/auth/me").json()["username"] == username
        if mode == "explicit_evaluation":
            assert selected == [explicit] and global_key_access == []
        elif mode == "evaluation_without_connection":
            assert selected == [] and application.state.browser_workbench is None and global_key_access == []
        else:
            assert len(selected) == 1 and selected[0].domain == "127.0.0.1:18180"
            assert selected[0].api_key == "controlled-legacy-key"
            assert global_key_access == ["is_file", "read_text"]
