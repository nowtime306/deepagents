"""A confirmed W07 receiver close can only be re-confirmed without new writes."""
import copy
from types import SimpleNamespace

import pytest
from evaluation_runner.fixed_evidence import file_digest, load
from evaluation_runner.journal import atomic_json, digest
from evaluation_runner.profiles_v2.registry import selection
from evaluation_runner.profiles_v2.w07 import handler
from test_profiles_v2 import fixture


def prepared(tmp_path):
    directory, data = fixture('AET-W07', tmp_path)
    attempt = data['attempts'][0]
    body = load(directory, attempt['w07_closed'], 'w07-v2')['body']
    platform = SimpleNamespace(get=lambda path: copy.deepcopy(body))
    journal = SimpleNamespace(directory=directory, data=data, save=lambda: None,
                              post=lambda *args: pytest.fail('Confirmed close must never issue another POST'))
    specimen, _, _ = selection('AET-W07', 2)
    return journal, attempt, body, platform, specimen


def test_expired_case_can_readonly_confirm_the_same_closed_unknown_twice(tmp_path):
    journal, attempt, _, platform, specimen = prepared(tmp_path)
    attempt['w07_deadline'] = '2000-01-01T00:00:00+00:00'
    original = copy.deepcopy(attempt)
    for _ in range(2):
        assert handler.cleanup(platform, journal, attempt, specimen) == 'CLEAN_RECEIVER_UNKNOWN_OPERATION_RETAINED'
    assert attempt == original
    assert attempt['task_outcome'] == 'NEEDS_REVIEW'


@pytest.mark.parametrize('damage', ['missing', 'hash', 'binding', 'facts', 'recovery', 'unknown_close',
                                  'missing_close', 'wrong_path', 'wrong_payload', 'reopened'])
def test_missing_or_changed_close_proof_cannot_authorize_cleanup(tmp_path, damage):
    journal, attempt, body, platform, specimen = prepared(tmp_path)
    if damage == 'missing':
        attempt.pop('w07_closed')
    elif damage in {'hash', 'binding'}:
        reference = attempt['w07_closed']
        path = journal.directory / reference['path']
        value = load(journal.directory, reference, 'w07-v2')
        value['binding']['owner_id'] = 'foreign'
        atomic_json(path, value)
        if damage == 'binding':
            reference['sha256'] = file_digest(path)
    elif damage == 'facts':
        body['facts']['receiver']['write_count'] = 2
    elif damage == 'recovery':
        body['recovery']['phase'] = 'FOREIGN'
    elif damage == 'reopened':
        body['closed'] = False
    else:
        intent = attempt['submission_intents']['w07-close-receiver']
        if damage == 'unknown_close':
            intent['status'] = 'UNKNOWN'
        elif damage == 'missing_close':
            attempt['submission_intents'].pop('w07-close-receiver')
        elif damage == 'wrong_path':
            intent['path'] += '/foreign'
        elif damage == 'wrong_payload':
            intent['payload_digest'] = digest({'foreign': True})
    with pytest.raises((KeyError, ValueError)):
        handler.cleanup(platform, journal, attempt, specimen)


def test_open_then_closed_cleanup_twice_has_only_one_close_write(tmp_path):
    journal, attempt, _, platform, specimen = prepared(tmp_path)
    body = load(journal.directory, attempt['w07_after'], 'w07-v2')['body']
    attempt.pop('w07_closed')
    attempt['submission_intents'].pop('w07-close-receiver')
    attempt['w07_deadline'] = '9999-01-01T00:00:00+00:00'
    calls = []

    def post(client, selected, path, payload, operation):
        calls.append((path, payload, operation))
        body['closed'] = True
        selected['submission_intents'][operation] = {
            'status': 'CONFIRMED', 'path': path, 'payload_digest': digest(payload), 'response': {}}

    platform.get = lambda path: copy.deepcopy(body)
    journal.post = post
    for _ in range(2):
        assert handler.cleanup(platform, journal, attempt, specimen) == 'CLEAN_RECEIVER_UNKNOWN_OPERATION_RETAINED'
    assert calls == [(handler.BASE + '/close', {}, 'w07-close-receiver')]
