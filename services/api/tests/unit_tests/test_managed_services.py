"""A backup restart cannot override a newer manual stop of the API."""

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.managed_services import ManagedServices, ServiceControlProfileError


def local_controller(tmp_path, monkeypatch):
    database = "postgresql://unit:unit@127.0.0.1:55432/fixed_profile_test"
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    environment = tmp_path / "services/api/.env"
    environment.parent.mkdir(parents=True, exist_ok=True)
    environment.write_text(f"DATABASE_URL={database}\nPLATFORM_DATA_DIR={tmp_path}\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PLATFORM_INTERNAL_API_URL", "http://127.0.0.1:8100")
    controller = ManagedServices(Settings(database_url=database, data_dir=tmp_path), None)
    controller.scripts = scripts
    return controller


async def test_manual_stop_marker_prevents_restart_of_api_and_dependent_workers(tmp_path, monkeypatch):
    directory = tmp_path / "services"
    directory.mkdir()
    (directory / "api.stop").write_text("manual-stop-newer-than-backup")
    controller = local_controller(tmp_path, monkeypatch)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Manual stop must not launch any process")

    controller.command = forbidden
    result = await controller.start(
        [
            {"kind": "api", "slot": 0, "stop_marker": "backup:old"},
            {"kind": "work", "slot": 0, "stop_marker": "backup:old"},
        ]
    )
    assert result is False
    assert (directory / "api.stop").read_text() == "manual-stop-newer-than-backup"


def test_actual_windows_start_script_preserves_a_newer_manual_stop_without_launching(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Native Windows service control acceptance")
    directory = tmp_path / "services"
    directory.mkdir()
    marker = directory / "api.stop"
    marker.write_text("new-manual-stop")
    root = Path(__file__).parents[4]
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(root / "scripts/start-managed-service.ps1"),
            "-Service",
            "api",
            "-ExpectedStopMarker",
            "backup:" + str(uuid4()),
        ],
        env={**os.environ, "PLATFORM_DATA_DIR": str(tmp_path)},
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    assert result.returncode == 0 and marker.read_text() == "new-manual-stop"
    assert not list(directory.glob("api-*.json"))


async def test_native_controller_returns_while_its_intended_child_keeps_running(tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Native Windows handle inheritance acceptance")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    child = scripts / "child.py"
    child.write_text("import time\ntime.sleep(10)\n")
    record = tmp_path / "child.pid"
    executable = sys.executable.replace("'", "''")
    argument = ('"' + str(child) + '"').replace("'", "''")
    destination = str(record).replace("'", "''")
    (scripts / "spawn.ps1").write_text(
        f"$child=Start-Process -FilePath '{executable}' -ArgumentList '{argument}' -WindowStyle Hidden -PassThru\n"
        f"$child.Id|Set-Content -LiteralPath '{destination}'\nWrite-Output 'Controller completed'\n",
        encoding="utf-8-sig",
    )
    controller = local_controller(tmp_path, monkeypatch)
    controller.scripts = scripts
    began = time.monotonic()
    try:
        await controller.command("spawn.ps1", [])
        assert time.monotonic() - began < 5
        assert record.exists()
    finally:
        if record.exists():
            identifier = int(record.read_text().strip())
            await __import__("asyncio").to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(identifier), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )


@pytest.mark.parametrize("method", ["snapshot", "command", "control", "stop", "start"])
async def test_independent_profile_rejects_every_control_entry_before_side_effects(tmp_path, monkeypatch, method):
    controller = local_controller(tmp_path, monkeypatch)
    controller.settings = Settings(database_url="postgresql://unit:unit@127.0.0.1:55432/isolated_test",
                                   data_dir=tmp_path / "isolated", internal_api_url="http://127.0.0.1:8139")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    before = set(tmp_path.rglob("*"))
    arguments = {"snapshot": (), "command": ("stop-platform-services.ps1", []),
                 "control": ("stop-platform-services.ps1", [], None), "stop": ([],), "start": ([],)}
    with pytest.raises(ServiceControlProfileError, match="^SERVICE_CONTROL_PROFILE_MISMATCH$"):
        await getattr(controller, method)(*arguments[method])
    assert not calls and set(tmp_path.rglob("*")) == before
    assert not (tmp_path / "isolated").exists()


def test_inherited_profile_mismatch_is_rejected_even_if_explicit_settings_match(tmp_path, monkeypatch):
    controller = local_controller(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unit:unit@127.0.0.1:55432/inherited_test")
    with pytest.raises(ServiceControlProfileError, match="^SERVICE_CONTROL_PROFILE_MISMATCH$"):
        controller.require_profile()


@pytest.mark.parametrize("field,value", [("database_url", "postgresql://unit:unit@127.0.0.1:55432/other_test"),
                                        ("data_dir", "other-data"),
                                        ("internal_api_url", "http://127.0.0.1:8139")])
def test_each_independent_identity_field_is_rejected(tmp_path, monkeypatch, field, value):
    controller = local_controller(tmp_path, monkeypatch)
    setattr(controller.settings, field, tmp_path / value if field == "data_dir" else value)
    with pytest.raises(ServiceControlProfileError, match="^SERVICE_CONTROL_PROFILE_MISMATCH$"):
        controller.require_profile()


async def test_ready_uses_configured_endpoint_without_proxy_or_redirect(tmp_path, monkeypatch):
    import httpx

    controller = ManagedServices(SimpleNamespace(internal_api_url="http://127.0.0.1:8139"), None)
    actual = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:8100/decoy"}) if len(requests) == 1 else (
            httpx.Response(200))

    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return actual(transport=httpx.MockTransport(respond), **kwargs)

    async def immediate(_):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr("app.managed_services.asyncio.sleep", immediate)
    await controller.ready()
    assert requests == ["http://127.0.0.1:8139/health/ready"] * 2
