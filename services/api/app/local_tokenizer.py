"""Read hash-verified tiktoken resources already on disk; never download on a request."""

import base64
import hashlib
import os
import tempfile
from functools import lru_cache
from pathlib import Path

import tiktoken
from tiktoken.model import encoding_name_for_model

# Patterns and resource hashes match the installed tiktoken 0.14 OpenAI constructors.
RESOURCES = {
    "cl100k_base": (
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
        r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|"
        r"\s++$|\s*[\r\n]|\s+(?!\S)|\s",
    ),
    "o200k_base": (
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
        "|".join(
            [
                r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
                r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
                r"\p{N}{1,3}",
                r" ?[^\s\p{L}\p{N}]+[\r\n/]*",
                r"\s*[\r\n]+",
                r"\s+(?!\S)",
                r"\s+",
            ]
        ),
    ),
}


@lru_cache(maxsize=4)
def load(name: str, path: Path, size: int, modified: int):
    digest, pattern = RESOURCES[name]
    if size > 20 * 1024 * 1024:
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        return None
    ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in raw.splitlines() if line)}
    return tiktoken.Encoding(name="platform-local-" + name, pat_str=pattern, mergeable_ranks=ranks, special_tokens={})


def encoder(config):
    if config.provider != "openai" or config.protocol != "openai":
        return None
    try:
        name = encoding_name_for_model(config.model)
    except KeyError:
        return None
    if name not in RESOURCES:
        return None
    cache = os.getenv(
        "TIKTOKEN_CACHE_DIR", os.getenv("DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache"))
    )
    if not cache:
        return None
    url = "https://openaipublic.blob.core.windows.net/encodings/" + name + ".tiktoken"
    path = Path(cache) / hashlib.sha1(url.encode()).hexdigest()
    try:
        stat = path.stat()
        return load(name, path, stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError):
        return None
