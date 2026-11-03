"""RFC TOTP and signed OIDC validation using local keys only."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.errors import APIError
from app.identity_api import match_step, totp
from app.identity_oidc import verify_token


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_vectors_and_replay(seconds, expected):
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp(secret, seconds, 8) == expected
    assert match_step(secret, totp(secret, seconds), seconds // 30, seconds) is None


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    keys = {
        "keys": [
            {"kid": "local", "kty": "RSA", "n": encode(numbers.n.to_bytes(256)), "e": encode(numbers.e.to_bytes(3))}
        ]
    }

    def token(changes):
        claims = {
            "iss": "https://identity.test",
            "sub": "stable-subject",
            "aud": "client",
            "nonce": "nonce",
            "iat": 100,
            "exp": 200,
        }
        claims.update(changes)
        message = (
            encode(json.dumps({"alg": "RS256", "kid": "local"}).encode()) + "." + encode(json.dumps(claims).encode())
        )
        return message + "." + encode(key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256()))

    return token, keys


def test_real_signature_valid(signer):
    token, keys = signer
    assert (
        verify_token(token({}), keys, {"issuer": "https://identity.test", "client_id": "client"}, "nonce", 150)["sub"]
        == "stable-subject"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://evil.test"},
        {"aud": "other"},
        {"nonce": "wrong"},
        {"exp": 149},
        {"nbf": 300},
        {"iat": 300},
        {"sub": ""},
        {"aud": ["client", "other"]},
    ],
)
def test_signed_but_invalid_claims_rejected(signer, changes):
    token, keys = signer
    with pytest.raises(APIError) as error:
        verify_token(token(changes), keys, {"issuer": "https://identity.test", "client_id": "client"}, "nonce", 150)
    assert error.value.status == 401
