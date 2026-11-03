"""An authoritative empty aggregate is distinct from a model's unknown usage."""

import pytest

from app.usage_api import totals


def row(*, known=0, unknown=0, pending=0, incoming=None, outgoing=None):
    return {"calls": known + unknown + pending, "reported_calls": known,
            "unknown_calls": unknown, "pending_calls": pending,
            "input_tokens": incoming, "output_tokens": outgoing}


def test_complete_empty_aggregate_has_zero_tokens():
    assert totals([]) == {"calls": 0, "reported_calls": 0, "unknown_calls": 0, "pending_calls": 0,
                          "input_tokens": 0, "output_tokens": 0}


@pytest.mark.parametrize("values", [[row(unknown=1)], [row(pending=1)], [row(unknown=2, pending=1)]])
def test_existing_unreported_calls_remain_unknown(values):
    result = totals(values)
    assert result["calls"] > 0 and result["input_tokens"] is None and result["output_tokens"] is None
    assert result["unknown_calls"] + result["pending_calls"] == result["calls"]


def test_reported_zero_tokens_are_known_and_mixed_partial_statistics_remain_explicit():
    assert totals([row(known=1, incoming=0, outgoing=0)])["input_tokens"] == 0
    result = totals([row(known=1, incoming=10, outgoing=5), row(unknown=1)])
    assert result == {"calls": 2, "reported_calls": 1, "unknown_calls": 1, "pending_calls": 0,
                      "input_tokens": 10, "output_tokens": 5}


def test_absent_collection_is_not_silently_treated_as_empty():
    with pytest.raises(TypeError):
        totals(None)
