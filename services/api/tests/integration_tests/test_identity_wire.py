"""Actual local HTTP IdP and signed recovery receiver, no external service calls."""

import base64
import hashlib
import hmac
import json
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from test_api import ORIGIN
from test_api import client as client

from app.identity_api import router
from app.identity_delivery import deliver_one, stop_delivery
from app.identity_oidc import router as oidc_router
from app.identity_recovery import email_send
from app.identity_recovery import router as recovery_router


def encoded(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_actual_local_smtp_receiver():
    received = []

    class SMTP(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 local SMTP\r\n")
            self.wfile.flush()
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                verb = line.split(b" ", 1)[0].strip().upper()
                if verb == b"DATA":
                    self.wfile.write(b"354 data\r\n")
                    self.wfile.flush()
                    chunks = []
                    while (item := self.rfile.readline()) != b".\r\n":
                        if not item:
                            return
                        chunks.append(item)
                    received.append(b"".join(chunks))
                    self.wfile.write(b"250 saved\r\n")
                elif verb == b"QUIT":
                    self.wfile.write(b"221 bye\r\n")
                    self.wfile.flush()
                    break
                else:
                    self.wfile.write(b"250 ok\r\n")
                self.wfile.flush()

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        email_send(
            {"host": "127.0.0.1", "port": server.server_address[1], "sender": "sender@local.test", "starttls": False},
            "person@local.test",
            "local-verification-token",
            "verify",
        )
        assert len(received) == 1 and b"person@local.test" in received[0]
        from email import policy
        from email.parser import BytesParser

        assert "local-verification-token" in BytesParser(policy=policy.default).parsebytes(received[0]).get_content()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def local_server():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    state = {"messages": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(value).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            origin = state["origin"]
            if self.path.endswith("openid-configuration"):
                self.reply(
                    {
                        "issuer": origin,
                        "authorization_endpoint": origin + "/authorize",
                        "token_endpoint": origin + "/token",
                        "jwks_uri": origin + "/jwks",
                        "code_challenge_methods_supported": ["S256"],
                    }
                )
            else:
                self.reply(
                    {
                        "keys": [
                            {
                                "kid": "local",
                                "kty": "RSA",
                                "n": encoded(numbers.n.to_bytes(256)),
                                "e": encoded(numbers.e.to_bytes(3)),
                            }
                        ]
                    }
                )

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/sms":
                if state.get("sms_delay"):
                    time.sleep(state["sms_delay"])
                signature = hmac.new(
                    b"local-signature", self.headers["X-Identity-Timestamp"].encode() + b"." + raw, hashlib.sha256
                ).hexdigest()
                state["signature_valid"] = hmac.compare_digest(signature, self.headers["X-Identity-Signature"])
                state["messages"].append(json.loads(raw))
                if state.get("sms_fail"):
                    self.send_error(503)
                    return
                self.reply({"ok": True})
                return
            parameters = parse_qs(raw.decode())
            state["token_requested"] = True
            state["pkce_valid"] = (
                encoded(hashlib.sha256(parameters["code_verifier"][0].encode()).digest()) == state["challenge"]
            )
            claims = {
                "iss": state["origin"],
                "sub": "stable-subject",
                "aud": "local-client",
                "nonce": state["nonce"],
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "email": "same@local.test",
                "email_verified": True,
            }
            content = (
                encoded(json.dumps({"alg": "RS256", "kid": "local"}).encode())
                + "."
                + encoded(json.dumps(claims).encode())
            )
            self.reply(
                {"id_token": content + "." + encoded(key.sign(content.encode(), padding.PKCS1v15(), hashes.SHA256()))}
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def prepare(client):
    connection, app = client
    connection.portal.call(stop_delivery, app)
    app.include_router(router)
    app.include_router(oidc_router)
    app.include_router(recovery_router)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    return connection, app, owner


def test_local_signed_oidc_pkce_replay_identity_and_revocation(client, local_server):
    c, app, owner = prepare(client)
    state = local_server
    identifier = str(uuid4())
    body = {
        "name": "本机IdP",
        "issuer": state["origin"],
        "client_id": "local-client",
        "enabled": True,
        "allow_registration": True,
    }
    assert c.put(f"/api/v1/admin/identity/providers/{identifier}", headers=ORIGIN, json=body).status_code == 200
    result = c.post(f"/api/v1/auth/identity/{identifier}/begin", headers=ORIGIN, json={})
    assert result.status_code == 200
    query = parse_qs(urlsplit(result.json()["url"]).query)
    state.update(nonce=query["nonce"][0], challenge=query["code_challenge"][0])
    reply = c.get(
        "/api/v1/auth/identity/callback",
        params={"state": query["state"][0], "code": "local-only"},
        follow_redirects=False,
    )
    assert reply.status_code == 303 and state["pkce_valid"]
    user = c.get("/api/v1/auth/me").json()
    assert user["id"] != owner and user["is_admin"] is False
    assert c.post("/api/v1/auth/security/mfa/enroll", headers=ORIGIN, json={"password": ""}).status_code == 200
    assert (
        c.get("/api/v1/auth/identity/callback", params={"state": query["state"][0], "code": "local-only"}).status_code
        == 401
    )
    c.cookies.clear()
    c.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={
            "username": c.portal.call(app.state.db.fetch, "SELECT username FROM users WHERE id=%s", (owner,))[0][
                "username"
            ],
            "password": "integration-password",
        },
    )
    body["enabled"] = False
    assert c.put(f"/api/v1/admin/identity/providers/{identifier}", headers=ORIGIN, json=body).status_code == 200
    assert not c.portal.call(
        app.state.db.fetch, "SELECT 1 FROM identity_provider_sessions WHERE provider_id=%s", (identifier,)
    )


def test_signed_sms_contact_and_one_use_password_reset(client, local_server):
    c, app, owner = prepare(client)
    state = local_server
    body = {
        "name": "本机短信接收端",
        "enabled": True,
        "configuration": json.dumps({"url": state["origin"] + "/sms", "secret": "local-signature"}),
    }
    assert c.put("/api/v1/admin/identity/channels/sms", headers=ORIGIN, json=body).status_code == 200
    assert (
        c.post(
            "/api/v1/auth/security/contacts",
            headers=ORIGIN,
            json={"kind": "sms", "address": "+8613800000000", "password": "integration-password"},
        ).status_code
        == 200
    )
    token = state["messages"][-1]["token"]
    assert state["signature_valid"]
    assert c.post("/api/v1/auth/security/contacts/verify", headers=ORIGIN, json={"token": token}).status_code == 200
    # A replacement address is not usable until its own new proof has been received.
    assert (
        c.post(
            "/api/v1/auth/security/contacts",
            headers=ORIGIN,
            json={"kind": "sms", "address": "+8613900000000", "password": "integration-password"},
        ).status_code
        == 200
    )
    old = c.portal.call(app.state.db.fetch, "SELECT verified FROM identity_contacts WHERE owner_id=%s", (owner,))
    assert old[0]["verified"] is True
    username = c.get("/api/v1/auth/me").json()["username"]
    assert (
        c.post(
            "/api/v1/auth/identity/recovery/request", headers=ORIGIN, json={"username": username, "kind": "sms"}
        ).status_code
        == 200
    )
    c.portal.call(deliver_one, app)
    reset_token = state["messages"][-1]["token"]
    rows = c.portal.call(
        app.state.db.fetch, "SELECT token_hash FROM identity_recovery_tokens WHERE owner_id=%s", (owner,)
    )
    assert reset_token not in json.dumps(rows)
    request = {"token": reset_token, "password": "replacement-password-123"}
    assert c.post("/api/v1/auth/identity/recovery/reset", headers=ORIGIN, json=request).status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401
    assert c.post("/api/v1/auth/identity/recovery/reset", headers=ORIGIN, json=request).status_code == 401
    assert (
        c.post(
            "/api/v1/auth/login", headers=ORIGIN, json={"username": username, "password": request["password"]}
        ).status_code
        == 200
    )
