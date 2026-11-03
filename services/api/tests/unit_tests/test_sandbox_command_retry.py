"""The installed sandbox SDK must never resend an uncertain command SSE POST."""

from http import HTTPStatus

import httpx
import pytest
from opensandbox.adapters.command_adapter import CommandsAdapter
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException
from opensandbox.models.sandboxes import SandboxEndpoint
from opensandbox.transport import RetryAsyncTransport, RetryPolicy


@pytest.mark.parametrize("failure", ["503", "lost_response"])
async def test_sdk_command_post_does_not_retry_even_with_non_idempotent_status_opt_in(failure):
    calls = []

    async def respond(request):
        calls.append(request.method)
        if failure == "lost_response":
            raise httpx.ReadError("Controlled response lost after dispatch", request=request)
        return httpx.Response(503, json={"code": "CONTROLLED_UNKNOWN", "message": "result unknown"})

    transport = RetryAsyncTransport(httpx.MockTransport(respond), RetryPolicy(
        retryable_status_codes_non_idempotent=frozenset({HTTPStatus.SERVICE_UNAVAILABLE})
    ))
    adapter = CommandsAdapter(ConnectionConfig(transport=transport), SandboxEndpoint(endpoint="sandbox.invalid"))
    try:
        with pytest.raises(SandboxException):
            await adapter.run("echo controlled-fixture")
        assert calls == ["POST"], "A lost or failed command response must not dispatch the script again"
    finally:
        await adapter._sse_client.aclose()
        await adapter._httpx_client.aclose()
        await transport.aclose()
