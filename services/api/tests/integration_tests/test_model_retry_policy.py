"""An unknown paid request must never be retried invisibly inside a provider SDK."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr

from app.config import ModelConfig
from app.graphs import resolve_model


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_unknown_model_request_is_sent_once(protocol):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            body = b'{"error":{"type":"api_error","message":"local unknown result"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = resolve_model(
            ModelConfig(
                id="local-retry",
                name="本机重试策略验收",
                model="local",
                protocol=protocol,
                api_key=SecretStr("local-only"),
                max_output_tokens=128,
                base_url=f"http://127.0.0.1:{server.server_port}" + ("/v1" if protocol == "openai" else ""),
            )
        )
        with pytest.raises(Exception) as error:
            await model.ainvoke([HumanMessage(content="本机未知结果验收")])
        assert getattr(error.value, "status_code", None) == 503
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
