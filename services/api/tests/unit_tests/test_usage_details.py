"""Provider details remain subsets and malformed or absent usage stays unknown."""

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.usage_details import reported_usage


def result(usage, **metadata):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="answer", usage_metadata=usage, response_metadata=metadata,
    ))]])


def test_details_are_not_added_to_totals():
    parsed = reported_usage(result({
        "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
        "input_token_details": {"cache_read": 40, "cache_creation": 0},
        "output_token_details": {"reasoning": 20},
    }))
    assert parsed == {
        "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
        "cache_read_tokens": 40, "cache_creation_tokens": 0, "reasoning_tokens": 20,
    }


def test_deepseek_raw_cache_fallback_and_missing_details():
    parsed = reported_usage(result(
        {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        token_usage={"prompt_cache_hit_tokens": 60},
    ))
    assert parsed["cache_read_tokens"] == 60
    assert parsed["cache_creation_tokens"] is None and parsed["reasoning_tokens"] is None
    assert parsed["total_tokens"] == 120


def test_invalid_totals_remain_unknown():
    assert reported_usage(result({"input_tokens": -1, "output_tokens": 20, "total_tokens": 19})) is None
    assert reported_usage(result({"input_tokens": 1, "output_tokens": 2, "total_tokens": 99})) is None
    assert reported_usage(result(None)) is None


def test_invalid_subset_does_not_corrupt_valid_totals():
    parsed = reported_usage(result({
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
        "input_token_details": {"cache_read": 11}, "output_token_details": {"reasoning": -1},
    }))
    assert parsed["total_tokens"] == 15
    assert parsed["cache_read_tokens"] is None and parsed["reasoning_tokens"] is None
