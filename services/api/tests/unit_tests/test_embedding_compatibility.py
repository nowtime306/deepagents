from app.config import ModelConfig
from app.embedding_compatibility import compatible_vector, fingerprint


def config(**changes):
    return ModelConfig(**({'id': 'e', 'name': 'embed', 'model': 'actual-v1',
                           'base_url': 'https://embed.example/v1', 'kind': 'embedding'} | changes))


def test_maintenance_preserves_vector_identity():
    old = config()
    new = config(name='renamed', revision=9, api_key='rotated')
    assert fingerprint(old) == fingerprint(new)
    assert compatible_vector({'vector_fingerprint': fingerprint(old), 'vector_dimensions': 3}, new, 3)


def test_same_dimensions_different_models_never_mix():
    stored = {'vector_fingerprint': fingerprint(config()), 'vector_dimensions': 3}
    assert not compatible_vector(stored, config(model='different-v1'), 3)
    assert not compatible_vector(stored, config(), 4)


def test_legacy_without_evidence_is_unknown():
    assert not compatible_vector({'vector_model_id': 'e', 'vector_model_revision': 1}, config(), 3)


def test_endpoint_change_needs_evidence():
    assert fingerprint(config()) != fingerprint(config(base_url='https://new.example/v1'))
    assert fingerprint(config(temperature=1, max_output_tokens=100)) == fingerprint(config())


async def test_confirmed_endpoint_migration_reuses_identity():
    from app.embedding_compatibility import contract_key, resolve_identity

    class Evidence:
        async def fetch(self, query, params):
            assert params == ("e", contract_key(config(base_url="https://new.example/v1")))
            return [{"source_key": contract_key(config())}]

    moved = await resolve_identity(Evidence(), config(base_url="https://new.example/v1"))
    assert fingerprint(moved) == fingerprint(config())
    assert fingerprint(moved, preprocessing="media-v1") != fingerprint(config())


async def test_gate_effect_snapshot_ignores_maintenance_but_detects_model_behavior():
    from app.knowledge_pins import snapshot
    from app.quality_gate import DEFAULTS, verdict
    from tests.unit_tests.test_quality_gate import evidence

    class Source:
        model = config().model_dump() | {"enabled": True}

        async def fetch(self, query, params=()):
            if "FROM model_configs" in query:
                return [self.model.copy()]
            return []

    db = Source()
    knowledge = {"id": "kb", "embedding_model_id": "e", "rerank_model_id": None,
                 "keyword_min_coverage": 0.2, "processing_revision": 1}
    original = await snapshot(db, knowledge)
    row, labels = evidence()
    db.model.update(name="renamed", revision=9, api_key="secret-rotation", is_default=True)
    assert verdict(DEFAULTS, row, labels, original == await snapshot(db, knowledge))["passed"]
    db.model["model"] = "same-dimension-other-model"
    assert not verdict(DEFAULTS, row, labels, original == await snapshot(db, knowledge))["passed"]
    assert row["status"] == "SUCCEEDED" and len(row["results"]) == 50
