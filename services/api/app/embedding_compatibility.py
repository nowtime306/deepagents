"""Vector identity excludes display and credential maintenance, never model semantics."""

import hashlib
import json


def contract_key(config):
    """Identify the provider request independently of local preprocessing."""
    def value(key):
        return config.get(key) if isinstance(config, dict) else getattr(config, key)
    identity = {key: value(key) for key in ("model", "base_url", "kind", "protocol")}
    identity["base_url"] = identity["base_url"].rstrip("/")
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def compatible_vector(row, config, dimensions=None, *, preprocessing="text-v1"):
    """Missing historical evidence is unknown, never an inferred compatibility claim."""
    return (row.get("vector_fingerprint") == fingerprint(config, preprocessing=preprocessing)
            and row.get("vector_dimensions") is not None
            and (dimensions is None or row["vector_dimensions"] == dimensions))


def fingerprint(config, *, preprocessing="text-v1"):
    """Use an attested provider identity and an explicit preprocessing contract."""
    key = (config.get("embedding_compatibility_key") if isinstance(config, dict)
           else config.embedding_compatibility_key)
    return hashlib.sha256(f"{key or contract_key(config)}:{preprocessing}".encode()).hexdigest()


async def resolve_identity(database, config):
    """Reuse a prior contract only after an explicit administrator attestation."""
    if config.kind not in {"embedding", "multimodal_embedding"}:
        return config
    rows = await database.fetch(
        "SELECT source_key FROM embedding_compatibility_evidence WHERE model_id=%s AND target_key=%s",
        (config.id, contract_key(config)),
    )
    return config.model_copy(update={"embedding_compatibility_key": rows[0]["source_key"] if rows else None})
