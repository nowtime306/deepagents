"""Trusted Linux-only Git snapshot helper, executed in a task container, never on the host."""

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

LIMIT = 20 * 1024 * 1024
FILE = 1024 * 1024
ROOT = Path("/workspace")
REPO = ROOT / "repo"
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}


def path(value: str) -> str:
    if (not value or len(value) > 500 or any(ord(c) < 32 for c in value) or "\\" in value or ":" in value
        or any(p in {"", ".", ".."} or p.lower() == ".git" for p in value.split("/"))):
        raise ValueError("unsafe_git_path")
    return value


def read(file: Path, limit: int = FILE) -> bytes:
    # Directory descriptors also close the parent-link race during informational model scans.
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in file.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("git_file_refused")
        data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError("git_file_limit")
        return data


def git(directory: Path, *args: str, accepted: tuple = (0,)) -> bytes:
    command = ["git", "--git-dir=" + str(directory), "--work-tree=" + str(REPO),
               "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null",
               "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=never", *args]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(command, env=ENV, stdout=output, stderr=errors, timeout=30, check=False)
        output.seek(0)
        data = output.read(LIMIT + 1)
        if result.returncode not in accepted or len(data) > LIMIT:
            raise ValueError("git_operation_failed")
        return data


def fresh(directory: Path, branch: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.-]{0,199}", branch) or ".." in branch:
        raise ValueError("git_branch_invalid")
    git(directory, "init", "--template=", "--initial-branch=" + branch, str(REPO))
    git(directory, "config", "user.name", "Owner Agent Task")
    git(directory, "config", "user.email", "task@local.invalid")


def isolated(directory: Path) -> Path:
    """Ignore task-controlled config, hooks, index extensions and object alternates."""
    original, target = REPO / ".git", directory / "git"
    fresh(target, "snapshot")
    used, count = 0, 0
    for name in ("objects", "refs", "packed-refs", "HEAD", "index", "info/exclude"):
        source = original / name
        if not source.exists() and not source.is_symlink():
            continue
        files = list(source.rglob("*")) if source.is_dir() and not source.is_symlink() else [source]
        for file in files:
            if file.is_symlink():
                raise ValueError("git_link_refused")
            if file.is_dir():
                continue
            relative = file.relative_to(original)
            if relative.as_posix() in {"objects/info/alternates", "objects/info/http-alternates"}:
                raise ValueError("git_alternates_refused")
            data = read(file, LIMIT)
            count, used = count + 1, used + len(data)
            if count > 2000 or used > LIMIT:
                raise ValueError("git_history_limit")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    return target


def head_files(directory: Path) -> dict:
    result = {}
    total = 0
    for entry in git(directory, "ls-tree", "-rlz", "--full-tree", "HEAD").split(b"\0"):
        if not entry:
            continue
        info, raw_path = entry.split(b"\t", 1)
        mode, kind, _, size = info.decode().split()
        name = path(raw_path.decode("utf-8"))
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("git_head_entry_refused")
        total += int(size)
        if int(size) > FILE or total > LIMIT:
            raise ValueError("git_head_size_limit")
        result[name] = mode
        if len(result) > 180:
            raise ValueError("git_head_file_limit")
    return result


def token_path(token: str) -> Path:
    if not re.fullmatch("[0-9a-f]{32}", token):
        raise ValueError("git_transfer_invalid")
    return Path("/tmp/owner-agent-git-" + token + ".bundle")


def inventory(directory: Path, baseline: list[str]) -> tuple[list[dict], dict]:
    head = head_files(directory)
    candidates = set(head) | {path(name) for name in baseline}
    raw = git(directory, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    candidates |= {path(name.decode("utf-8")) for name in raw.split(b"\0") if name}
    output, modes, total = [], {}, 0
    for name in sorted(candidates):
        file = REPO / name
        if not file.exists() and not file.is_symlink():
            continue
        data = read(file)
        total += len(data)
        output.append({"path": "/repo/" + name, "is_dir": False, "size": len(data)})
        modes[name] = "100755" if file.stat().st_mode & stat.S_IXUSR else "100644"
    if len(output) > 180 or total > LIMIT:
        raise ValueError("git_workspace_limit")
    return output, {"modes": modes, "deleted": sorted(set(head) - set(modes))}


def operate(body: dict) -> dict:
    operation = body["operation"]
    if operation == "init":
        if (REPO / ".git").exists() or (REPO / ".git").is_symlink():
            raise ValueError("git_already_exists")
        REPO.mkdir(exist_ok=True)
        fresh(REPO / ".git", body["branch"])
        for name, mode in body["modes"].items():
            file = REPO / path(name)
            read(file)
            file.chmod(0o700 if mode == "100755" else 0o600)
        git(REPO / ".git", "add", "--force", "--all", "--", ".")
        git(REPO / ".git", "commit", "--allow-empty", "--no-gpg-sign", "-m", "Source " + body["sha"])
        return {}
    if operation in {"capture", "inventory"}:
        with tempfile.TemporaryDirectory(prefix="owner-agent-git-") as temporary:
            directory = isolated(Path(temporary))
            entries, metadata = inventory(directory, body["baseline"])
            if operation == "inventory":
                return {"entries": entries}
            objects = git(directory, "cat-file", "--batch-check", "--batch-all-objects").splitlines()
            if len(objects) > 2000 or sum(int(item.split()[-1]) for item in objects) > LIMIT:
                raise ValueError("git_history_expanded_limit")
            head = git(directory, "rev-parse", "HEAD").decode().strip()
            branch = git(directory, "symbolic-ref", "--short", "-q", "HEAD", accepted=(0, 1)).decode().strip()
            excluded = directory / "info/exclude"
            ignore = base64.b64encode(read(excluded, 65536)).decode() if excluded.exists() else ""
            target = token_path(body["token"])
            if target.exists() or target.is_symlink():
                raise ValueError("git_transfer_exists")
            git(directory, "bundle", "create", str(target), "--all", "HEAD")
            data = read(target, LIMIT)
            return {"entries": entries, "metadata": {**metadata, "head": head, "branch": branch, "exclude": ignore},
                    "size": len(data), "digest": hashlib.sha256(data).hexdigest()}
    if operation == "read_chunk":
        data = read(token_path(body["token"]), LIMIT)
        offset = body["offset"]
        if not isinstance(offset, int) or not 0 <= offset < len(data):
            raise ValueError("git_chunk_offset")
        return {"data": base64.b64encode(data[offset:offset + 512 * 1024]).decode()}
    if operation == "write_chunk":
        file, offset = token_path(body["token"]), body["offset"]
        data = base64.b64decode(body["data"], validate=True)
        if not isinstance(offset, int) or offset < 0 or len(data) > 512 * 1024 or offset + len(data) > LIMIT:
            raise ValueError("git_chunk_limit")
        descriptor = os.open(file, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                             | (os.O_CREAT | os.O_EXCL if offset == 0 else 0), 0o600)
        with os.fdopen(descriptor, "r+b") as output:
            info = os.fstat(output.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != offset:
                raise ValueError("git_transfer_conflict")
            output.seek(offset)
            output.write(data)
        return {}
    if operation == "restore":
        file = token_path(body["token"])
        data = read(file, LIMIT)
        if hashlib.sha256(data).hexdigest() != body["digest"]:
            raise ValueError("git_bundle_integrity")
        if (REPO / ".git").exists() or (REPO / ".git").is_symlink():
            raise ValueError("git_already_exists")
        REPO.mkdir(exist_ok=True)
        directory, meta = REPO / ".git", body["metadata"]
        fresh(directory, "restore")
        git(directory, "bundle", "verify", str(file))
        temporary_branch = "owner-agent-restore-" + body["token"]
        git(directory, "-c", "protocol.file.allow=always", "fetch", str(file),
            "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*", "HEAD:refs/heads/" + temporary_branch)
        git(directory, "symbolic-ref", "HEAD", "refs/heads/" + temporary_branch)
        head_files(directory)
        if git(directory, "rev-parse", "HEAD").decode().strip() != meta["head"]:
            raise ValueError("git_head_integrity")
        git(directory, "checkout", "--force", meta["branch"] if meta["branch"] else "--detach",
            *([] if meta["branch"] else [meta["head"]]))
        git(directory, "update-ref", "-d", "refs/heads/" + temporary_branch)
        if meta.get("exclude"):
            excluded = directory / "info/exclude"
            excluded.parent.mkdir(exist_ok=True)
            excluded.write_bytes(base64.b64decode(meta["exclude"], validate=True))
        for name in meta["deleted"]:
            target = REPO / path(name)
            read(target)
            target.unlink()
        file.unlink()
        return {}
    if operation == "modes":
        for name, mode in body["modes"].items():
            file = REPO / path(name)
            read(file)
            file.chmod(0o700 if mode == "100755" else 0o600)
        return {}
    raise ValueError("git_operation_invalid")


def main() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    try:
        body = json.loads(base64.b64decode(sys.argv[1], validate=True))
        print(json.dumps(operate(body)))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)[:160]}))


if __name__ == "__main__":
    main()
