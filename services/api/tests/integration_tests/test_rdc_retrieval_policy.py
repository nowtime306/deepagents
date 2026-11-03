"""Retrieval policy stays fixed per phase; dimensions and authorization remain explicit."""

from uuid import UUID, uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client
from test_memory_vectors import embedding as embedding
from test_memory_vectors import memory

from app.memory_vectors import MemoryRecall
from app.resource_runtime import resource_run


@pytest.mark.parametrize('mode', ['ask', 'work', 'workflow'])
def test_knowledge_threshold_and_rerank_binding_are_fixed_but_new_run_refreshes(client, mode):
    c, app = client
    owner = UUID(c.get('/api/v1/auth/me').json()['id'])
    kb = c.post('/api/v1/capabilities/knowledge', headers=ORIGIN,
                json={'name': uuid4().hex}).json()['id']
    c.post(f'/api/v1/capabilities/knowledge/{kb}/documents', headers=ORIGIN,
           files={'file': ('words.txt', b'apple warranty')})
    rerank = 'rerank-policy-' + uuid4().hex
    c.portal.call(app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,kind) VALUES(%s,%s,'controlled','http://unused','rerank')",
        (rerank, rerank))
    reranked = []

    async def rank(config, query, documents, **kwargs):
        reranked.append(config.id)
        return [(i, 1.0) for i in range(len(documents))]

    app.state.knowledge.reranker = rank
    run_id = uuid4()

    async def scenario():
        async with resource_run(app.state.db, owner, mode, run_id):
            first = await app.state.knowledge.search(UUID(kb), owner, 'apple absent')
            await app.state.db.execute(
                'UPDATE knowledge_bases SET keyword_min_coverage=.9,rerank_model_id=%s WHERE id=%s', (rerank, kb))
            second = await app.state.knowledge.search(UUID(kb), owner, 'apple absent')
            assert first['items'] and second['items']
            assert first['keyword_min_coverage'] == second['keyword_min_coverage'] == 0
            assert not second['reranked'] and reranked == []
        async with resource_run(app.state.db, owner, mode, uuid4()):
            latest = await app.state.knowledge.search(UUID(kb), owner, 'apple absent')
            assert latest['keyword_min_coverage'] == .9 and latest['items'] == []
        await app.state.db.execute('UPDATE knowledge_bases SET keyword_min_coverage=0 WHERE id=%s', (kb,))
        # Resource mode owns the direct rerank operation; no fake parent-run lease is claimed.
        latest = await app.state.knowledge.search(UUID(kb), owner, 'apple absent')
        assert latest['reranked'] and reranked == [rerank]
        async with resource_run(app.state.db, owner, mode, run_id):
            await app.state.db.execute('DELETE FROM knowledge_bases WHERE id=%s', (kb,))
            from app.errors import APIError
            with pytest.raises(APIError, match='无权访问'):
                await app.state.knowledge.search(UUID(kb), owner, 'apple absent')

    c.portal.call(scenario)
    row = c.portal.call(app.state.db.fetch,
        "SELECT snapshot,revision FROM run_resource_snapshots WHERE run_id=%s AND resource_kind='knowledge'",
        (run_id,))[0]
    assert row['snapshot']['keyword_min_coverage'] == 0 and row['snapshot']['rerank_model_id'] is None
    assert row['revision'] == '1'


def test_memory_model_binding_stays_fixed_and_disable_is_immediate(embedding):
    c, app, model, _, _ = embedding
    owner = UUID(c.get('/api/v1/auth/me').json()['id'])
    next_model = 'memory-policy-' + uuid4().hex
    c.portal.call(app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,kind) VALUES(%s,%s,'other','http://unused','embedding')",
        (next_model, next_model))
    recall = MemoryRecall(app.state.db, app.state.models)
    run_id = uuid4()

    async def scenario():
        async with resource_run(app.state.db, owner, 'work', run_id):
            first, _ = await recall.config(owner)
            await app.state.db.execute(
                'UPDATE memory_settings SET embedding_model_id=%s,revision=revision+1 WHERE owner_id=%s',
                (next_model, owner))
            second, _ = await recall.config(owner)
            assert first.id == second.id == model
            await app.state.db.execute('UPDATE memory_settings SET enabled=false WHERE owner_id=%s', (owner,))
            disabled, _ = await recall.config(owner)
            assert disabled is None
        await app.state.db.execute('UPDATE memory_settings SET enabled=true WHERE owner_id=%s', (owner,))
        async with resource_run(app.state.db, owner, 'work', uuid4()):
            latest, _ = await recall.config(owner)
            assert latest.id == next_model

    c.portal.call(scenario)
    row = c.portal.call(app.state.db.fetch,
        "SELECT snapshot FROM run_resource_snapshots WHERE run_id=%s AND resource_kind='memory'", (run_id,))[0]
    assert row['snapshot']['embedding_model_id'] == model


def test_query_dimension_change_has_explicit_keyword_fallback(embedding, monkeypatch):
    c, _, _, _, _ = embedding
    memory(c, '偏好', '用户偏好中文')
    assert c.post('/api/v1/memories/index', headers=ORIGIN, json={}).json()['indexed'] == 1

    async def changed_dimension(config, texts, **kwargs):
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr('app.memory_vectors.embed_texts', changed_dimension)
    result = c.post('/api/v1/memories/recall', headers=ORIGIN, json={'query': '中文'})
    assert result.status_code == 200 and result.json()['mode'] == 'keyword', result.text
    assert '维度' in result.json()['warning'] and '重新索引' in result.json()['warning']
