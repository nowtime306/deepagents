"""Independent release integrity boundaries; all Git mutations target pytest's fake repository."""

import json
import zipfile

import pytest
from test_platform_release import completion_inputs, prepared, release
from test_platform_release import repository as repository


def test_moved_fake_tag_cannot_seal_an_earlier_source_archive(repository):
    root, command = repository
    _, record = prepared(repository)
    qa, deployment, health = completion_inputs(root, record)
    (root / "apps/web/src/test.ts").write_text("new source", encoding="utf-8")
    command("add", "apps/web/src/test.ts")
    command("commit", "-m", "fake later release")
    command("tag", "-f", "zhixing-v0.2.0")
    with pytest.raises(ValueError, match="标签"):
        release.complete(root, "0.2.0", qa, deployment, health)
    assert release.verify(root, "0.2.0")["status"] == "prepared"


@pytest.mark.parametrize("damage", ["missing-member", "changed-member"])
def test_recomputed_zip_checksum_does_not_hide_a_changed_member(repository, damage):
    root, _ = repository
    _, record = prepared(repository)
    directory = root / ".local/releases/0.2.0"
    archive = directory / "source.zip"
    with zipfile.ZipFile(archive) as source:
        contents = {item.filename: source.read(item) for item in source.infolist()}
    if damage == "missing-member":
        del contents["apps/web/src/test.ts"]
    else:
        contents["apps/web/src/test.ts"] = b"different member content"
    with zipfile.ZipFile(archive, "w") as target:
        for name, body in contents.items():
            target.writestr(name, body)
    record["source_archive_sha256"] = release.sha256(archive)
    release.save(directory, record)
    with pytest.raises(ValueError, match="源码文件摘要"):
        release.verify(root, "0.2.0")


def test_missing_worktree_file_cannot_be_reported_deployed(repository):
    root, _ = repository
    _, record = prepared(repository)
    qa, deployment, health = completion_inputs(root, record)
    (root / "apps/web/src/test.ts").unlink()
    with pytest.raises(ValueError, match="当前业务源码"):
        release.complete(root, "0.2.0", qa, deployment, health)
    assert release.verify(root, "0.2.0")["status"] == "prepared"


def test_duplicate_prepare_preserves_previous_package_and_record(repository):
    root, _ = repository
    _, record = prepared(repository)
    directory = root / ".local/releases/0.2.0"
    before = {name: (directory / name).read_bytes() for name in ("source.zip", "release.json")}
    with pytest.raises(ValueError, match="已有记录"):
        release.prepare(root, "0.2.0", "HEAD", [])
    assert {name: (directory / name).read_bytes() for name in before} == before
    assert json.loads(before["release.json"])["commit"] == record["commit"]
