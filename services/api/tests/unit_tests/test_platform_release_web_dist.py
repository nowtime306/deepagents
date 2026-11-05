"""A custom served build must match all three release evidence records."""

import json
import os
import subprocess

import pytest
from test_platform_release import completion_inputs, prepared, release
from test_platform_release import repository as repository


def custom_inputs(repository):
    root, record = prepared(repository)
    qa, deployment, health = completion_inputs(root, record)
    directory = root / ".local/served/web/.next-release"
    directory.mkdir(parents=True)
    (directory / "BUILD_ID").write_text("verified-custom-build", encoding="utf-8")
    for path, fields in (
        (qa, {"web_build_id": "verified-custom-build"}),
        (deployment, {"build_id": "verified-custom-build", "web_dist": directory.relative_to(root).as_posix()}),
        (health, {"web_build_id": "verified-custom-build"}),
    ):
        data = json.loads(path.read_text())
        data.update(fields)
        path.write_text(json.dumps(data))
    return root, qa, deployment, health, directory


def test_custom_served_dist_does_not_record_the_old_default_build(repository):
    root, qa, deployment, health, directory = custom_inputs(repository)
    result = release.complete(root, "0.2.0", qa, deployment, health, web_dist=directory)
    assert result["build_id"] == "verified-custom-build"
    assert result["web_dist"] == directory.relative_to(root).as_posix()


@pytest.mark.parametrize("record_name", ["qa", "deployment", "health"])
def test_custom_dist_requires_matching_build_in_every_record(repository, record_name):
    root, qa, deployment, health, directory = custom_inputs(repository)
    path = {"qa": qa, "deployment": deployment, "health": health}[record_name]
    data = json.loads(path.read_text())
    data["build_id" if record_name == "deployment" else "web_build_id"] = "unrelated-build"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="构建"):
        release.complete(root, "0.2.0", qa, deployment, health, web_dist=directory)
    assert release.verify(root, "0.2.0")["status"] == "prepared"


def test_custom_dist_cannot_use_another_declared_served_directory(repository):
    root, qa, deployment, health, directory = custom_inputs(repository)
    data = json.loads(deployment.read_text())
    data["web_dist"] = ".local/other-dist"
    deployment.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="构建"):
        release.complete(root, "0.2.0", qa, deployment, health, web_dist=directory)


def test_custom_dist_outside_project_is_rejected(repository):
    root, qa, deployment, health, directory = custom_inputs(repository)
    with pytest.raises(ValueError, match="目录"):
        release.complete(root, "0.2.0", qa, deployment, health, web_dist=root.parent / "outside-project")


def test_custom_dist_alias_is_rejected(repository):
    root, qa, deployment, health, directory = custom_inputs(repository)
    alias = root / ".local/served/alias"
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        assert alias.absolute().is_relative_to(root.absolute()) and directory.resolve().is_relative_to(root.resolve())
        subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:RELEASE_TEST_ALIAS -Target $env:RELEASE_TEST_TARGET | Out-Null",
            ],
            env={**os.environ, "RELEASE_TEST_ALIAS": str(alias), "RELEASE_TEST_TARGET": str(directory)},
            check=True,
            capture_output=True,
            timeout=10,
        )
        assert alias.is_junction()
    with pytest.raises(ValueError, match="目录"):
        release.complete(root, "0.2.0", qa, deployment, health, web_dist=alias)
