import pytest

from evaluation_runner.contracts import Check
from evaluation_runner.facts import verify


@pytest.mark.parametrize("evidence", [
    {"run": {"status": "SUCCEEDED"}},
    {"root": {"mode": "ask"}, "observed": ["run"], "messages": {"items": [], "next_before": None}},
    {"root": {"mode": "work"}, "observed": ["messages"], "messages": {"items": [], "next_before": None}},
])
def test_missing_or_inapplicable_messages_cannot_pass_zero_count(evidence):
    result = verify(Check(registered_id="user_message_count", parameters={"expected": 0}), evidence, None)
    assert result.status == "BLOCKED"


def test_authoritative_complete_ask_messages_have_a_real_count():
    result = verify(Check(registered_id="user_message_count", parameters={"expected": 1}), {
        "root": {"mode": "ask"}, "observed": ["messages"],
        "messages": {"items": [{"role": "user", "content": {"text": "real input"}}], "next_before": None}}, None)
    assert result.status == "PASS"
