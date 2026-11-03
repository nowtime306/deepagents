"""Public HTTP nodes cannot resolve or redirect into private networks."""

from types import SimpleNamespace

import pytest

from app.safe_http import destination, fetch_public_text, public_addresses


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "https://user:pass@example.org",
        "https://example.org:8080",
        "https://example.org/#fragment",
        "https://example.org/\r\nHeader:bad",
    ],
)
def test_unsafe_url_shapes_are_rejected(url):
    with pytest.raises(ValueError):
        destination(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "224.0.0.1"])
def test_private_addresses_are_rejected_before_connect(monkeypatch, address):
    monkeypatch.setattr("app.safe_http.socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, (address, 443))])
    with pytest.raises(ValueError):
        public_addresses("example.org")


@pytest.mark.parametrize("status,expected", [(200, "ok"), (302, None)])
def test_pinned_ip_original_tls_hostname_and_no_redirect(monkeypatch, status, expected):
    calls = []

    class Socket:
        def close(self):
            pass

        def shutdown(self, *args):
            pass

    class Context:
        def set_alpn_protocols(self, protocols):
            assert protocols == ["http/1.1"]

        def wrap_socket(self, socket, server_hostname):
            assert server_hostname == "example.org"
            return socket

    class Connection:
        sock = None

        def __init__(self, host, port, **kwargs):
            assert (host, port) == ("example.org", 443)

        def request(self, method, path, headers):
            calls.append((method, path, headers))

        def getresponse(self):
            return SimpleNamespace(status=status, getheader=lambda *a: "identity", read=lambda count: b"ok")

        def close(self):
            if self.sock:
                self.sock.close()

    def connect(address, **kwargs):
        assert address == ("8.8.8.8", 443)
        return Socket()

    monkeypatch.setattr(
        "app.safe_http.socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))]
    )
    monkeypatch.setattr("app.safe_http.socket.create_connection", connect)
    monkeypatch.setattr("app.safe_http.ssl.create_default_context", Context)
    monkeypatch.setattr("app.safe_http.http.client.HTTPSConnection", Connection)
    if expected:
        assert fetch_public_text("https://example.org/page?q=value") == expected
    else:
        with pytest.raises(ValueError, match="未跟随重定向"):
            fetch_public_text("https://example.org/page?q=value")
    assert len(calls) == 1 and calls[0][0] == "GET" and "Authorization" not in calls[0][2]
