"""Only the declared F07 initialization root can use a complete init audit."""
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_cli_v2_integration import publication_data

from evaluation_runner import fixed_audit, fixed_run, network
from evaluation_runner.fixed_evidence import binding, store
from evaluation_runner.journal import Journal


def prepared(tmp_path, monkeypatch):
    directory, data = publication_data(tmp_path, 'AET-F07')
    attempt = data['attempts'][0]
    attempt['f07_runs'] = {'timer-init': attempt['f07_runs']['timer-init']}
    attempt['f07_refs'] = {'timer-init': attempt['f07_refs']['timer-init']}
    attempt['versioned_roots'] = [{'id': attempt['f07_runs']['timer-init']['root_id'], 'mode': 'workflow'}]
    attempt.update(task_outcome='NEEDS_REVIEW', cleanup_status='NOT_CHECKED', versioned_submission_unknown=False)
    attempt.pop('run_audit_session_id', None)
    session = network.Session('init', fixed_audit.export_identity(data), None)
    attempt['initialization_audit_session_id'] = session.data['session_id']
    monkeypatch.setattr(network, 'ACTIVE', session)
    fixed_audit.begin(directory, session)
    with network.phase('execution'):
        pass
    return directory, data, attempt, session


def test_standard_init_publication_preserves_real_root_and_needs_review(tmp_path, monkeypatch):
    directory, data, attempt, session = prepared(tmp_path, monkeypatch)
    journal = Journal(directory)
    try:
        journal.data = data
        report = fixed_audit.publish(directory, journal, session)
    finally:
        journal.close()
    assert report['gate'] == report['control_gate'] == 'NEEDS_REVIEW'
    assert attempt['started'] and len(attempt['versioned_roots']) == 1
    assert fixed_audit.validate(directory, data)


@pytest.mark.parametrize('damage', ['v1', 'extra_root', 'unknown', 'foreign', 'missing_audit', 'wrong_action', 'after_run'])
def test_init_audit_cannot_replace_incomplete_or_full_run_audit(tmp_path, monkeypatch, damage):
    directory, data, attempt, session = prepared(tmp_path, monkeypatch)
    if damage == 'v1':
        attempt['profile_version'] = 1
    elif damage == 'extra_root':
        attempt['f07_runs']['timer-main'] = dict(attempt['f07_runs']['timer-init'])
    elif damage == 'unknown':
        attempt['versioned_submission_unknown'] = True
    elif damage == 'foreign':
        attempt['f07_runs']['timer-init']['root_id'] = 'foreign'
    elif damage == 'missing_audit':
        attempt.pop('initialization_audit_session_id')
    elif damage == 'wrong_action':
        session.data['action'] = 'cleanup'
    elif damage == 'after_run':
        attempt['run_audit_session_id'] = 'missing-real-run'
    with network.phase('export'):
        pass
    fixed_audit.seal(directory, session, 'COMPLETE')
    with pytest.raises(ValueError):
        fixed_audit.validate(directory, data)


def test_real_fixed_init_records_its_audit_before_business_and_publishes(tmp_path, monkeypatch):
    directory, _ = publication_data(tmp_path, 'AET-F07')
    output = tmp_path / 'fresh-output'
    platform = SimpleNamespace(login=lambda: None, close=lambda: None)
    monkeypatch.setattr(fixed_run, 'Platform', lambda target: platform)

    def initialize(client, journal, attempt, specimen):
        assert attempt['initialization_audit_session_id'] == network.ACTIVE.data['session_id']
        root = str(uuid4())
        attempt.update(profile_version=2, started=True, versioned_submission_unknown=False,
                       f07_runs={'timer-init': {'root_id': root, 'scene': 'timer', 'state': 'OBSERVED'}},
                       versioned_roots=[{'id': root, 'mode': 'workflow'}])
        reference = store(journal.directory, 'f07-v2', {'kind': 'f07-v2', 'binding': binding(journal.data, attempt),
                                                      'body': {'run_id': root, 'scene': 'timer'}})
        attempt['f07_refs'] = {'timer-init': reference}
        journal.save()

    monkeypatch.setattr(fixed_run, 'initialize', initialize)
    monkeypatch.setattr(sys, 'argv', ['fixed_run', 'init', '--case-id', 'AET-F07', '--profile-version', '2',
                                    '--output', str(output), '--target-manifest', str(directory / 'synthetic-target.json')])
    assert fixed_run.main() == 0
    report = json.loads((output / 'report.json').read_bytes())
    assert report['gate'] == report['control_gate'] == 'NEEDS_REVIEW'
    data = json.loads((output / 'journal.json').read_bytes())
    assert fixed_audit.validate(output, data)
    assert data['attempts'][0]['started'] is True
