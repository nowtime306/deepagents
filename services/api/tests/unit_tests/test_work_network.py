"""Explicit domains cannot replace the fixed non-public deny floor."""

from unittest.mock import patch

import pytest

from app.work_network import DENIED, domain, policy


@pytest.mark.parametrize(
    "value", ["https://example.com", "*.example.com", "127.0.0.1", "a/b.example", "example.com:80", "localhost"]
)
def test_invalid_domain(value):
    with pytest.raises(ValueError):
        domain(value)


async def test_network_policy_is_default_deny_and_keeps_private_floor():
    with patch("app.work_network.public_addresses", return_value=["93.184.215.14"]):
        result = await policy(["Example.com"])
    assert result.default_action == "deny"
    assert {rule.target for rule in result.egress if rule.action == "deny"} == set(DENIED)
    assert [rule.target for rule in result.egress if rule.action == "allow"] == ["example.com"]
    with patch("app.work_network.public_addresses", side_effect=ValueError("Private target")):
        with pytest.raises(ValueError):
            await policy(["internal.example.com"])
