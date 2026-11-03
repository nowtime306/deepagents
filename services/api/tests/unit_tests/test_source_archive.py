"""The working-copy archive includes uncommitted code and excludes runtime secrets."""

import subprocess
import zipfile

from app.source_archive import create


def test_archive_captures_new_and_changed_source_without_credentials_or_dependencies(tmp_path):
    root, target = tmp_path / "project", tmp_path / "backup"
    root.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "app.py").write_text("original")
    subprocess.run(["git", "-C", str(root), "add", "app.py"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Local",
            "-c",
            "user.email=local@example.invalid",
            "commit",
            "-m",
            "isolated archive fixture",
        ],
        check=True,
        capture_output=True,
    )
    (root / "app.py").write_text("uncommitted change")
    (root / "new.py").write_text("untracked source")
    (root / ".env").write_text("DO_NOT_ARCHIVE=test-only")
    (root / "model.key").write_text("test-only")
    (root / ".local").mkdir()
    (root / ".local/private.json").write_text("test-only")
    (root / "node_modules").mkdir()
    (root / "node_modules/dependency.js").write_text("excluded")
    create(root, target)
    with zipfile.ZipFile(target / "source.zip") as archive:
        assert set(archive.namelist()) == {"app.py", "new.py"}
        assert archive.read("app.py") == b"uncommitted change"
        assert archive.read("new.py") == b"untracked source"
