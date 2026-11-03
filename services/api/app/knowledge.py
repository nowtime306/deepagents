"""Document parsing, lexical retrieval and optional persistent Qdrant hybrid search."""

import io
import math
import re
import zipfile
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from docx import Document
from pypdf import PdfReader
from qdrant_client import AsyncQdrantClient, models

from app.config import ModelConfig
from app.embedding_compatibility import compatible_vector, fingerprint
from app.errors import fail
from app.model_catalog import supports_embedding
from app.models import ModelRegistry
from app.resource_runtime import resource_run
from app.retrieval_evidence import coverage, guidance
from app.spreadsheet_text import spreadsheet_sections
from app.storage import Database

MAX_UPLOAD = 4 * 1024 * 1024


def collection_name(identifier: UUID, config: ModelConfig, *, dimensions: int | None = None) -> str:
    """Separate actual embedding contracts while retaining indexes through maintenance."""
    suffix = f"_d{dimensions}" if dimensions is not None else ""
    return f"kb_{identifier}_{fingerprint(config)[:24]}{suffix}"


def parse_document(name: str, data: bytes) -> list[tuple[str, str]]:
    """Extract bounded text while preserving pages or document section labels."""
    suffix = Path(name).suffix.lower()
    if suffix in {".txt", ".md"}:
        sections = [("全文", data.decode("utf-8-sig"))]
    elif suffix == ".xlsx":
        sections = spreadsheet_sections(data)
    elif suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 100:
            fail(422, "UNSUPPORTED_DOCUMENT", "PDF 不得加密，页数最多 100 页")
        sections = [(f"第 {index + 1} 页", page.extract_text() or "") for index, page in enumerate(reader.pages)]
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
                fail(413, "DOCUMENT_TOO_LARGE", "Word 文档解压后超过限制")
        document = Document(io.BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        text += "\n" + "\n".join(
            " | ".join(cell.text for cell in row.cells) for table in document.tables for row in table.rows
        )
        sections = [("文档正文", text)]
    else:
        fail(422, "UNSUPPORTED_DOCUMENT", "支持 TXT、Markdown、PDF、DOCX 和 XLSX 文件")
    if not any(text.strip() for _, text in sections):
        fail(422, "EMPTY_DOCUMENT", "未提取到文字，扫描 PDF 需先进行 OCR")
    if sum(len(text) for _, text in sections) > 200000:
        fail(413, "DOCUMENT_TOO_LARGE", "文字超过 20 万字，请拆分后上传")
    return sections


def split_sections(sections: list[tuple[str, str]], *, size: int = 800, overlap: int = 120) -> list[tuple[str, str]]:
    """Chunk within each source location with a small overlap."""
    return [
        (location, text[start : start + size])
        for location, text in sections
        for start in range(0, len(text.strip()), size - overlap)
        if text[start : start + size].strip()
    ]


def terms(text: str) -> list[str]:
    """Combine Latin words, CJK characters and bigrams for Chinese keyword matching."""
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", text.lower())
    return words + [
        words[index] + words[index + 1]
        for index in range(len(words) - 1)
        if len(words[index]) == len(words[index + 1]) == 1
    ]


def lexical_rank(query: str, rows: list[dict]) -> list[tuple[str, float]]:
    """Compute BM25-style scores without requiring an external embedding service."""
    tokens = [Counter(terms(row["content"])) for row in rows]
    average = sum(sum(item.values()) for item in tokens) / max(len(tokens), 1)
    results = []
    for row, frequencies in zip(rows, tokens, strict=True):
        score = 0.0
        for term in set(terms(query)):
            frequency = frequencies[term]
            if frequency:
                document_frequency = sum(term in item for item in tokens)
                weight = math.log(1 + (len(rows) - document_frequency + 0.5) / (document_frequency + 0.5))
                score += (
                    weight
                    * frequency
                    * 2.2
                    / (frequency + 1.2 * (0.25 + 0.75 * sum(frequencies.values()) / max(average, 1)))
                )
        if score:
            results.append((str(row["id"]), score))
    return sorted(results, key=lambda pair: pair[1], reverse=True)


async def embed_texts(config: ModelConfig, texts: list[str], *, meter=None) -> list[list[float]]:
    """Use a configured OpenAI-compatible embedding endpoint with bounded batches."""
    if not supports_embedding(config.kind, config.protocol):
        fail(422, "MODEL_KIND_INVALID", "知识库需要 OpenAI 兼容接口的文本向量模型")
    vectors = []
    headers = (
        {"Authorization": f"Bearer {config.api_key.get_secret_value()}"} if config.api_key.get_secret_value() else {}
    )
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for offset in range(0, len(texts), 32):
            batch = texts[offset : offset + 32]
            receipt = await meter.start(batch) if meter else None
            try:
                response = await client.post(
                    f"{config.base_url}/embeddings",
                    headers=headers,
                    json={"model": config.model, "input": batch},
                )
                response.raise_for_status()
                payload = response.json()
                if meter:
                    await meter.finish(receipt, payload.get("usage"))
                items = sorted(payload["data"], key=lambda item: item["index"])
                vectors.extend(item["embedding"] for item in items)
            except BaseException as error:
                if meter and receipt:
                    await meter.error(receipt, error)
                raise
    if len(vectors) != len(texts) or not vectors or not 1 <= len(vectors[0]) <= 8192:
        fail(502, "INVALID_EMBEDDING", "Embedding 服务返回的数据格式不正确")
    return vectors


async def rerank_texts(config: ModelConfig, query: str, documents: list[str], *, meter=None) -> list[tuple[int, float]]:
    """Rank candidate texts using a compatible /rerank endpoint."""
    if config.kind != "rerank" or config.protocol != "openai":
        fail(422, "MODEL_KIND_INVALID", "请选择兼容的重排模型")
    headers = (
        {"Authorization": f"Bearer {config.api_key.get_secret_value()}"} if config.api_key.get_secret_value() else {}
    )
    receipt = await meter.start([query, *documents]) if meter else None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(
                f"{config.base_url}/rerank",
                headers=headers,
                json={"model": config.model, "query": query, "documents": documents, "top_n": len(documents)},
            )
            response.raise_for_status()
            payload = response.json()
            if meter:
                await meter.finish(receipt, payload)
            results = payload["results"]
    except BaseException as error:
        if meter and receipt:
            await meter.error(receipt, error)
        raise
    ranked = [(item["index"], float(item["relevance_score"])) for item in results]
    if (
        len(ranked) != len(documents)
        or {index for index, _ in ranked} != set(range(len(documents)))
        or any(not math.isfinite(score) for _, score in ranked)
    ):
        fail(502, "INVALID_RERANK", "重排服务返回的数据格式不正确")
    return ranked


class KnowledgeService:
    """Own the local vector index; PostgreSQL remains authoritative for ownership and deletion."""

    def __init__(self, database: Database, registry: ModelRegistry, directory: Path) -> None:
        self.database, self.registry = database, registry
        self.vectors = AsyncQdrantClient(path=str(directory))
        self.embedder = embed_texts
        self.reranker = rerank_texts

    async def owned(self, identifier: UUID, owner_id: UUID) -> dict:
        """Authorize a knowledge base before reading, parsing or indexing documents."""
        rows = await self.database.fetch(
            "SELECT * FROM knowledge_bases WHERE id=%s AND owner_id=%s", (identifier, owner_id)
        )
        if not rows:
            fail(404, "NOT_FOUND", "知识库不存在或无权访问")
        return rows[0]

    async def upload(self, knowledge: dict, name: str, data: bytes) -> dict:
        """Parse and atomically save a current source revision, deduplicating repeated imports."""
        from app.knowledge_documents import save

        return await save(self, knowledge, name, data)

    async def index(
        self, knowledge: dict, document_id: UUID, chunks: list[tuple], *,
        revision: int | None = None, acknowledge_duplicate: bool = False,
    ) -> dict | None:
        async with resource_run(self.database, knowledge["owner_id"], "resource", uuid4()):
            from app.retrieval_policy import knowledge_policy

            live = await self.owned(knowledge["id"], knowledge["owner_id"])
            knowledge = await knowledge_policy(self.database, self.registry.vault, live)
            return await self._index(knowledge, document_id, chunks, revision=revision,
                                     acknowledge_duplicate=acknowledge_duplicate)

    async def _index(
        self,
        knowledge: dict,
        document_id: UUID,
        chunks: list[tuple],
        *,
        revision: int | None = None,
        acknowledge_duplicate: bool = False,
    ) -> dict | None:
        """Keep keyword search usable even when a remote embedding service is unavailable."""
        if not knowledge["embedding_model_id"]:
            return
        if revision is None:
            rows = await self.database.fetch("SELECT revision FROM documents WHERE id=%s", (document_id,))
            if not rows:
                return
            revision = rows[0]["revision"]
        from app.knowledge_usage import authority, context_for, finish, meter, must_stop
        from app.memory_vectors import valid_vectors

        previous = await self.database.fetch(
            "SELECT 1 FROM resource_operations r WHERE r.owner_id=%s AND r.source_id=%s "
            "AND r.source_revision=%s AND r.status='UNKNOWN' AND r.created_at>coalesce("
            "(SELECT max(s.created_at) FROM resource_operations s WHERE s.source_id=r.source_id "
            "AND s.source_revision=r.source_revision AND s.status='SUCCEEDED'),'-infinity'::timestamptz) LIMIT 1",
            (knowledge["owner_id"], document_id, revision),
        )
        if previous and not acknowledge_duplicate:
            fail(409, "INDEX_REVIEW_REQUIRED", "上次索引结果未知，请核对消耗并明确确认可能重复计费后再重建")
        context, own = None, False
        try:
            config = await self.registry.get(knowledge["embedding_model_id"])
            context, own = await context_for(
                self, knowledge, config, action="index", source=document_id, revision=revision
            )
            vectors = await self.embedder(
                config, [text for _, _, text in chunks], meter=meter(self, knowledge, config, context)
            )
            vectors = valid_vectors(vectors, len(chunks))
            await authority(self.database, knowledge["owner_id"], knowledge["id"], context, config)
            active = await self.database.fetch("SELECT revision FROM documents WHERE id=%s", (document_id,))
            if not active or active[0]["revision"] != revision:
                fail(409, "DOCUMENT_CONFLICT", "索引期间文档已更新，本次旧结果未启用")
            collection = collection_name(knowledge["id"], config, dimensions=len(vectors[0]))
            if not await self.vectors.collection_exists(collection):
                await self.vectors.create_collection(
                    collection,
                    vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE),
                )
            await self.vectors.upsert(
                collection,
                points=[
                    models.PointStruct(id=str(chunk[0]), vector=vector, payload={"document_id": str(document_id)})
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
            await self.database.execute(
                "UPDATE documents SET vector_status='READY',error=NULL,vector_model_id=%s,vector_model_revision=%s,"
                "vector_fingerprint=%s,vector_dimensions=%s "
                "WHERE id=%s AND revision=%s",
                (config.id, config.revision, fingerprint(config), len(vectors[0]), document_id, revision),
            )
            await finish(self, context, own)
            return {"operation_id": context["id"], "status": "SUCCEEDED"}
        except BaseException as error:
            await finish(self, context, own, error)
            await self.database.execute(
                "UPDATE documents SET vector_status='FAILED',error=%s WHERE id=%s AND revision=%s",
                ("向量索引失败，可继续关键词检索；检查 Embedding 配置后重新索引", document_id, revision),
            )
            if not isinstance(error, Exception) or must_stop(error):
                raise
            return {"operation_id": context["id"] if context else None, "status": "FAILED"}

    async def search(self, knowledge_id, owner_id, query, limit=5, *, usage_context=None):
        run_id = usage_context["id"] if usage_context else uuid4()
        mode = usage_context["mode"] if usage_context else "resource"
        async with resource_run(self.database, owner_id, mode, run_id):
            return await self._search(knowledge_id, owner_id, query, limit, usage_context=usage_context)

    async def _search(
        self, knowledge_id: UUID, owner_id: UUID, query: str, limit: int = 5, *, usage_context=None
    ) -> dict:
        """Return authorized source snippets and fuse lexical and dense ranking when available."""
        knowledge = await self.owned(knowledge_id, owner_id)
        from app.retrieval_policy import knowledge_policy

        knowledge = await knowledge_policy(self.database, self.registry.vault, knowledge)
        from app.knowledge_usage import authority, context_for, finish, meter, must_stop

        context, own, resource_error = usage_context, False, None
        rows = await self.database.fetch(
            "SELECT c.*,d.name,d.vector_status,d.vector_model_id,d.vector_model_revision,"
            "d.vector_fingerprint,d.vector_dimensions FROM document_chunks c "
            "JOIN documents d ON d.id=c.document_id WHERE d.knowledge_id=%s AND d.status='READY' "
            "AND c.document_revision=d.revision",
            (knowledge_id,),
        )
        candidates = lexical_rank(query, rows)
        by_id = {str(row["id"]): row for row in rows}
        minimum = knowledge["keyword_min_coverage"]
        lexical = [
            (identifier, score)
            for identifier, score in candidates
            if (overlap := coverage(query, by_id[identifier]["content"])) > 0 and overlap >= minimum
        ]
        scores = {identifier: 1 / (60 + rank) for rank, (identifier, _) in enumerate(lexical, 1)}
        lexical_scores = dict(lexical)
        vector_scores = {}
        mode, warning = "keyword", None
        if knowledge["embedding_model_id"] and any(row["vector_status"] == "READY" for row in rows):
            try:
                config = await self.registry.get(knowledge["embedding_model_id"])
                if not any(
                    compatible_vector(row, config)
                    for row in rows
                ):
                    fail(409, "KNOWLEDGE_INDEX_CHANGED",
                         "向量模型身份、维度或预处理缺少兼容证据；请核对模型版本及服务目标，或重新索引")
                context, created = await context_for(self, knowledge, config, context)
                own = own or created
                vector = (await self.embedder(config, [query], meter=meter(self, knowledge, config, context)))[0]
                await authority(self.database, owner_id, knowledge_id, context, config)
                response = await self.vectors.query_points(
                    collection_name(knowledge_id, config, dimensions=len(vector)),
                    query=vector, limit=20, score_threshold=0.2
                )
                eligible = {str(row["id"]) for row in rows if compatible_vector(row, config, len(vector))}
                for rank, point in enumerate(response.points, 1):
                    if str(point.id) not in eligible:
                        continue
                    scores[str(point.id)] = scores.get(str(point.id), 0) + 1 / (60 + rank)
                    vector_scores[str(point.id)] = point.score
                mode = "hybrid"
            except Exception as error:
                await finish(self, context, own, error)
                own = False
                if must_stop(error):
                    raise
                resource_error = error
                warning = "向量兼容性未确认或检索不可用，本次使用关键词检索；请核对模型版本、维度、服务目标与预处理"
        ranked = sorted(
            (row for row in rows if str(row["id"]) in scores), key=lambda row: scores[str(row["id"])], reverse=True
        )[:20]
        reranked = False
        if knowledge.get("rerank_model_id") and ranked:
            try:
                config = await self.registry.get(knowledge["rerank_model_id"])
                if resource_error:
                    fail(409, "RESOURCE_INTERRUPTED", "同次资源调用发生异常，本次不继续请求重排模型")
                context, created = await context_for(self, knowledge, config, context)
                own = own or created
                order = await self.reranker(
                    config,
                    query,
                    [row["content"] for row in ranked],
                    meter=meter(self, knowledge, config, context, kind="rerank"),
                )
                await authority(self.database, owner_id, knowledge_id, context, config, "rerank")
                scores.update({str(ranked[index]["id"]): score for index, score in order})
                ranked = [ranked[index] for index, _ in sorted(order, key=lambda pair: pair[1], reverse=True)]
                reranked = True
            except Exception as error:
                await finish(self, context, own, error)
                own = False
                if must_stop(error):
                    raise
                warning = (warning + "；" if warning else "") + "重排暂不可用，本次使用原始检索顺序"
        if context and not resource_error:
            await authority(self.database, owner_id, knowledge_id, context)
        await finish(self, context, own)
        live = await self.database.fetch(
            "SELECT c.id FROM document_chunks c JOIN documents d ON d.id=c.document_id "
            "JOIN knowledge_bases k ON k.id=d.knowledge_id WHERE k.id=%s AND k.owner_id=%s "
            "AND d.status='READY' AND d.revision=c.document_revision AND c.id=ANY(%s::uuid[])",
            (knowledge_id, owner_id, [row["id"] for row in ranked]),
        )
        identifiers = {row["id"] for row in live}
        ranked = [row for row in ranked if row["id"] in identifiers]
        ranked = ranked[:limit]
        return {
            "operation_id": context["id"] if context and context["mode"] == "resource" else None,
            "mode": mode,
            "reranked": reranked,
            "warning": warning,
            "evidence_status": "candidates" if ranked else "no_evidence",
            "guidance": guidance(bool(ranked)),
            "keyword_candidates": len(candidates),
            "keyword_filtered": len(candidates) - len(lexical),
            "keyword_min_coverage": minimum,
            "score_type": "rerank" if reranked else "rrf",
            "items": [
                {
                    "chunk_id": str(row["id"]),
                    "document_id": str(row["document_id"]),
                    "document_revision": row["document_revision"],
                    "name": row["name"],
                    "location": row["location"],
                    "text": row["content"],
                    "score": round(scores[str(row["id"])], 5),
                    "keyword_score": lexical_scores.get(str(row["id"])),
                    "keyword_coverage": round(coverage(query, row["content"]), 3),
                    "vector_score": vector_scores.get(str(row["id"])),
                }
                for row in ranked
            ],
        }
