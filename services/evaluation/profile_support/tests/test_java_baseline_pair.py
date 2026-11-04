"""Author protocol/lifecycle substitutes only; no protected program execution."""
import asyncio
import base64
import json
import shlex
from types import SimpleNamespace

import evaluation_java_verifier as server
import pytest
import test_java_verifier as helpers

from evaluation_runner import fixed_java_client as client


def baseline():
    value = helpers.report(server.ORIGINAL_SHA)
    value['passed'] = False
    for index, actual in enumerate((2, -7, 4)):
        value['cases'][index]['stdout_sha256'] = client.sha(str(actual).encode())
        value['cases'][index]['passed'] = index == 2
    return value


def sdk(fixture, *, failure=None):
    phases = []

    async def run(command, *, opts):
        envelope = json.loads(base64.b64decode(shlex.split(command)[-1]))
        payload = json.loads(base64.b64decode(envelope['payload']))
        phase = envelope['phase']; phases.append(phase)
        assert opts.uid == opts.gid == 0
        assert base64.b64decode(envelope['program']) == server.PROGRAM
        assert base64.b64decode(envelope['launcher']) == server.LAUNCHER
        assert base64.b64decode(payload['source_base64']) == (server.ORIGINAL if phase == 'baseline' else fixture.content)
        if phase == 'candidate':
            saved = json.loads(fixture.service.path.read_text())['jobs'][str(fixture.run['task_id'])]
            assert saved['phase'] == 'BASELINE_RECORDED' and saved['baseline_report'] == baseline()
            if failure == 'candidate-transport':
                raise TimeoutError('Missing second execution acknowledgment')
        value = baseline() if phase == 'baseline' else helpers.report(fixture.args[-1])
        if phase == 'baseline' and failure == 'baseline-pass':
            value = helpers.report(server.ORIGINAL_SHA)
        if phase == 'baseline' and failure == 'baseline-compile':
            value['compile']['exit_code'] = 1
            value.pop('adapter_compile'); value.pop('class_protection'); value['cases'] = []
        if phase == 'baseline' and failure == 'lease':
            fixture.database.live = False
        return SimpleNamespace(error=None, exit_code=0, text=json.dumps(value))

    fixture.commands.run = run
    return phases


def test_actual_leased_consumer_runs_two_fixed_commands_in_same_second_sdk_slot(tmp_path):
    f = helpers.fixture(tmp_path); phases = sdk(f)

    async def scenario():
        await f.service.register(*f.args)
        result = await f.service.execute(f.run)
        assert result['baseline_verification'] == baseline()
        assert result['verification']['passed'] is True and phases == ['baseline', 'candidate']
        assert f.sandboxes.creates == f.sandboxes.releases == 1 and f.sandboxes.attempts == 2
        assert f.reload().jobs[str(f.run['task_id'])]['phase'] == 'COMPLETED'
        with pytest.raises(RuntimeError):
            await f.service.execute(f.run)
        assert len(phases) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['baseline-pass', 'baseline-compile', 'lease', 'candidate-transport'])
def test_unknown_or_invalid_baseline_cannot_continue_or_replay(tmp_path, failure):
    f = helpers.fixture(tmp_path); phases = sdk(f, failure=failure)

    async def scenario():
        await f.service.register(*f.args)
        with pytest.raises((RuntimeError, ValueError, TimeoutError)):
            await f.service.execute(f.run)
        assert phases == (['baseline', 'candidate'] if failure == 'candidate-transport' else ['baseline'])
        assert f.sandboxes.creates == f.sandboxes.releases == 1
        assert f.reload().jobs[str(f.run['task_id'])]['phase'] == 'UNKNOWN'
        with pytest.raises(RuntimeError):
            await f.service.execute(f.run)
    asyncio.run(scenario())


def test_failed_durable_baseline_save_never_dispatches_repaired_command(tmp_path, monkeypatch):
    f = helpers.fixture(tmp_path); phases = sdk(f)

    async def scenario():
        await f.service.register(*f.args)
        original = f.service.save

        def save():
            if f.service.jobs[str(f.run['task_id'])]['phase'] == 'BASELINE_RECORDED':
                raise OSError('Fixed baseline persistence failed')
            return original()

        monkeypatch.setattr(f.service, 'save', save)
        with pytest.raises(OSError):
            await f.service.execute(f.run)
        assert phases == ['baseline'] and f.sandboxes.releases == 1
        assert f.reload().jobs[str(f.run['task_id'])]['phase'] == 'UNKNOWN'
    asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['missing', 'all-pass', 'wrong-source', 'wrong-output', 'wrong-jdk', 'timeout'])
def test_server_and_offline_reader_both_reject_incomplete_negative_control(fault):
    original = baseline(); repaired = helpers.report('a' * 64)
    if fault == 'missing': original = None
    elif fault == 'all-pass': original = helpers.report(server.ORIGINAL_SHA)
    elif fault == 'wrong-source': original['source_sha256'] = 'b' * 64
    elif fault == 'wrong-output': original['cases'][0]['stdout_sha256'] = client.sha(b'other')
    elif fault == 'wrong-jdk': repaired['java_version_sha256'] = '0' * 64
    else: original['cases'][0]['timed_out'] = True
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        server.validate_pair(original, repaired, 'a' * 64)
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        client.baseline_protocol(original, repaired)


def test_protocol_accepts_real_original_failure_and_keeps_repaired_fail_as_fail():
    negative = baseline(); repaired = helpers.report('a' * 64)
    assert server.validate_pair(negative, repaired, 'a' * 64) is None
    assert client.baseline_protocol(negative, repaired) is True
    repaired['cases'][0]['stdout_sha256'] = client.sha(b'2')
    repaired['cases'][0]['passed'] = False; repaired['passed'] = False
    server.validate_report(repaired, 'a' * 64)
    assert client.protocol(repaired, 'a' * 64) == 'FAIL'


def test_partial_baseline_phase_is_unknown_after_reconstruction(tmp_path):
    f = helpers.fixture(tmp_path)
    asyncio.run(f.service.register(*f.args))
    f.service.jobs[str(f.run['task_id'])].update(phase='BASELINE_RECORDED', baseline_report=baseline())
    f.service.save()
    assert f.reload().jobs[str(f.run['task_id'])]['phase'] == 'UNKNOWN'


def test_final_protected_verdict_requires_both_report_bindings():
    candidate = {'root': {'run_id': 'source'}, 'artifact_id': 'artifact', 'source_sha256': 'a' * 64,
                 'original_sha256': server.ORIGINAL_SHA, 'diff_sha256': 'b' * 64, 'changed': True}
    result = {'case_id': 'AET-W02', 'verifier_run_id': 'verifier', 'source_run_id': 'source',
              'artifact_id': 'artifact', 'source_sha256': 'a' * 64,
              'baseline_verification': baseline(), 'verification': helpers.report('a' * 64)}
    verifier = {'run_id': 'verifier', 'result': result}
    assert client.protected_verdict(verifier, candidate) == 'PASS'
    del result['baseline_verification']
    with pytest.raises(ValueError): client.protected_verdict(verifier, candidate)


def test_fixed_phase_rejects_nonbaseline_content_before_any_effect(tmp_path):
    f = helpers.fixture(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(f.service.run_fixed(f.run, {}, None, b'other', 'baseline'))
    with pytest.raises(ValueError):
        asyncio.run(f.service.run_fixed(f.run, {}, None, b'other', '/tmp/arbitrary'))
    assert f.sandboxes.creates == 0


def test_changed_fixed_original_binding_blocks_before_sdk_creation(tmp_path):
    f = helpers.fixture(tmp_path)
    asyncio.run(f.service.register(*f.args))
    f.service.jobs[str(f.run['task_id'])]['baseline_source_sha256'] = '0' * 64
    with pytest.raises(RuntimeError): asyncio.run(f.service.execute(f.run))
    assert f.sandboxes.creates == 0


def test_old_single_report_target_is_blocked_by_readiness_before_submission():
    capabilities = {'java_verifier': {'protocol': 'java-max-v2', 'initialized': True,
        'program_sha256': server.PROGRAM_SHA, 'launcher_sha256': server.LAUNCHER_SHA,
        'tests_sha256': server.TESTS_SHA, 'observer': 'java-verifier-owned-v1'},
        'case_limits': {'schema_version': 1, 'enforcement': 'controlled-case-reserve-v1',
            'scope': 'evaluation-owner-and-case', 'profiles': {'AET-W02': {'max_model_calls': 6, 'max_tokens': 90, 'max_roots': 2}}}}
    specimen = SimpleNamespace(budgets=SimpleNamespace(max_model_calls=10, max_tokens=20000))
    with pytest.raises(RuntimeError): client.readiness(None, specimen, capabilities=capabilities)
    capabilities['java_verifier']['baseline_protocol'] = 'original-repair-v1'
    assert client.readiness(None, specimen, capabilities=capabilities) is None
