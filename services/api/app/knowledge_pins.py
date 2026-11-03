"""Comparable experiments pin source revisions, retrieval parameters and provider revisions."""

from app.errors import fail

FIELDS = ("embedding_model_id", "rerank_model_id", "keyword_min_coverage", "processing_revision")


async def snapshot(database, knowledge):
    documents = await database.fetch(
        "SELECT id,revision,source_digest,status,vector_status,vector_model_id,vector_model_revision "
        "FROM documents WHERE knowledge_id=%s ORDER BY id",
        (knowledge["id"],),
    )
    models = await database.fetch(
        "SELECT id,revision,enabled FROM model_configs WHERE id=ANY(%s::text[]) ORDER BY id",
        ([knowledge[key] for key in ("embedding_model_id", "rerank_model_id") if knowledge[key]],),
    )
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
                row["vector_model_revision"],
            ]
            for row in documents
        ],
        "models": [[row["id"], row["revision"], row["enabled"]] for row in models],
    }


async def guard(database, knowledge, expected):
    if await snapshot(database, knowledge) != expected:
        fail(409, "EVALUATION_SOURCE_CHANGED", "评测来源、索引状态、检索设置或模型修订已变化，已停止本实验")
