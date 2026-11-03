"""Controlled embedding provider boundary with genuine native metering and shared root caps."""

import json

from app.resource_runtime import current_scope
from langchain_core.messages import HumanMessage


def install_embeddings(app, state):
    import app.memory_vectors as memory
    from app import knowledge, model_api

    originals = (knowledge.embed_texts, memory.embed_texts, model_api.embed_texts, app.state.knowledge.embedder,
                 app.state.knowledge.reranker)

    async def controlled(config, texts, *, meter=None):
        scope = current_scope()
        if scope is None or meter is None:
            raise ValueError("Controlled embedding requires a genuine run and native meter")
        with state.lock:
            binding = state.models.get(config.id)
            if (not binding or binding["role"] != "embedding" or binding["owner_id"] != str(scope.owner)
                    or config.kind != "embedding" or config.protocol != "openai"
                    or config.model != f"eval::{binding['case_id']}::v1"):
                raise ValueError("Only sealed controlled embedding model IDs are allowed")
        if not isinstance(texts, list) or not 1 <= len(texts) <= 32 or not all(isinstance(t, str) and len(t) <= 64000 for t in texts):
            raise ValueError("Invalid controlled embedding batch")
        tokens = 7 * len(texts)
        key = state.reserve(binding["case_id"], "embedding", [HumanMessage(content=json.dumps(texts))], cost=tokens)
        receipt = None
        try:
            receipt = await meter.start(texts)
            if receipt is None:
                raise ValueError("Native embedding meter returned no receipt")
            vectors = []
            for text in texts:
                values = [float(word in text) for word in ("星桥", "保修", "发票", "木星")]
                vectors.append(values if any(values) else [0.0, 0.0, 0.0, 1.0])
            await meter.finish(receipt, {"prompt_tokens": tokens, "total_tokens": tokens})
            state.complete(key)
            return vectors
        except BaseException as error:
            state.mark_unknown(key)
            if receipt is not None:
                await meter.error(receipt, error)
            raise

    async def unavailable_reranker(*args, **kwargs):
        raise ValueError("Rerank suppliers are not enabled in this fixed engineering target")

    knowledge.embed_texts = memory.embed_texts = model_api.embed_texts = controlled
    app.state.knowledge.embedder = controlled
    app.state.knowledge.reranker = unavailable_reranker

    def restore():
        knowledge.embed_texts, memory.embed_texts, model_api.embed_texts, app.state.knowledge.embedder, app.state.knowledge.reranker = originals

    return restore
