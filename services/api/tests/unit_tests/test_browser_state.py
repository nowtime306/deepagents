"""Restoration only uses current domain grants and model results contain no private state."""

import pytest

from app.browser_state import filter_state, public_result
from app.errors import APIError


def test_domain_revocation_filters_cookies_origins_session_and_tabs():
    state = {
        "storage": {
            "cookies": [
                {"domain": ".example.com", "name": "parent", "value": "secret"},
                {"domain": "other.invalid", "name": "other", "value": "secret"},
            ],
            "origins": [
                {"origin": "https://www.example.com", "localStorage": []},
                {"origin": "https://other.invalid", "localStorage": []},
            ],
        },
        "session": {"https://www.example.com": {"token": "private"}, "https://other.invalid": {}},
        "pages": ["https://www.example.com/page?token=private", "https://other.invalid/page", "http://www.example.com"],
    }
    filtered = filter_state(state, ["www.example.com"])
    assert len(filtered["storage"]["cookies"]) == len(filtered["storage"]["origins"]) == 1
    assert set(filtered["session"]) == {"https://www.example.com"}
    assert filtered["pages"] == ["https://www.example.com/page?token=private"]
    assert filter_state(state, ["example.com.evil.invalid"])["storage"]["cookies"] == []
    assert state["storage"]["cookies"][1]["name"] == "other"


def test_private_state_size_and_model_result_redaction():
    with pytest.raises(APIError) as error:
        filter_state({"session": {"https://example.com": {"large": "x" * (2 * 1024 * 1024)}}}, ["example.com"])
    assert error.value.code == "BROWSER_STATE_LIMIT"
    result = public_result(
        {
            "url": "https://example.com/path?access=private#secret",
            "title": "title",
            "storage": {"cookie": "private"},
            "tabs": [{"index": 0, "url": "https://example.com?a=private", "title": "tab"}],
        }
    )
    assert result == {
        "url": "https://example.com/path",
        "title": "title",
        "tabs": [{"index": 0, "url": "https://example.com", "title": "tab"}],
    }
