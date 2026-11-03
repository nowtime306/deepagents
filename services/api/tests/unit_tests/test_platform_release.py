"""Release evidence must describe the committed and fully verified platform."""
import importlib.util
import json
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("platform_release", Path(__file__).resolve().parents[4] / "scripts/platform_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def repository(tmp_path):
    def command(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True, stderr=subprocess.STDOUT).strip()
    command("init", "-b", "onwer_agent")
    command("config", "user.name", "Release Test")
    command("config", "user.email", "release-test@example.invalid")
    command("remote", "add", "origin", "https://operator:fake-private-value@example.invalid/project.git")
    (tmp_path / "apps/web/src").mkdir(parents=True)
    (tmp_path / "apps/web/src/test.ts").write_text("export const version = 1;", encoding="utf-8")
    (tmp_path / "platform-release.json").write_text('{"version":"0.2.0"}', encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n.local/\napps/web/.next/\n", encoding="utf-8")
    (tmp_path / ".env").write_text("PRIVATE=not-in-the-source-package", encoding="utf-8")
    command("add", "."); command("commit", "-m", "test source")
    command("tag", "zhixing-v0.2.0")
    return tmp_path, command


def prepared(repository):
    root, _ = repository
    record = release.prepare(root, "0.2.0", "HEAD", [])
    return root, record


def completion_inputs(root, record):
    directory = root / ".local/releases/0.2.0"
    qa = directory / "qa.json"
    qa.write_text(json.dumps({"status": "passed", "browser": "passed", "source_sha256": record["source_sha256"]}))
    deployment = directory / "deployment.json"
    deployment.write_text(json.dumps({"status": "deployed", "phase": "complete", "scope": "full", "web": "rebuilt",
                                      "time": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(), "backup": "private-backup-reference"}))
    health = directory / "health.json"
    health.write_text(json.dumps({"api": "ready", "web": "production-ready", "migrations": "current"}))
    build = root / "apps/web/.next/BUILD_ID"
    build.parent.mkdir(); build.write_text("unit-fixture-build")
    return qa, deployment, health


def test_package_is_committed_source_and_excludes_runtime_secrets(repository):
    root, _ = repository
    (root / "apps/web/src/test.ts").write_text("unverified dirty content")
    record = release.prepare(root, "0.2.0", "HEAD", [])
    assert record["status"] == "prepared" and "operator" not in record["remote"] and "fake-private" not in record["remote"]
    with zipfile.ZipFile(root / ".local/releases/0.2.0/source.zip") as archive:
        assert archive.read("apps/web/src/test.ts") == b"export const version = 1;"
        assert ".env" not in archive.namelist()
    assert release.verify(root, "0.2.0")["commit"] == record["commit"]


@pytest.mark.parametrize("filename", ["services/api/.ENV", "infra/server.PEM", "../escape.ts", "C:/escape.ts", "apps\\secret.ts"])
def test_private_or_escaping_archive_paths_are_rejected(tmp_path, filename):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        entry = zipfile.ZipInfo("fixture")
        entry.filename = filename
        zipped.writestr(entry, "fixture")
    with pytest.raises(ValueError):
        release.source_hashes(archive)


def test_duplicate_zip_paths_are_rejected_without_last_value_wins(tmp_path):
    archive = tmp_path / "duplicates.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("apps/web/src/A.ts", "first")
        zipped.writestr("apps/web/src/a.ts", "second")
    with pytest.raises(ValueError, match="重复"):
        release.source_hashes(archive)


def test_rejected_prepare_leaves_no_unsafe_package(repository):
    root, command = repository
    (root / "services/api").mkdir(parents=True)
    (root / "services/api/.ENV").write_text("unsafe fixture")
    command("add", "--force", "services/api/.ENV"); command("commit", "-m", "unsafe test fixture")
    with pytest.raises(ValueError):
        release.prepare(root, "0.2.0", "HEAD", [])
    assert not (root / ".local/releases/0.2.0").exists()
    assert not list((root / ".local/releases").glob(".preparing-*"))


@pytest.mark.parametrize("problem", ["qa-pending", "partial-qa", "backend-only", "old-deployment", "extra-source", "changed-source"])
def test_incomplete_or_different_release_cannot_be_marked_deployed(repository, problem):
    root, record = prepared(repository)
    qa, deployment, health = completion_inputs(root, record)
    if problem == "qa-pending":
        payload = json.loads(qa.read_text()); payload["browser"] = "pending"; qa.write_text(json.dumps(payload))
    elif problem == "partial-qa":
        payload = json.loads(qa.read_text()); payload["source_sha256"] = {"platform-release.json": record["source_sha256"]["platform-release.json"]}; qa.write_text(json.dumps(payload))
    elif problem in {"backend-only", "old-deployment"}:
        payload = json.loads(deployment.read_text())
        payload.update({"scope": "backend-only", "web": "unchanged"} if problem == "backend-only" else {"time": "2000-01-01T00:00:00+00:00"})
        deployment.write_text(json.dumps(payload))
    elif problem == "extra-source":
        (root / "apps/web/src/EXTRA.tsx").write_text("unreviewed")
    else:
        (root / "apps/web/src/test.ts").write_text("changed")
    with pytest.raises(ValueError):
        release.complete(root, "0.2.0", qa, deployment, health)
    assert release.verify(root, "0.2.0")["status"] == "prepared"


def test_only_full_verified_release_records_new_build_and_health(repository):
    root, record = prepared(repository)
    qa, deployment, health = completion_inputs(root, record)
    result = release.complete(root, "0.2.0", qa, deployment, health)
    assert result["status"] == "deployed" and result["build_id"] == "unit-fixture-build"
    assert result["commit"] == record["commit"]


def test_archive_corruption_is_detected(repository):
    root, _ = prepared(repository)
    with (root / ".local/releases/0.2.0/source.zip").open("ab") as archive:
        archive.write(b"tampered")
    with pytest.raises(ValueError, match="摘要"):
        release.verify(root, "0.2.0")


def windows_prepared(repository):
    root, command = repository
    command("config", "core.autocrlf", "true")
    source = root / "apps/web/src/test.ts"
    source.write_bytes(b"export const version = 1;\nexport const ready = true;\n")
    (root / "apps/web/src/new.ts").write_bytes(b"export const edited = true;\n")
    binary = root / "apps/web/src/fixture.bin"
    binary.write_bytes(b"fixture\x00\n")
    (root / "apps/web/src/nonutf8.bin").write_bytes(b"\xff\n")
    command("add", ".")
    command("commit", "-m", "multiline Windows fixture")
    command("tag", "--force", "zhixing-v0.2.0")
    source.unlink()
    command("checkout", "--", "apps/web/src/test.ts")
    assert b"\r\n" in source.read_bytes()
    assert b"\r\n" not in (root / "apps/web/src/new.ts").read_bytes()
    root, record = prepared(repository)
    qa, deployment, health = completion_inputs(root, record)
    payload = json.loads(qa.read_text())
    payload["worktree_sha256"] = {name: release.sha256(root / name) for name in record["source_sha256"]}
    qa.write_text(json.dumps(payload))
    return root, record, qa, deployment, health


def test_windows_checkout_with_frozen_raw_source_can_complete(repository):
    root, record, qa, deployment, health = windows_prepared(repository)
    before = (root / "apps/web/src/test.ts").read_bytes()
    with zipfile.ZipFile(root / ".local/releases/0.2.0/source.zip") as archive:
        assert archive.read("apps/web/src/test.ts") == before.replace(b"\r\n", b"\n")
    result = release.complete(root, "0.2.0", qa, deployment, health)
    assert result["status"] == "deployed" and result["source_sha256"] == record["source_sha256"]
    assert result["worktree_sha256"] == json.loads(qa.read_text())["worktree_sha256"]
    assert (root / "apps/web/src/test.ts").read_bytes() == before
    assert repository[1]("config", "core.autocrlf") == "true"


@pytest.mark.parametrize("problem", [
    "missing-raw", "partial-raw", "extra-raw", "after-qa", "body", "trailing", "bom", "binary", "nonutf8",
])
def test_windows_checkout_does_not_weaken_source_verification(repository, problem):
    root, _, qa, deployment, health = windows_prepared(repository)
    payload = json.loads(qa.read_text())
    source = root / "apps/web/src/test.ts"
    if problem == "missing-raw":
        del payload["worktree_sha256"]
    elif problem == "partial-raw":
        del payload["worktree_sha256"]["platform-release.json"]
    elif problem == "extra-raw":
        payload["worktree_sha256"]["apps/extra.ts"] = "not part of the archive"
    else:
        if problem in {"binary", "nonutf8"}:
            source = root / ("apps/web/src/fixture.bin" if problem == "binary" else "apps/web/src/nonutf8.bin")
            source.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))
        elif problem == "bom":
            source.write_bytes(b"\xef\xbb\xbf" + source.read_bytes())
        elif problem == "trailing":
            source.write_bytes(source.read_bytes().replace(b";\r\n", b"; \r\n"))
        else:
            source.write_bytes(source.read_bytes().replace(b"version = 1", b"version = 9"))
        if problem != "after-qa":
            payload["worktree_sha256"][str(source.relative_to(root)).replace("\\", "/")] = release.sha256(source)
    qa.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        release.complete(root, "0.2.0", qa, deployment, health)
    assert release.verify(root, "0.2.0")["status"] == "prepared"
