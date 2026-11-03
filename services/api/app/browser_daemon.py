"""Trusted, single-threaded browser service injected into a task's dedicated UID."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

PORT = 8917
MAX_FILE = 1024 * 1024
MAX_STATE = 2 * MAX_FILE
MAX_BODY = 3 * MAX_FILE
PRIVATE = Path("/var/lib/owner-agent-browser")


class Browser:
    """Keep the browser on the HTTP server's thread; no arbitrary code endpoint."""

    def __init__(self, playwright, domains: list[str]) -> None:
        self.domains = set(domains)
        self.browser = playwright.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disk-cache-size=1048576"],
        )
        self.context = None
        self.page = None
        self.calls = 0
        self.receipts = OrderedDict()
        self.restore({})

    def allowed(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                parsed.scheme == "https"
                and parsed.hostname in self.domains
                and parsed.port in (None, 443)
                and not parsed.username
            )
        except ValueError:
            return False

    def new_page(self, page) -> None:
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.on(
            "framenavigated",
            lambda frame: (
                page.close()
                if frame == page.main_frame
                and frame.url != "about:blank"
                and not self.allowed(frame.url)
                and not page.is_closed()
                else None
            ),
        )

    def active(self):
        if self.page is None or self.page.is_closed():
            self.page = next(iter(self.context.pages), None) or self.context.new_page()
        return self.page

    def restore(self, state: dict) -> dict:
        if self.context is not None:
            self.context.close()
        storage = state.get("storage", {"cookies": [], "origins": []})
        self.context = self.browser.new_context(
            accept_downloads=True,
            service_workers="block",
            viewport={"width": 1280, "height": 720},
            storage_state=storage,
        )
        self.context.set_default_timeout(10000)
        self.context.route(
            "**/*", lambda route: route.continue_() if self.allowed(route.request.url) else route.abort()
        )
        self.context.route_web_socket("**", lambda socket: socket.close())
        self.context.on("page", self.new_page)
        session = json.dumps(state.get("session", {}), ensure_ascii=True)
        self.context.add_init_script(
            "(() => { const states=" + session + "; const values=states[location.origin] || {}; "
            "for (const [key,value] of Object.entries(values)) sessionStorage.setItem(key,value); })();"
        )
        pages = state.get("pages", [])
        self.page = None
        for url in pages[:10]:
            if self.allowed(url):
                self.page = self.context.new_page()
                self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return self.status()

    def status(self) -> dict:
        page = self.active()
        return {
            "tabs": [
                {"index": index, "url": tab.url, "title": tab.title()} for index, tab in enumerate(self.context.pages)
            ],
            "active": self.context.pages.index(page),
            "url": page.url,
            "title": page.title(),
        }

    def export(self) -> dict:
        state = {"storage": self.context.storage_state(indexed_db=True), "session": {}, "pages": []}
        for page in self.context.pages:
            if self.allowed(page.url):
                parsed = urlsplit(page.url)
                origin = f"https://{parsed.hostname}" + (":443" if parsed.port else "")
                state["pages"].append(page.url)
                state["session"][origin] = page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))")
        if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_STATE:
            raise ValueError("browser_state_too_large")
        return state

    def operation(self, op: str, values: dict) -> dict:
        page = self.active()
        result = {}
        if op in {"open", "new_tab"}:
            if not self.allowed(values["url"]):
                raise ValueError("ungranted_url")
            if op == "new_tab":
                if len(self.context.pages) >= 10:
                    raise ValueError("tab_limit")
                page = self.context.new_page()
            self.page = page
            page.goto(values["url"], wait_until="domcontentloaded", timeout=20000)
        elif op == "select_tab":
            self.page = self.context.pages[values["index"]]
        elif op == "close_tab":
            if len(self.context.pages) == 1:
                raise ValueError("last_tab")
            page.close()
            self.page = self.context.pages[0]
        elif op == "list_tabs":
            return self.status()
        elif not self.allowed(page.url):
            raise ValueError("open_page_first")
        elif op == "click":
            page.locator(values["selector"]).click()
        elif op == "fill":
            page.locator(values["selector"]).fill(values["text"])
        elif op == "scroll":
            page.mouse.wheel(0, values["pixels"])
        elif op == "read":
            result["text"] = page.locator(values["selector"]).inner_text()[:12000]
        elif op == "screenshot":
            data = page.screenshot(type="png")
            if len(data) > MAX_FILE:
                raise ValueError("screenshot_too_large")
            result["data"] = base64.b64encode(data).decode()
        elif op == "upload":
            data = base64.b64decode(values["data"], validate=True)
            if len(data) > MAX_FILE or not re.fullmatch(r"[^/\\\x00]{1,180}", values["name"]):
                raise ValueError("upload_invalid")
            page.locator(values["selector"]).set_input_files(
                {"name": values["name"], "mimeType": "application/octet-stream", "buffer": data}
            )
        elif op == "download":
            with page.expect_download(timeout=10000) as download:
                page.locator(values["selector"]).click()
            with tempfile.TemporaryDirectory(dir=PRIVATE) as directory:
                target = Path(directory) / "download"
                download.value.save_as(target)
                with target.open("rb") as source:
                    data = source.read(MAX_FILE + 1)
                if len(data) > MAX_FILE:
                    raise ValueError("download_too_large")
                result["data"] = base64.b64encode(data).decode()
        elif op == "pointer":
            if any(
                type(values[key]) is not int or not 0 <= values[key] < bound for key, bound in [("x", 1280), ("y", 720)]
            ):
                raise ValueError("invalid_pointer")
            page.mouse.click(values["x"], values["y"])
        elif op == "type":
            if not isinstance(values["text"], str) or len(values["text"]) > 4000:
                raise ValueError("invalid_text")
            page.keyboard.insert_text(values["text"])
        elif op == "key":
            if values["key"] not in {
                "Enter",
                "Tab",
                "Escape",
                "Backspace",
                "Delete",
                "ArrowUp",
                "ArrowDown",
                "ArrowLeft",
                "ArrowRight",
                "Control+a",
            }:
                raise ValueError("invalid_key")
            page.keyboard.press(values["key"])
        else:
            raise ValueError("unsupported_operation")
        return {**result, **self.status()}

    def invoke(self, body: dict) -> dict:
        if self.calls >= 2000:
            raise ValueError("browser_operation_limit")
        self.calls += 1
        op = body["op"]
        if op == "export":
            return self.export()
        if op == "restore":
            return self.restore(body["state"])
        if op == "status":
            return self.status()
        identifier = body["request_id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9-]{1,80}", identifier):
            raise ValueError("invalid_request_id")
        fingerprint = hashlib.sha256(json.dumps([op, body.get("parameters", {})], sort_keys=True).encode()).hexdigest()
        if identifier in self.receipts:
            previous = self.receipts[identifier]
            if previous[0] != fingerprint:
                raise ValueError("request_conflict")
            return previous[1]
        # A started receipt remains uncertain if Playwright fails, so the same id never repeats an action.
        result = {"ok": False, "error": "operation_result_unknown"}
        self.receipts[identifier] = (fingerprint, result)
        try:
            result = {"ok": True, "result": self.operation(op, body.get("parameters", {}))}
        except Exception:
            result = {"ok": False, "error": "operation_result_unknown"}
        self.receipts[identifier] = (fingerprint, result)
        while len(self.receipts) > 32:
            self.receipts.popitem(last=False)
        return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def send(self, code: int, data: bytes, media: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        return hmac.compare_digest(self.headers.get("X-Owner-Browser-Key", ""), self.server.token)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/proof":
            nonce = parse_qs(parsed.query).get("nonce", [""])[0]
            if not re.fullmatch(r"[a-f0-9]{64}", nonce):
                self.send(400, b"{}")
                return
            proof = hmac.new(self.server.token.encode(), nonce.encode(), hashlib.sha256).hexdigest()
            self.send(200, json.dumps({"proof": proof}).encode())
        elif parsed.path == "/frame" and self.authorized():
            try:
                data = self.server.browser.active().screenshot(type="jpeg", quality=65)
                if len(data) > MAX_FILE:
                    raise ValueError("frame_too_large")
                self.send(200, data, "image/jpeg")
            except Exception:
                self.send(503, b"{}")
        else:
            self.send(403, b"{}")

    def do_POST(self) -> None:
        if self.path != "/rpc" or not self.authorized():
            self.send(403, b"{}")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Content-Type") != "application/json":
                raise ValueError("invalid_body")
            body = json.loads(self.rfile.read(length))
            result = self.server.browser.invoke(body)
            self.send(200, json.dumps(result, ensure_ascii=False).encode())
        except Exception:
            self.send(422, b'{"error":"browser_request_rejected"}')


def main() -> None:
    if os.getuid() != 1001 or os.getgid() != 1001:
        raise RuntimeError("Browser requires its dedicated account")
    os.umask(0o077)
    token = secrets.token_hex(32)
    (PRIVATE / "token").write_text(token)
    (PRIVATE / "pid").write_text(str(os.getpid()))
    domains = json.loads((PRIVATE / "domains.json").read_text())
    with sync_playwright() as playwright:
        browser = Browser(playwright, domains)
        try:
            with HTTPServer(("0.0.0.0", PORT), Handler) as server:
                server.token, server.browser = token, browser
                server.serve_forever(poll_interval=0.25)
        finally:
            browser.browser.close()


if __name__ == "__main__":
    main()
