"""Conservative lexical evidence gating, independent of dense and reranker scores."""

import re

# Split on query scaffolding rather than joining characters across removed words.
SCAFFOLD = re.compile(
    r"是多少|多少钱|是什么|什么时候|怎么办|为什么|怎么样|如何|是否|哪些|哪个|什么|多少|多久|几天|几年|几点|"
    r"可以|还能|需要|请问|一下|分别"
)
ENGLISH_STOP = {"a", "an", "the", "is", "are", "what", "how", "when", "of", "to", "in", "can", "i"}


def query_terms(query: str) -> set[str]:
    """Use exact Latin identifiers and contiguous Chinese bigrams, excluding question scaffolding."""
    normalized = SCAFFOLD.sub(" ", query.lower())
    result = set(re.findall(r"[a-z0-9_]+", normalized)) - ENGLISH_STOP
    for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
        result.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return result


def coverage(query: str, text: str) -> float:
    """Return lexical overlap, not an answer-confidence probability."""
    requested = query_terms(query)
    available = set(re.findall(r"[a-z0-9_]+", text.lower()))
    for segment in re.findall(r"[\u3400-\u9fff]+", text.lower()):
        available.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return len(requested & available) / len(requested) if requested else 0.0


def guidance(has_evidence: bool) -> str:
    """Explain retrieval limits to the calling agent without asserting model compliance."""
    if has_evidence:
        return (
            "以下仅为候选证据，并不保证足以回答。逐项核对问题、时间和适用范围；"
            "仅引用实际支持结论的片段 ID。资料不足请明确说明，不得编造引用。"
        )
    return (
        "本次没有通过筛选的知识库证据。请明确说明未找到资料，不得声称知识库支持答案或编造引用；"
        "可请求补充文档或改写问题。"
    )
