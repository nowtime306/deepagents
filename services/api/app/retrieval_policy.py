"""Freeze retrieval behavior per execution phase while keeping resource authority live."""

from fastapi.encoders import jsonable_encoder

from app.resource_runtime import current_scope, freeze_resource
from app.resource_tools import used

KNOWLEDGE_FIELDS = ("embedding_model_id", "rerank_model_id", "keyword_min_coverage",
                    "chunk_size", "chunk_overlap", "processing_revision")


async def knowledge_policy(database, vault, knowledge):
    """Keep model bindings and retrieval thresholds fixed independently of maintenance revisions."""
    values = {key: knowledge.get(key) for key in KNOWLEDGE_FIELDS}
    state = current_scope()
    summary = values.copy()
    if state and ("knowledge", str(knowledge["id"])) not in state.values:
        summary["index_compatibility"] = await database.fetch(
            "SELECT DISTINCT vector_fingerprint,vector_dimensions FROM documents WHERE knowledge_id=%s "
            "AND vector_status='READY' ORDER BY vector_fingerprint,vector_dimensions LIMIT 64", (knowledge["id"],))
    fixed = await freeze_resource(
        "knowledge", knowledge["id"], knowledge.get("configuration_revision", 1), values,
        vault=vault, snapshot=jsonable_encoder(summary))
    await used("knowledge", knowledge["id"])
    return {**knowledge, **fixed}


async def memory_policy(vault, owner, settings):
    """Keep the selected semantic model fixed, with enablement checked separately by the caller."""
    values = {"embedding_model_id": settings["embedding_model_id"], "enabled": settings["enabled"]}
    fixed = await freeze_resource("memory", owner, settings["revision"], values,
                                  vault=vault, snapshot=values)
    await used("memory", owner)
    return {**settings, **fixed}
