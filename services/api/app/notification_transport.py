"""Bounded, pinned notification POSTs; never log secret webhook URLs."""

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.parse import quote, urlsplit


def endpoint(url: str, allow_private: bool) -> tuple:
    if len(url) > 4000 or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("通知地址格式无效")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("请填写不含账号和片段的 HTTP(S) 通知地址")
    if parsed.scheme != "https" and not allow_private:
        raise ValueError("公网通知必须使用 HTTPS")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port in {3100, 8100, 18180, 55432, 2375, 2376}:
        raise ValueError("禁止访问平台、数据库与管理服务端口")
    hostname = parsed.hostname.encode("idna").decode().rstrip(".")
    records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(item[4][0] for item in records))
    if not addresses:
        raise ValueError("通知地址无法解析")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
            raise ValueError("禁止访问元数据、保留或多播地址")
        if not address.is_global and not allow_private:
            raise ValueError("内网通知需管理员明确授权")
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        path += "?" + quote(parsed.query, safe="=&%/:?@!$'()*+,;-._~")
    return hostname, port, path, parsed.scheme == "https", addresses


def payload(kind: str, notice: dict, secret: str, attempt: str, *, timestamp: str | None = None) -> tuple:
    timestamp = timestamp or str(int(time.time()))
    # Metadata only. No user goal, model answer, credentials, attachments or mentions.
    title = notice["title"].replace("@", "＠").replace("<", "＜").replace(">", "＞")[:120]
    text = f"知行 · {title}\n{notice['message']}\n通知编号：{notice['id']}\n时间：{notice['created_at']}"
    headers = {"Content-Type": "application/json; charset=utf-8", "X-Agent-Delivery": attempt}
    if kind == "slack":
        body = {
            "text": text,
            "mrkdwn": False,
            "blocks": [{"type": "section", "text": {"type": "plain_text", "text": text}}],
        }
    elif kind == "feishu":
        body = {"msg_type": "text", "content": {"text": text}}
        if secret:
            body.update(
                timestamp=timestamp,
                sign=base64.b64encode(
                    hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest(),
                ).decode(),
            )
    elif kind == "wecom":
        body = {"msgtype": "text", "text": {"content": text, "mentioned_list": []}}
    else:
        body = {
            "id": str(notice["id"]),
            "title": title,
            "status": notice["message"],
            "time": str(notice["created_at"]),
            "target_kind": notice["target_kind"],
            "target_id": str(notice["target_id"]) if notice["target_id"] else None,
        }
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    if kind == "webhook":
        headers["X-Agent-Timestamp"] = timestamp
        headers["X-Agent-Signature"] = (
            "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + encoded, hashlib.sha256).hexdigest()
        )
    return encoded, headers


def accepted(kind: str, status: int, content: bytes) -> tuple[str, str | None]:
    if not 200 <= status < 300:
        return ("UNKNOWN" if status >= 500 else "FAILED"), "CHANNEL_HTTP_REJECTED"
    if kind == "webhook":
        return "SUCCEEDED", None
    if kind == "slack":
        return ("SUCCEEDED", None) if content.strip() == b"ok" else ("FAILED", "CHANNEL_RESPONSE_REJECTED")
    try:
        response = json.loads(content)
        success = response.get("code" if kind == "feishu" else "errcode") == 0
        return ("SUCCEEDED", None) if success else ("FAILED", "CHANNEL_RESPONSE_REJECTED")
    except (ValueError, AttributeError):
        return "UNKNOWN", "CHANNEL_RESPONSE_UNKNOWN"


def deliver(channel: dict, notice: dict, vault, attempt: str) -> tuple[str, str | None]:
    """Pin DNS and TLS hostname; a timeout after sending is never automatically replayed."""
    try:
        url = vault.decrypt(channel["endpoint_ciphertext"]).get_secret_value()
        hostname, port, path, tls, addresses = endpoint(url, channel["allow_private"])
        secret = vault.decrypt(channel["secret_ciphertext"]).get_secret_value()
        content, headers = payload(channel["kind"], notice, secret, attempt)
    except (ValueError, OSError):
        return "FAILED", "CHANNEL_ADDRESS_REJECTED"
    context = ssl.create_default_context()
    connection = (
        http.client.HTTPSConnection(hostname, port, timeout=10, context=context)
        if tls
        else http.client.HTTPConnection(hostname, port, timeout=10)
    )
    raw = None

    def abort():
        active = connection.sock
        if active:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active.close()

    deadline = threading.Timer(20, abort)
    deadline.daemon = True
    try:
        raw = socket.create_connection((addresses[0], port), timeout=10)
        connection.sock = context.wrap_socket(raw, server_hostname=hostname) if tls else raw
        raw = None
        deadline.start()
        connection.request(
            "POST", path, content, headers={**headers, "Connection": "close", "Accept-Encoding": "identity"}
        )
        response = connection.getresponse()
        body = response.read(32769)
        if len(body) > 32768 or response.getheader("Content-Encoding", "identity") not in {"", "identity"}:
            return "UNKNOWN", "CHANNEL_RESPONSE_TOO_LARGE"
        return accepted(channel["kind"], response.status, body)
    except (OSError, http.client.HTTPException, ValueError):
        return "UNKNOWN", "CHANNEL_DELIVERY_UNKNOWN"
    finally:
        deadline.cancel()
        if raw:
            raw.close()
        connection.close()
