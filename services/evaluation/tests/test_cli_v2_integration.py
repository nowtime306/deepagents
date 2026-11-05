"""Offline CLI integration: dependency identity, sealed reports, and shell entry."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_profiles_v2 import fixture

from evaluation_runner import fixed_audit, fixed_evidence, fixed_run, network
from evaluation_runner.contracts import Target
from evaluation_runner.journal import Journal, atomic_json, digest
from evaluation_runner.profiles_v2 import lifecycle
from evaluation_runner.profiles_v2.registry import selection

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))


def script(name):
    spec = importlib.util.spec_from_file_location('tested_' + name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dependencies(tmp_path):
    source = script('evaluation_sources')
    actual = next(parent for parent in Path(__file__).resolve().parents
                  if (parent / 'services/api/.venv/Scripts/python.exe').is_file())
    contract = ROOT / 'services/evaluation/profile_support/suite-capabilities-v2.json'
    target = tmp_path / contract.relative_to(ROOT)
    target.parent.mkdir(parents=True)
    shutil.copyfile(contract, target)
    for relative, _ in source.DEPENDENCIES.values():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(actual / relative, target)
    return source, actual


@pytest.mark.parametrize('index', range(6))
def test_each_real_dependency_drift_changes_inventory_and_blocks_v2(tmp_path, index):
    source, _ = dependencies(tmp_path)
    origins = {name: str(tmp_path / relative) for name, (relative, _) in source.DEPENDENCIES.items()}
    with patch.object(source, 'runtime_origins', return_value=origins):
        original = source.source_fingerprint(tmp_path, profile_version=2)
        relative = list(source.DEPENDENCIES.values())[index][0]
        path = tmp_path / relative
        path.write_bytes(path.read_bytes() + b'\n# isolated counterexample\n')
        assert source.source_fingerprint(tmp_path) != original
        with pytest.raises(ValueError, match='dependency changed'):
            source.source_fingerprint(tmp_path, profile_version=2)


def test_actual_api_interpreter_origins_and_missing_or_shadowed_inputs(tmp_path):
    source, actual = dependencies(tmp_path)
    origins = source.runtime_origins(actual)  # Actual API Python, no runner imports.
    assert {name: Path(path).resolve() for name, path in origins.items()} == {
        name: (actual / relative).resolve() for name, (relative, _) in source.DEPENDENCIES.items()}
    local = {name: str(tmp_path / relative) for name, (relative, _) in source.DEPENDENCIES.items()}
    first = next(iter(local))
    with patch.object(source, 'runtime_origins', return_value=local):
        source.validate_runtime(tmp_path)
        local[first] = str(tmp_path / 'shadow.py')
        with pytest.raises(ValueError):
            source.validate_runtime(tmp_path)
        local[first] = str(tmp_path / source.DEPENDENCIES[first][0])
        Path(local[first]).unlink()
        with pytest.raises(ValueError):
            source.validate_runtime(tmp_path)
    assert source.source_fingerprint(tmp_path)  # Legacy inventory does not demand a v2 environment.


def publication_data(tmp_path, case_id):
    directory, data = fixture(case_id, tmp_path)
    data['suite_context'].update(schema_version=1, repeat_index=1, suite_id=str(uuid4()))
    data['case_definition_digest'] = data['suite_context']['case_definition_digest']
    specimen, _, _ = selection(case_id, 2)
    target = Target(environment_id=data['environment_id'], owner_id=data['owner_id'],
                    platform_commit=data['suite_context']['candidate_commit'], api_base_url='http://127.0.0.1:39999',
                    required_migrations=['085'], fixture_profile=specimen.fixture_profile)
    data['target_digest'] = digest(target.model_dump())
    atomic_json(directory / 'synthetic-target.json', target.model_dump())
    attempt = data['attempts'][0]
    attempt.update(owner_id=data['owner_id'], environment_id=data['environment_id'])
    # These are synthetic fixture copies, re-enveloped solely for the standard
    # publication contract. Archived execution journals are never migrated.
    hashes = {}
    for path in (directory / 'evidence').rglob('*.json'):
        value = json.loads(path.read_bytes())
        if 'binding' in value:
            value['binding'] = fixed_evidence.binding(data, attempt)
            atomic_json(path, value)
        hashes[path.relative_to(directory).as_posix()] = fixed_evidence.file_digest(path)

    def rehash(value):
        if isinstance(value, dict):
            if value.get('path') in hashes and 'sha256' in value:
                value['sha256'] = hashes[value['path']]
            for child in value.values():
                rehash(child)
        elif isinstance(value, list):
            for child in value:
                rehash(child)

    rehash(attempt)
    attempt['business_outcomes'] = lifecycle.verified(directory, data, attempt, specimen)['business_outcomes']
    return directory, data


def publish_fixture(tmp_path, monkeypatch, case_id):
    directory, data = publication_data(tmp_path, case_id)
    data['network_audit_refs'] = []
    attempt = data['attempts'][0]
    attempt['network_audit_refs'] = []
    session = network.Session('run', fixed_audit.export_identity(data), None)
    attempt['run_audit_session_id'] = session.data['session_id']
    monkeypatch.setattr(network, 'ACTIVE', session)
    fixed_audit.begin(directory, session)
    with network.phase('execution'):
        pass  # Fixed synthetic facts already exist; no model, tool, or server is run.
    journal = Journal(directory)
    try:
        journal.data = data
        report = fixed_audit.publish(directory, journal, session)
    finally:
        journal.close()
    ready = fixed_audit.validate_ready(directory)
    assert ready['gate'] == report['gate']
    return directory, report, ready


@pytest.mark.parametrize('case_id', ['AET-W07', 'AET-W08', 'AET-F07'])
def test_real_publish_to_ready_keeps_v2_business_and_control_gates(tmp_path, monkeypatch, case_id):
    directory, report, ready = publish_fixture(tmp_path, monkeypatch, case_id)
    assert report['control_gate'] == 'PASS'
    assert report['gate'] == ('PASS' if case_id == 'AET-W08' else 'NEEDS_REVIEW')
    assert ready['suite_context']['profile_version'] == 2
    if case_id != 'AET-W08':
        assert report['attempts'][0]['cleanup_status'] != 'CLEAN'
        assert report['attempts'][0]['cost'] is None
        assert 'NEEDS_REVIEW' in (directory / 'results.xml').read_text()


@pytest.mark.parametrize('field,value', [('profile_version', 1), ('profile_version', True),
                                      ('case_definition_digest', '0' * 64)])
def test_ready_rejects_resealed_wrong_version_or_definition(tmp_path, monkeypatch, field, value):
    directory, _, ready = publish_fixture(tmp_path, monkeypatch, 'AET-W08')
    ready['suite_context'][field] = value
    atomic_json(directory / fixed_audit.READY, ready)
    with pytest.raises(ValueError):
        fixed_audit.validate_ready(directory)


def test_ready_recomputes_a_resealed_forged_control_gate(tmp_path, monkeypatch):
    directory, report, ready = publish_fixture(tmp_path, monkeypatch, 'AET-F07')
    report['control_evidence'] = []
    atomic_json(directory / 'report.json', report)
    ready['files']['report.json'] = fixed_evidence.file_digest(directory / 'report.json')
    atomic_json(directory / fixed_audit.READY, ready)
    with pytest.raises(ValueError, match='control report'):
        fixed_audit.validate_ready(directory)


@pytest.mark.parametrize('version', [1, True, None, '2'])
def test_v2_context_rejected_before_initialization_with_correct_v2_digests(version):
    specimen, dataset, description = selection('AET-W08', 2)
    context = {**description, 'profile_version': version}
    with pytest.raises(ValueError, match='profile version'):
        fixed_run.suite_context(None, specimen, dataset, SimpleNamespace(platform_commit='a' * 40), context)


def test_context_path_cannot_replace_recorded_v2_with_v1(tmp_path):
    directory, data = publication_data(tmp_path, 'AET-W08')
    specimen, dataset, _ = selection('AET-W08', 2)
    path = directory / 'request.json'
    atomic_json(path, {**data['suite_context'], 'profile_version': 1})
    with pytest.raises(ValueError, match='profile version'):
        fixed_run.validate_offline_report(data, specimen, dataset, context_path=path, directory=directory)


def test_wrong_version_context_is_rejected_before_platform_login_or_effects(tmp_path, monkeypatch):
    directory, data = publication_data(tmp_path, 'AET-W08')
    request = directory / 'wrong-context.json'
    atomic_json(request, {**data['suite_context'], 'profile_version': 1})
    monkeypatch.setattr(sys, 'argv', ['fixed_run', 'init', '--case-id', 'AET-W08', '--profile-version', '2',
                                    '--output', str(tmp_path / 'new-output'), '--target-manifest',
                                    str(directory / 'synthetic-target.json'), '--suite-context', str(request)])
    with patch.object(fixed_run, 'Platform') as platform:
        assert fixed_run.main() == 2
        platform.assert_not_called()


@pytest.mark.parametrize('code,forged,mismatch', [(0, False, False), (1, False, True), (0, True, True)])
def test_suite_consumes_actual_sealed_v2_ready_and_preserves_unknown(tmp_path, monkeypatch, code, forged, mismatch):
    suite = script('evaluation-suite')
    directory, report, ready = publish_fixture(tmp_path, monkeypatch, 'AET-F07')
    state = tmp_path / 'state'
    state.mkdir()
    shutil.copyfile(directory / 'synthetic-target.json', state / 'target-manifest.json')
    atomic_json(tmp_path / 'suite-context.json', ready['suite_context'])
    atomic_json(state / 'credentials.json', {})
    if forged:
        report['control_evidence'] = []
        atomic_json(directory / 'report.json', report)
        ready['files']['report.json'] = fixed_evidence.file_digest(directory / 'report.json')
        atomic_json(directory / fixed_audit.READY, ready)
    attempt = {'case_id': 'AET-F07', 'profile_version': 2, 'status': 'NEEDS_REVIEW',
               'candidate_commit': ready['suite_context']['candidate_commit'], 'repeat_ordinal': 1,
               'case_digest': ready['case_definition_digest']}
    with patch.object(suite, 'check_candidate'), patch.object(suite, 'verify_parent_target'), \
            patch.object(suite, 'finalize_target'):
        suite.perform_attempt(attempt, state, directory, lambda action, *args: code if action == 'run' else 0, 'a' * 40)
    assert attempt['status'] == 'NEEDS_REVIEW'
    assert ('infrastructure_error' in attempt) is mismatch
    assert attempt['report_validation']['status'] == ('REJECTED' if forged else 'VERIFIED')


@pytest.mark.parametrize('version,code,control,mismatch', [(2, 0, 'PASS', False), (2, 1, 'PASS', True),
                                                        (2, 0, 'NEEDS_REVIEW', True), (1, 0, 'PASS', True)])
def test_suite_exit_uses_only_verified_v2_control_without_laundering_business(tmp_path, version, code, control, mismatch):
    suite = script('evaluation-suite')
    state = tmp_path / 'state'
    state.mkdir()
    atomic_json(state / 'credentials.json', {})
    result = tmp_path / 'result'
    result.mkdir()
    atomic_json(result / 'report.json', {})
    attempt = {'profile_version': version, 'status': 'NEEDS_REVIEW', 'business_started': True}
    report = {'gate': 'NEEDS_REVIEW', 'control_gate': control, 'attempts': []}
    with patch.object(suite, 'check_candidate'), patch.object(suite, 'verify_parent_target'), \
            patch.object(suite, 'verified_child_report', return_value=report), patch.object(suite, 'finalize_target'):
        suite.perform_attempt(attempt, state, result, lambda action, *args: code if action == 'run' else 0, 'a' * 40)
    assert attempt['status'] == 'NEEDS_REVIEW'
    assert ('infrastructure_error' in attempt) is mismatch


@pytest.mark.parametrize('version', [1, 2])
def test_powershell_standard_entry_forwards_version_without_starting_services(tmp_path, version):
    source = ROOT / 'scripts/evaluation-suite.ps1'
    shutil.copyfile(source, tmp_path / source.name)
    helper = """function New-EvaluationOutput($path) { return $path }
function Get-EvaluationPython { return 'unused' }
function Invoke-EvaluationProcess($python, $arguments, $directory, $name) {
    ConvertTo-Json -Compress -InputObject $arguments | Set-Content -LiteralPath (Join-Path $directory 'argv.json')
    return 0
}
"""
    (tmp_path / 'evaluation-process.ps1').write_text(helper, encoding='utf-8')
    for filename in ('dataset.json', 'admin.txt'):
        (tmp_path / filename).write_text('{}')
    args = ['pwsh', '-NoProfile', '-File', str(tmp_path / source.name), '-Dataset', str(tmp_path / 'dataset.json'),
            '-Output', str(tmp_path), '-AdminConnectionFile', str(tmp_path / 'admin.txt'), '-CandidateCommit', 'a' * 40,
            '-RulesId', 'controlled-a20-v' + str(version), '-MaxSeconds', '1', '-MaxModelCalls', '0',
            '-MaxTokens', '0', '-MaxSandboxes', '0']
    if version == 2:
        args += ['-ProfileVersion', '2']
    subprocess.run(args, check=True, capture_output=True)
    actual = json.loads((tmp_path / 'argv.json').read_text(encoding='utf-8-sig'))
    assert actual[actual.index('--profile-version') + 1] == str(version)


@pytest.mark.parametrize('case_id', ['AET-W07', 'AET-F07'])
def test_run_suite_constructs_versioned_attempt_for_real_perform_path(tmp_path, monkeypatch, case_id):
    suite = script('evaluation-suite')
    _, _, descriptor = selection(case_id, 2)
    directory, report, _ = publish_fixture(tmp_path, monkeypatch, case_id)
    repository = tmp_path / 'repo'
    for relative in ['services/evaluation/profile_support/suite-capabilities-v2.json',
                     'services/evaluation/src/evaluation_runner/fixed_run.py']:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
    dataset = ROOT / 'services/evaluation/src/evaluation_runner/profiles_v2/definitions/catalog.json'
    output = repository / '.local/agent-evaluations/new-suite'
    specimen, _, _ = selection(case_id, 2)
    args = SimpleNamespace(profile_version=2, rules_id='controlled-a20-v2', output=output,
                           dataset=dataset, case_id=[case_id], repeat_count=1,
                           max_model_calls=specimen.budgets.max_model_calls,
                           max_tokens=specimen.budgets.max_tokens,
                           max_sandboxes=descriptor['maximum_sandboxes'], max_seconds=300,
                           candidate_commit='a' * 40, admin_connection_file=None, manager_key_file=None)
    actions = []

    def invoke(arguments, child, action, *rest):
        actions.append(action)
        if action == 'start':
            (child / 'target').mkdir()
            atomic_json(child / 'target/credentials.json', {})
        if action == 'run':
            (child / 'result').mkdir()
            shutil.copyfile(directory / 'report.json', child / 'result/report.json')
        return 0

    def verified(state, result, attempt):
        # Use a genuinely published and validated report; target ownership itself
        # is covered by the preceding sealed-ready tests, not this construction test.
        fixed_audit.validate_ready(directory)
        return report

    with patch.object(suite, 'ROOT', repository), patch.object(suite, 'check_candidate'), \
            patch.object(suite.subprocess, 'check_output', return_value='a' * 40), \
            patch.object(suite, 'source_fingerprint', return_value={'fixed': 'source'}), \
            patch.object(suite, 'describe_case', return_value=descriptor), patch.object(suite, 'invoke', side_effect=invoke), \
            patch.object(suite, 'verify_parent_target'), patch.object(suite, 'verified_child_report', side_effect=verified), \
            patch.object(suite, 'finalize_target'):
        assert suite.run_suite(args) == 1  # Business/retention still requires review.
    stored = json.loads((output / 'suite.json').read_bytes())
    attempt = stored['attempts'][0]
    assert attempt['report_validation']['status'] == 'VERIFIED'
    assert 'infrastructure_error' not in attempt
    assert attempt['profile_version'] == 2
    assert attempt['status'] == 'NEEDS_REVIEW' and attempt['cleanup'] == 'RETAINED'
    assert stored['stopped_reason'] == 'TARGET_RETAINED_CAPACITY_FULL'
    assert actions == ['start', 'preflight', 'init', 'run']
