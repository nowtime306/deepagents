"""Package a committed platform release; keep source and private data backups separate."""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
BUSINESS = ("apps/", "services/", "scripts/", "assets/", "infra/", "libs/deepagents/")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def matches_source(path: Path, archive_digest: str, worktree_digest: str) -> bool:
    """Require frozen raw bytes, allowing only UTF-8 text checkout line endings."""
    data = path.read_bytes()
    current = hashlib.sha256(data).hexdigest()
    if current != worktree_digest:
        return False
    if current == archive_digest:
        return True
    if b"\0" in data or b"\r\n" not in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() == archive_digest


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=root, text=True, encoding="utf-8").strip()


def safe_remote(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit(parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1])) if parsed.scheme in {"http", "https"} else value


def release_directory(root: Path, version: str) -> Path:
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("版本必须为MAJOR.MINOR.PATCH，不能包含路径")
    directory = root / ".local/releases" / version
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("发布目录越界")
    return directory


def source_hashes(archive: Path) -> dict:
    result = {}
    seen = set()
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            path = PurePosixPath(entry.filename)
            if entry.orig_filename != entry.filename:
                raise ValueError("源码包包含非规范路径")
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or re.match(r"^[A-Za-z]:", entry.filename):
                raise ValueError("源码包路径越界")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("源码包包含符号链接")
            folded = entry.filename.casefold().rstrip("/")
            if folded in seen:
                raise ValueError("源码包包含重复或大小写冲突的路径")
            seen.add(folded)
            if path.name.casefold().startswith(".env") and not path.name.casefold().endswith(".example"):
                raise ValueError("源码包包含环境凭据文件")
            if path.suffix.casefold() in {".key", ".pem", ".p12", ".db", ".sqlite"} or ".local" in [part.casefold() for part in path.parts]:
                raise ValueError("源码包包含私有数据或密钥路径")
            if not entry.is_dir() and (entry.filename.startswith(BUSINESS) or entry.filename == "platform-release.json"):
                with zipped.open(entry) as stream:
                    result[entry.filename] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def save(directory: Path, record: dict) -> None:
    temporary = directory / "release.json.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(directory / "release.json")


def prepare(root: Path, version: str, commit: str, reports: list[str]) -> dict:
    directory = release_directory(root, version)
    if directory.exists():
        raise ValueError("该发布版本已有记录，未覆盖")
    identifier = git(root, "rev-parse", "--verify", commit + "^{commit}")
    configuration = json.loads(git(root, "show", identifier + ":platform-release.json"))
    if configuration["version"] != version:
        raise ValueError("Git提交中的平台版本号与请求不符")
    for report in reports:
        if PurePosixPath(report).is_absolute() or ".." in PurePosixPath(report).parts:
            raise ValueError("验收报告路径无效")
        git(root, "cat-file", "-e", identifier + ":" + report)
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".preparing-", dir=directory.parent) as staging:
        archive = Path(staging) / "source.zip"
        subprocess.run(["git", "-c", "core.autocrlf=false", "archive", "--format=zip", "--output=" + str(archive), identifier], cwd=root, check=True)
        files, archive_digest = source_hashes(archive), sha256(archive)
        directory.mkdir()
        archive.replace(directory / "source.zip")
    record = {"version": version, "tag": "zhixing-v" + version, "commit": identifier,
              "branch": git(root, "branch", "--show-current"), "remote": safe_remote(git(root, "remote", "get-url", "origin")),
              "prepared_at": datetime.now(UTC).isoformat(), "status": "prepared", "reports": reports,
              "source_archive_sha256": archive_digest, "source_sha256": files}
    save(directory, record)
    return record


def verify(root: Path, version: str) -> dict:
    directory = release_directory(root, version)
    record = json.loads((directory / "release.json").read_text(encoding="utf-8"))
    if record["version"] != version or sha256(directory / "source.zip") != record["source_archive_sha256"]:
        raise ValueError("发布清单或源码包摘要不符")
    if source_hashes(directory / "source.zip") != record["source_sha256"]:
        raise ValueError("源码文件摘要不符")
    return record


def complete(
    root: Path, version: str, qa_path: Path, deployment_path: Path, health_path: Path,
    *, web_dist: Path | None = None,
) -> dict:
    """Seal verified release evidence, optionally binding an explicitly served build directory."""
    record = verify(root, version)
    if git(root, "rev-parse", record["tag"] + "^{commit}") != record["commit"]:
        raise ValueError("发布标签未固定到已归档提交")
    qa, deployment, health = [json.loads(path.read_text(encoding="utf-8-sig")) for path in (qa_path, deployment_path, health_path)]
    if qa.get("status") != "passed" or qa.get("browser") != "passed" or not qa.get("source_sha256"):
        raise ValueError("独立测试和浏览器验收尚未通过")
    if record["source_sha256"] != qa["source_sha256"]:
        raise ValueError("发布源码与已验收源码不同")
    if (deployment.get("status") != "deployed" or deployment.get("phase") != "complete"
            or deployment.get("scope") != "full" or deployment.get("web") != "rebuilt"):
        raise ValueError("部署尚未完成")
    if datetime.fromisoformat(deployment["time"]) < datetime.fromisoformat(record["prepared_at"]):
        raise ValueError("部署记录早于本次发布准备，不能复用旧部署结果")
    if health.get("api") != "ready" or health.get("web") != "production-ready" or health.get("migrations") != "current":
        raise ValueError("正式服务尚未通过健康检查")
    actual = set(filter(None, git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
                                 *[prefix.rstrip("/") for prefix in BUSINESS], "platform-release.json").split("\0")))
    if actual != set(record["source_sha256"]):
        raise ValueError("当前业务源码文件集合与发布提交不同")
    worktree = qa.get("worktree_sha256", qa["source_sha256"])
    if not isinstance(worktree, dict) or set(worktree) != actual:
        raise ValueError("已验收工作区源码文件集合与发布提交不同")
    if any(not (root / name).resolve().is_relative_to(root.resolve()) or (root / name).is_symlink()
           or not (root / name).is_file() or not matches_source(root / name, digest, worktree[name])
           for name, digest in record["source_sha256"].items()):
        raise ValueError("当前业务源码与发布提交不同")
    directory = root / "apps/web/.next" if web_dist is None else web_dist
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.absolute()
    if web_dist is not None:
        paths = [directory / "BUILD_ID", directory, *directory.parents]
        if (
            ".." in directory.parts
            or not directory.resolve().is_relative_to(root.resolve())
            or not directory.is_relative_to(root.absolute())
            or any(path.is_symlink() or path.is_junction() for path in paths if path.is_relative_to(root.absolute()))
            or not (directory / "BUILD_ID").is_file()
        ):
            raise ValueError("实际构建目录必须位于项目内且不含链接")
    build_id = (directory / "BUILD_ID").read_text(encoding="utf-8").strip()
    if web_dist is not None and (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", build_id)
        or qa.get("web_build_id") != build_id
        or deployment.get("build_id") != build_id
        or health.get("web_build_id") != build_id
        or deployment.get("web_dist") != directory.relative_to(root.absolute()).as_posix()
    ):
        raise ValueError("实际构建与测试、部署或健康记录不一致")
    record.update(status="deployed", deployed_at=deployment["time"], backup=deployment.get("backup"),
                  build_id=build_id, health=health,
                  qa_record=str(qa_path.relative_to(root)), worktree_sha256=worktree)
    if web_dist is not None:
        record["web_dist"] = directory.relative_to(root.absolute()).as_posix()
    save(release_directory(root, version), record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify", "complete", "fail"])
    parser.add_argument("version")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--report", action="append", default=[])
    parser.add_argument("--qa-record", type=Path)
    parser.add_argument("--deployment-record", type=Path, default=ROOT / ".local/platform-deployment.json")
    parser.add_argument("--health-record", type=Path, default=ROOT / ".local/platform-health.json")
    parser.add_argument("--web-dist", type=Path, help="complete: verified served build directory within the project")
    arguments = parser.parse_args()
    if arguments.web_dist is not None and arguments.action != "complete":
        parser.error("--web-dist仅用于complete")
    if arguments.action == "prepare":
        record = prepare(ROOT, arguments.version, arguments.commit, arguments.report)
    elif arguments.action == "complete":
        if not arguments.qa_record:
            parser.error("complete需要--qa-record")
        record = complete(
            ROOT, arguments.version, arguments.qa_record.resolve(), arguments.deployment_record,
            arguments.health_record, web_dist=arguments.web_dist,
        )
    else:
        record = verify(ROOT, arguments.version)
        if arguments.action == "fail":
            record.update(status="failed", failed_at=datetime.now(UTC).isoformat())
            save(release_directory(ROOT, arguments.version), record)
    print(json.dumps({key: record[key] for key in ("version", "commit", "branch", "status")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
