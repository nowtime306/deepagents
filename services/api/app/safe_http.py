"""Public HTTPS GET with pinned DNS results, certificate checks and bounded bodies."""

import http.client
import ipaddress
import socket
import ssl
import threading
from urllib.parse import quote, urlsplit

MAX_HTTP_BODY = 1024 * 1024


def destination(url: str) -> tuple[str, str]:
    if len(url) > 2000 or any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("HTTP 地址无效")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("HTTP 节点只接受不含凭证和片段的固定 HTTPS 地址")
    if parsed.port not in (None, 443):
        raise ValueError("HTTP 节点仅允许 HTTPS 443 端口")
    hostname = parsed.hostname.encode("idna").decode().rstrip(".")
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        path += "?" + quote(parsed.query, safe="=&%/:?@!$'()*+,;-._~")
    return hostname, path


def public_addresses(hostname: str) -> list[str]:
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(
        not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast
        for address in addresses
    ):
        raise ValueError("HTTP 目标必须解析到公网地址，禁止访问本机、内网和元数据服务")
    return addresses


def fetch_public_text(url: str) -> str:
    """Never re-resolve the approved address when opening the actual TLS connection."""
    hostname, path = destination(url)
    addresses = public_addresses(hostname)
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    connection = http.client.HTTPSConnection(hostname, 443, timeout=10, context=context)
    raw = None
    expired = threading.Event()

    def abort():
        expired.set()
        active = connection.sock
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active.close()

    deadline = threading.Timer(20, abort)
    deadline.daemon = True
    try:
        raw = socket.create_connection((addresses[0], 443), timeout=10)
        connection.sock = context.wrap_socket(raw, server_hostname=hostname)
        raw = None
        deadline.start()
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/json,text/plain,text/html",
                "Accept-Encoding": "identity",
                "User-Agent": "OwnerAgentWorkflow/1.0",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise ValueError("HTTP 请求失败或返回跳转，未跟随重定向")
        if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
            raise ValueError("HTTP 节点不接受压缩响应")
        content = response.read(MAX_HTTP_BODY + 1)
        if expired.is_set():
            raise TimeoutError("HTTP 请求超过执行时间上限")
        if len(content) > MAX_HTTP_BODY:
            raise ValueError("HTTP 响应超过 1 MB")
        return content.decode("utf-8", "replace")
    finally:
        deadline.cancel()
        if raw:
            raw.close()
        connection.close()
