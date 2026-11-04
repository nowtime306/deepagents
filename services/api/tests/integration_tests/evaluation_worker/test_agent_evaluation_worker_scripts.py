"""Active marker/PowerShell env contracts only; no database, target or business is started."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import evaluation_platform_effects as effects
import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())


def load_script(name, path):
    """Load an active repository CLI with a hyphenated filename; never private candidate code."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


target = load_script("worker_marker_target", ROOT / "scripts/evaluation-target.py")


def marker(tmp_path, monkeypatch):
    suite = tmp_path / "suite"
    child = suite / "AET-A02-r1"
    directory = child / "target"
    directory.mkdir(parents=True)
    identifier = str(uuid4())
    commit = "a" * 40
    parent = {
        "schema_version": 1,
        "contract": "platform-effect-permit-v1",
        "parent_run_id": identifier,
        "parent_owner_id": str(uuid4()),
        "candidate_commit": commit,
        "selected_cases": ["AET-A02"],
    }
    (suite / "platform-parent.json").write_text(json.dumps(parent), encoding="utf-8")
    (child / "suite-context.json").write_text(
        json.dumps({"candidate_commit": commit, "case_id": "AET-A02"}), encoding="utf-8"
    )
    monkeypatch.setattr(target, "ROOT", ROOT)
    monkeypatch.setenv(effects.ORIGIN, "http://127.0.0.1:23456")
    monkeypatch.setenv(effects.SECRET, "s" * 40)
    return directory, identifier, commit


def test_marker_binds_case_and_candidate_without_copying_secret_to_metadata(tmp_path, monkeypatch):
    directory, identifier, commit = marker(tmp_path, monkeypatch)
    value = target.platform_parent(directory, commit, identifier)
    assert value["parent_run_id"] == identifier and value["case_id"] == "AET-A02"
    assert "secret" not in str(value).lower() and "127.0.0.1" not in str(value)


@pytest.mark.parametrize("failure", ["lost_marker", "lost_env", "foreign_run", "foreign_candidate"])
def test_required_marker_and_env_fail_before_any_database_provisioning(tmp_path, monkeypatch, failure):
    directory, identifier, commit = marker(tmp_path, monkeypatch)
    if failure == "lost_marker":
        (directory.parent.parent / "platform-parent.json").unlink()
    elif failure == "lost_env":
        monkeypatch.delenv(effects.ORIGIN)
        monkeypatch.delenv(effects.SECRET)
    elif failure == "foreign_run":
        identifier = str(uuid4())
    else:
        commit = "b" * 40
    with pytest.raises((ValueError, RuntimeError)):
        target.platform_parent(directory, commit, identifier)
    assert not (directory / "target-state.json").exists()


@pytest.mark.parametrize("delegation", [False, True])
def test_real_powershell_child_forwards_only_explicit_permit_pair_not_result_or_provider_keys(tmp_path, delegation):
    out = tmp_path / "private"
    out.mkdir()
    code = (
        "import os,json; print(json.dumps({k:(k in os.environ) for k in ["
        "'EVALUATION_PLATFORM_PERMIT_ORIGIN','EVALUATION_PLATFORM_PERMIT_SECRET',"
        "'EVALUATION_PLATFORM_RESULT_SECRET','OPENAI_API_KEY','CONFIDENT_API_KEY']}))"
    )
    harness = tmp_path / "env-probe.ps1"
    helper = str(ROOT / "scripts/evaluation-process.ps1").replace("'", "''")
    python = str(Path(sys.executable)).replace("'", "''")
    output = str(out).replace("'", "''")
    program = code.replace("'", "''")
    harness.write_text(
        ". '"
        + helper
        + "'\n$code = Invoke-EvaluationProcess '"
        + python
        + "' @('-I','-c','"
        + program
        + "') '"
        + output
        + "' 'probe' -ParentDelegation:$"
        + str(delegation).lower()
        + "\nexit $code\n",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
    }
    env.update(
        EVALUATION_PLATFORM_PERMIT_ORIGIN="http://127.0.0.1:23456",
        EVALUATION_PLATFORM_PERMIT_SECRET="s" * 40,
        EVALUATION_PLATFORM_RESULT_SECRET="r" * 40,
        OPENAI_API_KEY="unit-provider-key",
        CONFIDENT_API_KEY="unit-confident-key",
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(harness)],
        env=env,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0
    data = json.loads(
        next(path for path in out.glob("probe-*.log") if not path.name.endswith("-error.log")).read_text(
            encoding="utf-8-sig"
        )
    )
    assert (
        data["EVALUATION_PLATFORM_PERMIT_ORIGIN"] is delegation
        and data["EVALUATION_PLATFORM_PERMIT_SECRET"] is delegation
    )
    assert (
        data["EVALUATION_PLATFORM_RESULT_SECRET"] is False
        and data["OPENAI_API_KEY"] is False
        and data["CONFIDENT_API_KEY"] is False
    )


def test_settled_index_only_history_still_requires_evidence_before_direct_target_drop():
    class Database:
        def execute(self, query):
            return SimpleNamespace(fetchone=lambda: (1,) if "resource_operations" in str(query) else None)

    assert target.business_history(Database()) is True
