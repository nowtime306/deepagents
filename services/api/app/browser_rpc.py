"""Trusted browser program appended to the workspace RPC helpers inside the container."""

import base64
import json
import os
import resource
import sys
import tempfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def browse(root, body, read_file, write_file, max_file):
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    allowed = set(body["domains"])
    results = []

    def permitted(url):
        value = urlsplit(url)
        return (
            value.scheme == "https" and value.hostname in allowed and value.port in (None, 443) and not value.username
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disk-cache-size=1048576"],
        )
        try:
            context = browser.new_context(
                accept_downloads=True, service_workers="block", viewport={"width": 1280, "height": 720}
            )
            context.set_default_timeout(10000)
            context.route("**/*", lambda route: route.continue_() if permitted(route.request.url) else route.abort())
            context.route_web_socket("**", lambda websocket: websocket.close())
            page = context.new_page()
            for index, step in enumerate(body["steps"]):
                op, parameters = step["op"], step["parameters"]
                result = {"step": index + 1, "operation": op}
                try:
                    if op in {"open", "new_tab"}:
                        if not permitted(parameters["url"]):
                            raise ValueError("ungranted_url")
                        if op == "new_tab":
                            page = context.new_page()
                        page.goto(parameters["url"], wait_until="domcontentloaded", timeout=20000)
                    elif op == "select_tab":
                        page = context.pages[parameters["index"]]
                    elif op == "close_tab":
                        if len(context.pages) == 1:
                            raise ValueError("last_tab")
                        page.close()
                        page = context.pages[0]
                    elif op == "list_tabs":
                        result["tabs"] = [
                            {"index": i, "url": p.url, "title": p.title()} for i, p in enumerate(context.pages)
                        ]
                    elif op == "click":
                        page.locator(parameters["selector"]).click()
                    elif op == "fill":
                        page.locator(parameters["selector"]).fill(parameters["text"])
                    elif op == "scroll":
                        page.mouse.wheel(0, parameters["pixels"])
                    elif op == "read":
                        result["text"] = page.locator(parameters["selector"]).inner_text()[:12000]
                    elif op == "screenshot":
                        write_file(root, parameters["path"], page.screenshot(), False)
                        result["path"] = parameters["path"]
                    elif op == "upload":
                        content = read_file(root, parameters["path"])
                        page.locator(parameters["selector"]).set_input_files(
                            {
                                "name": PurePosixPath(parameters["path"]).name,
                                "mimeType": "application/octet-stream",
                                "buffer": content,
                            }
                        )
                    elif op == "download":
                        with page.expect_download(timeout=10000) as download:
                            page.locator(parameters["selector"]).click()
                        with tempfile.TemporaryDirectory() as directory:
                            target = os.path.join(directory, "download")
                            download.value.save_as(target)
                            with open(target, "rb") as source:
                                content = source.read(max_file + 1)
                            write_file(root, parameters["path"], content, False)
                        result["path"] = parameters["path"]
                    result.update({"url": page.url, "title": page.title()})
                    results.append(result)
                except Exception as error:
                    return {"ok": False, "steps": results, "failed_step": index + 1, "error": type(error).__name__}
            return {"ok": True, "steps": results}
        finally:
            browser.close()


if __name__ == "__main__":
    body = json.loads(base64.b64decode(sys.argv[1], validate=True))
    root = os.open("/workspace", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        print(
            json.dumps(
                browse(root, body, globals()["read"], globals()["write"], globals()["MAX_FILE"]), ensure_ascii=False
            )
        )
    finally:
        os.close(root)
