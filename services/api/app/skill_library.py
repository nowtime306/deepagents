"""Validated skill packages exposed through the official read-only backend interface."""

import io
import re
import stat
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath

import yaml
from deepagents.backends.state import StateBackend
from deepagents.backends.utils import create_file_data

from app.errors import fail


def parse_skill(filename: str, data: bytes) -> tuple[str, str, dict[str, str]]:
    """Validate text resources and archive paths without extracting into host directories."""
    if filename.lower().endswith(".zip"):
        files = read_archive(data)
    elif filename.lower().endswith(".md"):
        files = {"SKILL.md": data.decode("utf-8-sig")}
    else:
        fail(422, "UNSUPPORTED_SKILL", "请上传 SKILL.md 或包含 SKILL.md 的 ZIP")
    roots = [name for name in files if PurePosixPath(name).name == "SKILL.md"]
    if len(roots) != 1:
        fail(422, "INVALID_SKILL", "技能包必须包含一个 SKILL.md")
    prefix = roots[0][: -len("SKILL.md")]
    if any(not name.startswith(prefix) for name in files):
        fail(422, "INVALID_SKILL", "所有资源必须位于同一技能目录")
    files = {name[len(prefix) :]: text for name, text in files.items()}
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", files["SKILL.md"], flags=re.DOTALL)
    if not match:
        fail(422, "INVALID_SKILL", "SKILL.md 必须包含 name 和 description 的 YAML 头部")
    try:
        metadata = yaml.safe_load(match[1])
    except yaml.YAMLError:
        fail(422, "INVALID_SKILL", "技能 YAML 头部格式不正确")
    if not isinstance(metadata, dict):
        fail(422, "INVALID_SKILL", "技能头部必须为字段对象")
    name, description = metadata.get("name"), metadata.get("description")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
        fail(422, "INVALID_SKILL", "技能 name 需为小写英文、数字和连字符，最多 64 字符")
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        fail(422, "INVALID_SKILL", "技能 description 需为 1–1024 字符")
    return name, description, files


def read_archive(data: bytes) -> dict[str, str]:
    """Reject traversal, symlinks, excessive expansion and unsupported binary resources."""
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 100 or sum(item.file_size for item in entries) > 2 * 1024 * 1024:
            fail(413, "SKILL_TOO_LARGE", "技能包最多 100 个资源，解压后不超过 2MB")
        for item in entries:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename:
                fail(422, "INVALID_SKILL_PATH", "技能包包含非法文件路径")
            if stat.S_ISLNK(item.external_attr >> 16):
                fail(422, "INVALID_SKILL_PATH", "技能包不允许符号链接")
            if item.is_dir():
                continue
            if item.filename in result or item.file_size > 512 * 1024:
                fail(422, "INVALID_SKILL", "技能包含重复路径或超过 512KB 的资源")
            result[item.filename] = archive.read(item).decode("utf-8-sig")
    return result


class SkillBackend(StateBackend):
    """Read only the selected database resources; no host file or shell access exists."""

    def __init__(self, files: dict[str, str], *, guard: Callable[[], Awaitable[None]] | None = None) -> None:
        super().__init__()
        self.guard = guard
        self.resources = {path: create_file_data(text) for path, text in files.items()}

    def _read_files(self) -> dict:
        return self.resources

    def _send_files_update(self, updates: dict) -> None:
        msg = "技能资源只读，不允许修改；脚本必须通过沙箱执行"
        raise PermissionError(msg)

    async def _check(self) -> None:
        if self.guard:
            await self.guard()

    async def als(self, path: str):
        await self._check()
        return self.ls(path)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000):
        await self._check()
        return self.read(file_path, offset, limit)

    async def aglob(self, pattern: str, path: str | None = None):
        await self._check()
        return self.glob(pattern, path)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None):
        await self._check()
        return self.grep(pattern, path, glob)

    async def adownload_files(self, paths: list[str]):
        await self._check()
        return self.download_files(paths)
