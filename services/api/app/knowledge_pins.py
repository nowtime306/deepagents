"""Comparable experiments pin source data, retrieval behavior and vector compatibility."""

from app.embedding_compatibility import fingerprint
from app.errors import fail

FIELDS = ("embedding_model_id", "rerank_model_id", "keyword_min_coverage", "processing_revision")


async def snapshot(database, knowledge):
    documents = await database.fetch(
        "SELECT id,revision,source_digest,status,vector_status,vector_model_id,vector_model_revision,"
        "vector_fingerprint,vector_dimensions "
        "FROM documents WHERE knowledge_id=%s ORDER BY id",
        (knowledge["id"],),
    )
    models = await database.fetch(
        "SELECT id,revision,enabled,model,base_url,kind,protocol FROM model_configs "
        "WHERE id=ANY(%s::text[]) ORDER BY id",
        ([knowledge[key] for key in ("embedding_model_id", "rerank_model_id") if knowledge[key]],),
    )
    for model in models:
        from app.embedding_compatibility import contract_key

        evidence = await database.fetch(
            "SELECT source_key FROM embedding_compatibility_evidence WHERE model_id=%s AND target_key=%s",
            (model["id"], contract_key(model)))
        model["embedding_compatibility_key"] = evidence[0]["source_key"] if evidence else None
    return {
        "knowledge": {key: knowledge[key] for key in FIELDS},
        "documents": [
            [
                str(row["id"]),
                row["revision"],
                row["source_digest"],
                row["status"],
                row["vector_status"],
                row["vector_model_id"],
                row.get("vector_fingerprint"),
                row.get("vector_dimensions"),
            ]
            for row in documents
        ],
        "schema": 2,
        "models": [[row["id"], fingerprint(row), row["enabled"]] for row in models],
    }


async def guard(database, knowledge, expected):
    if await snapshot(database, knowledge) != expected:
        fail(409, "EVALUATION_SOURCE_CHANGED", "评测来源、索引状态、检索设置或模型行为或兼容信息已变化，已停止本实验")
