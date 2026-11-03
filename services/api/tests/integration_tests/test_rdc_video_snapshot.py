"""Author coverage of multi-frame model snapshots; decoding and provider are controlled doubles."""

from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client
from test_expansion_media_costs import prepare

from app.errors import APIError
from app.media_retrieval_video import directory, index_frame, process


def test_video_frames_freeze_actual_model_and_new_job_refreshes_but_disable_stops(client, monkeypatch):
    c, app, owner = prepare(client)
    model = 'video-snapshot-' + uuid4().hex
    c.portal.call(app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol,"
        "temperature,max_output_tokens) "
        "VALUES(%s,%s,'controlled-vector','https://old.fixture.invalid/v1',%s,"
        "'multimodal_embedding','dashscope_multimodal',.1,32)",
        (model, model, app.state.models.vault.encrypt('first-test-key')))
    observed = []
    jobs = []

    async def provider(config, content):
        observed.append((config.base_url, config.api_key.get_secret_value(), config.temperature,
                         config.max_output_tokens, config.revision))
        if len(observed) == 1:
            await app.state.db.execute(
                "UPDATE model_configs SET base_url='https://new.fixture.invalid/v1',api_key_ciphertext=%s,"
                "temperature=.9,max_output_tokens=99,revision=revision+1 WHERE id=%s",
                (app.state.models.vault.encrypt('second-test-key'), model))
        return [1.0, 0.0]

    async def frames_only(state, job):
        # Replace only decoder orchestration; process(), index_frame(), registry and ledger stay real.
        for number in range(3):
            path = directory(state.settings, job['id']) / f'controlled-frame-{number}.png'
            path.write_bytes(b'controlled-decoded-frame')
            await index_frame(state, job, path, number)
            if job['id'] == jobs[-1] and len(jobs) == 2 and number == 0:
                await state.db.execute('UPDATE model_configs SET enabled=false WHERE id=%s', (model,))

    monkeypatch.setattr('app.media_retrieval_video.vectorize', provider)
    monkeypatch.setattr('app.media_retrieval_video._process', frames_only)

    def job():
        # An unverified endpoint migration gets a fresh index, so no vectors are declared compatible by this test.
        kb = c.post('/api/v1/capabilities/knowledge', headers=ORIGIN, json={'name': uuid4().hex}).json()['id']
        configured = c.post(f'/api/v1/capabilities/knowledge/{kb}/image-index',
                            headers=ORIGIN, json={'model_id': model})
        assert configured.status_code == 201, configured.text
        identifier = uuid4()
        c.portal.call(app.state.db.execute,
            "INSERT INTO knowledge_video_jobs(id,knowledge_id,owner_id,model_id,model_revision,name,size,"
            "interval_seconds,max_frames,status,vector_fingerprint) "
            "SELECT %s,%s,%s,%s,m.revision,'controlled.mp4',1,1,3,'RUNNING',i.vector_fingerprint "
            "FROM knowledge_image_indexes i JOIN model_configs m ON m.id=i.model_id WHERE i.knowledge_id=%s",
            (identifier, kb, owner, model, kb))
        directory(app.state.settings, identifier).mkdir(parents=True)
        jobs.append(identifier)
        return c.portal.call(app.state.db.fetch, 'SELECT * FROM knowledge_video_jobs WHERE id=%s', (identifier,))[0]

    first = job()
    c.portal.call(process, app.state, first)
    assert observed == [('https://old.fixture.invalid/v1', 'first-test-key', .1, 32, 1)] * 3
    old = c.portal.call(app.state.db.fetch,
        'SELECT model_revision,resource_snapshot_id FROM model_usage WHERE run_id=%s', (first['id'],))
    assert len(old) == 3 and {row['model_revision'] for row in old} == {1}
    assert len({row['resource_snapshot_id'] for row in old}) == 1 and old[0]['resource_snapshot_id'] is not None

    following = job()
    with pytest.raises(APIError, match='停用'):
        c.portal.call(process, app.state, following)
    assert observed[-1] == ('https://new.fixture.invalid/v1', 'second-test-key', .9, 99, 2)
    assert len(observed) == 4  # Disabled before the next frame; no fifth provider call or ledger reservation.
    new = c.portal.call(app.state.db.fetch,
        'SELECT model_revision,resource_snapshot_id FROM model_usage WHERE run_id=%s', (following['id'],))
    assert len(new) == 1 and new[0]['model_revision'] == 2
    assert new[0]['resource_snapshot_id'] != old[0]['resource_snapshot_id']
    snapshots = c.portal.call(app.state.db.fetch,
        'SELECT snapshot FROM run_resource_snapshots WHERE run_id=ANY(%s)', ([first['id'], following['id']],))
    assert 'first-test-key' not in str(snapshots) and 'second-test-key' not in str(snapshots)
