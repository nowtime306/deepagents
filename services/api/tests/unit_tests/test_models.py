"""Credential secrecy and endpoint validation independent of external providers."""

import pytest
from pydantic import ValidationError

from app.models import CredentialVault, ModelInput


def test_vault_persists_key_and_never_stores_plaintext(tmp_path) -> None:
    vault = CredentialVault(tmp_path / "model.key")
    encrypted = vault.encrypt("private-provider-key")
    assert "private-provider-key" not in encrypted
    restored = CredentialVault(tmp_path / "model.key")
    assert restored.decrypt(encrypted).get_secret_value() == "private-provider-key"


def test_missing_master_key_is_not_silently_replaced(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="主密钥缺失"):
        CredentialVault(tmp_path / "missing.key", create=False)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:secret@host/v1", "https://host/v1?key=secret"])
def test_model_endpoint_rejects_non_http_and_embedded_secrets(url) -> None:
    with pytest.raises(ValidationError):
        ModelInput(name="model", model="example", base_url=url)


def test_local_model_url_and_default_constraints() -> None:
    body = ModelInput(name="local", model="example", base_url="http://127.0.0.1:11434/v1/")
    assert body.base_url == "http://127.0.0.1:11434/v1"
    with pytest.raises(ValidationError):
        ModelInput(name="model", model="example", base_url=body.base_url, enabled=False, is_default=True)
