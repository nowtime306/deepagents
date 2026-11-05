"""Known timeout only for bounded, side-effect-free text operations."""

import asyncio
from collections.abc import Awaitable, Callable
from weakref import WeakKeyDictionary

from app.errors import APIError
from app.resource_runtime import current_scope

_expired = WeakKeyDictionary()


def confirmed_pure_timeout(error: Exception, node: object) -> bool:
    proof = _expired.get(error)
    return (
        isinstance(error, PureNodeTimeout)
        and proof is not None
        and proof[0] is node
        and proof[1].expired()
        and node.parameters.get("timeout_ms") == proof[2]
        and proof[3] is asyncio.current_task()
        and proof[4] is current_scope()
    )


class PureNodeTimeout(APIError):
    """The pure text executor exceeded its opt-in deadline; no external effect was dispatched."""

    def __init__(self):
        super().__init__(408, "WORKFLOW_PURE_TIMEOUT", "纯文本转换超过执行时限")


async def run_pure_transform(
    calculate: Callable[[], str],
    timeout_ms: int,
    *,
    controlled: Awaitable[str] | None = None,
    node: object | None = None,
) -> str:
    # Cancellation does not terminate a running Python thread. The default callable only
    # performs existing bounded local text operations and never dispatches external effects.
    operation = controlled if controlled is not None else asyncio.to_thread(calculate)
    deadline = asyncio.timeout(timeout_ms / 1000)
    try:
        async with deadline:
            return await operation
    except TimeoutError as error:
        if not deadline.expired():
            raise
        failure = PureNodeTimeout()
        _expired[failure] = (node, deadline, timeout_ms, asyncio.current_task(), current_scope())
        raise failure from error
