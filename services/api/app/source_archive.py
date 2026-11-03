"""Archive tracked and untracked source without runtime files, credentials or dependencies."""

import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

EXCLUDED = {
    ".git",
    ".local",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
    "attachments",
    ".codex",
    ".agents",
    ".claude",
    "dist",
    "build",
}
SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".cjs",
    ".mjs",
    ".css",
    ".scss",
    ".json",
    ".md",
    ".toml",
    ".sql",
    ".yml",
    ".yaml",
    ".txt",
    ".sh",
    ".ps1",
    ".lock",
    ".html",
    ".svg",
    ".xml",
    ".ini",
    ".cfg",
    ".example",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
}
NAMES = {"LICENSE", "Makefile", "Dockerfile", ".gitignore", ".dockerignore", ".python-version"}


def allowed(name):
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and not set(path.parts) & EXCLUDED
        and not path.name.startswith(".env")
        and not path.name.endswith((".key", ".pem", ".p12"))
        and (path.suffix in SUFFIXES or path.name in NAMES)
    )


def create(root: Path, target: Path):
    names = (
        subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=30,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    paths, size = [], 0
    for name in sorted(set(filter(None, names))):
        if not allowed(name):
            continue
        source = root / name
        if not source.exists():
            continue  # Existing deleted source stays deleted in this working-copy archive.
        if not source.resolve().is_relative_to(root.resolve()) or any(
            p.is_symlink() or p.is_junction() for p in (source, *source.parents) if p.is_relative_to(root)
        ):
            raise ValueError("源码包含越界链接")
        if not source.is_file():
            continue
        size += source.stat().st_size
        paths.append((name, source))
        if len(paths) > 30000 or size > 300 * 1024 * 1024:
            raise ValueError("源码归档超过30000文件或300MB限制")
    with zipfile.ZipFile(target / "source.zip", "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, source in paths:
            archive.write(source, name)
    head = (
        subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, timeout=10)
        .stdout.decode()
        .strip()
    )
    branch = (
        subprocess.run(["git", "branch", "--show-current"], cwd=root, check=True, capture_output=True, timeout=10)
        .stdout.decode()
        .strip()
    )
    (target / "source-info.json").write_text(
        json.dumps(
            {
                "head": head,
                "branch": branch,
                "working_copy": True,
                "files": len(paths),
                "bytes": size,
                "note": "包含未提交源码，未提交或推送；凭证和运行数据在独立的受保护备份中",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
