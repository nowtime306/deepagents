"""Browser plans reject ambiguous operations, ungranted destinations and unsafe file paths."""

import pytest

from app.browser_tools import validate_steps


def test_browser_plan_validation():
    first = {"op": "open", "parameters": {"url": "https://example.com"}}
    validate_steps([first, {"op": "fill", "parameters": {"selector": "input", "text": "test"}}], ["example.com"])
    for bad in [
        [{"op": "click", "parameters": {"selector": "a"}}],
        [{"op": "open", "parameters": {"url": "http://example.com"}}],
        [{"op": "open", "parameters": {"url": "https://127.0.0.1"}}],
        [{"op": "open", "parameters": {"url": "https://example.com.evil.invalid"}}],
        [first, {"op": "screenshot", "parameters": {"path": "/../secret"}}],
        [first, {"op": "read", "parameters": {"selector": "body", "script": "arbitrary"}}],
        [first, {"op": "select_tab", "parameters": {"index": True}}],
        [first] * 13,
    ]:
        with pytest.raises(ValueError):
            validate_steps(bad, ["example.com"])
