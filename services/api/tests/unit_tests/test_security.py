"""Password and token handling regression coverage."""

from app.security import hash_password, token_digest, verify_password


def test_password_salt_and_verification() -> None:
    encoded = hash_password("correct password")
    assert encoded != hash_password("correct password")
    assert verify_password("correct password", encoded)
    assert not verify_password("wrong password", encoded)
    assert "correct password" not in encoded


def test_tokens_are_stored_as_digests() -> None:
    assert len(token_digest("opaque-session")) == 64
    assert token_digest("opaque-session") == token_digest("opaque-session")
    assert token_digest("other-session") != token_digest("opaque-session")
