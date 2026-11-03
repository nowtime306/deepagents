"""Trusted sandbox-side workspace operations; never follows links outside its root."""

import base64
import json
import os
import stat
import sys

MAX_FILE = 1024 * 1024
MAX_FILES = 300


def parts(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in ("\\", "\x00", ":")):
        raise ValueError("invalid_path")
    pieces = path.split("/")[1:]
    if any(piece in (".", "..") for piece in pieces):
        raise ValueError("invalid_path")
    return [piece for piece in pieces if piece]


def parent(root: int, pieces: list[str], create: bool = False) -> int:
    current = os.dup(root)
    try:
        for piece in pieces:
            if create:
                try:
                    os.mkdir(piece, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(piece, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def read(root: int, path: str) -> bytes:
    pieces = parts(path)
    if not pieces:
        raise ValueError("is_directory")
    directory = parent(root, pieces[:-1])
    try:
        descriptor = os.open(pieces[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
                raise ValueError("unsafe_or_oversized_file")
            data = source.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ValueError("file_too_large")
            return data
    finally:
        os.close(directory)


def write(root: int, path: str, data: bytes, replace: bool) -> None:
    pieces = parts(path)
    if not pieces or len(data) > MAX_FILE:
        raise ValueError("invalid_path_or_size")
    directory = parent(root, pieces[:-1], create=True)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open(pieces[-1], flags | (0 if replace else os.O_EXCL), 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as target:
            info = os.fstat(target.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe_file")
            os.ftruncate(target.fileno(), 0)
            target.write(data)
    finally:
        os.close(directory)


def scan(root: int, exclude: list[str] | None = None) -> list[dict]:
    output = []

    def walk(directory: int, prefix: str, depth: int) -> None:
        if depth > 20:
            raise ValueError("workspace_depth_exceeded")
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(output) >= MAX_FILES:
                    raise ValueError("workspace_file_limit")
                info = entry.stat(follow_symlinks=False)
                path = prefix + "/" + entry.name
                if path in (exclude or []):
                    continue
                if stat.S_ISLNK(info.st_mode):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    output.append({"path": path + "/", "is_dir": True, "size": 0})
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        walk(child, path, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    output.append({"path": path, "is_dir": False, "size": info.st_size})

    walk(root, "", 0)
    return output


def operate(root: int, body: dict) -> dict:
    operation = body["operation"]
    if operation == "scan":
        return {"entries": scan(root, body.get("exclude"))}
    if operation == "read":
        return {"data": base64.b64encode(read(root, body["path"])).decode()}
    if operation == "write":
        write(root, body["path"], base64.b64decode(body["data"], validate=True), body.get("replace", False))
        return {}
    if operation == "edit":
        old, new = body["old"], body["new"]
        if not old:
            raise ValueError("empty_old_string")
        text = read(root, body["path"]).decode("utf-8")
        count = text.count(old)
        if count == 0 or (count > 1 and not body.get("replace_all", False)):
            raise ValueError("edit_match_not_unique")
        write(root, body["path"], text.replace(old, new).encode(), True)
        return {"occurrences": count}
    if operation == "delete":
        pieces = parts(body["path"])
        if not pieces:
            raise ValueError("invalid_path")
        directory = parent(root, pieces[:-1])
        try:
            # Validate through a non-following read before unlinking the name.
            read(root, body["path"])
            os.unlink(pieces[-1], dir_fd=directory)
        finally:
            os.close(directory)
        return {}
    raise ValueError("unsupported_operation")


def main() -> None:
    try:
        body = json.loads(base64.b64decode(sys.argv[1], validate=True))
        root = os.open("/workspace", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            result = operate(root, body)
        finally:
            os.close(root)
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"error": "工作区文件不存在、路径不安全或超过限制"}))


if __name__ == "__main__":
    main()
