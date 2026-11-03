"""Offline cache misses, verified local resources and image reserves without Base64 tokenizing."""

import base64
import hashlib
from types import SimpleNamespace

from app.local_tokenizer import RESOURCES, encoder
from app.token_estimates import IMAGE_RESERVE, content_tokens, text_tokens


def test_no_resource_or_invalid_hash_never_downloads(tmp_path, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Network forbidden")))
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    config = SimpleNamespace(provider="openai", protocol="openai", model="gpt-4")
    assert encoder(config) is None
    key = hashlib.sha1(b"https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken").hexdigest()
    (tmp_path / key).write_bytes(b"corrupt cache")
    assert encoder(config) is None
    assert text_tokens("你好", config) == 2
    assert (tmp_path / key).read_bytes() == b"corrupt cache"


def test_verified_local_byte_bpe_and_images_do_not_use_base64_length(tmp_path, monkeypatch):
    # A controlled 256-byte BPE isolates offline loading from downloading production vocabulary.
    raw = b"".join(base64.b64encode(bytes([i])) + b" " + str(i).encode() + b"\n" for i in range(256))
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setitem(RESOURCES, "cl100k_base", (digest, RESOURCES["cl100k_base"][1]))
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    key = hashlib.sha1(b"https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken").hexdigest()
    (tmp_path / key).write_bytes(raw)
    config = SimpleNamespace(provider="openai", protocol="openai", model="gpt-4")
    assert text_tokens("你好", config) == len("你好".encode())
    assert text_tokens("hello", config) == 5
    blocks = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * 100000}}]
    assert content_tokens(blocks, config) == IMAGE_RESERVE
    config.provider = "custom"
    assert encoder(config) is None and text_tokens("你好", config) == 2
