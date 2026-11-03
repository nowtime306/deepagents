"""Capacity warnings use observed free space and do not invent throughput limits."""

from types import SimpleNamespace

from app.operations_health import host


def test_low_disk_observation_warns_without_claiming_a_concurrency_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.operations_health.shutil.disk_usage", lambda path: SimpleNamespace(total=100 * 1024**3, free=4 * 1024**3)
    )
    snapshot = host(tmp_path)
    assert snapshot["logical_cpus"] > 0
    assert any("磁盘空间不足" in message for message in snapshot["alerts"])
    assert "max_concurrency" not in snapshot
