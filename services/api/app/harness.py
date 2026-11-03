"""One Deep Agents loop, with distinct platform mode policies and tool grants."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware


@dataclass(frozen=True)
class ModePolicy:
    name: Literal["ask", "work"]
    model_calls: int
    token_limit: int


ASK_POLICY = ModePolicy("ask", 12, 64000)
WORK_POLICY = ModePolicy("work", 40, 80000)


class NoDelegation(AgentMiddleware):
    """Replace the SDK delegation middleware rather than hiding it in a prompt."""

    @property
    def name(self) -> str:
        return "SubAgentMiddleware"


class RunGuard(AgentMiddleware):
    """Check current run authority at model and application tool boundaries."""

    def __init__(self, guard: Callable[[], Awaitable[None]]) -> None:
        self.guard = guard

    async def abefore_model(self, state, runtime):
        await self.guard()

    async def awrap_tool_call(self, request, handler):
        await self.guard()
        return await handler(request)


def build_harness(*, mode: Literal["ask", "work"], model, checkpointer, backend=None, middleware=(), **kwargs):
    """Share the native loop while keeping Ask tools and Work approvals separate."""
    configured = list(middleware)
    if mode == "ask":
        backend = backend or StateBackend()
        # Keep the existing Ask checkpoint message reducer; SDK 0.7 defaults to a
        # different delta channel. Existing conversation history remains readable.
        kwargs["state_schema"] = AgentState
        # User-bound SkillsMiddleware already supplies its own guarded, read-only
        # filesystem middleware; it replaces this empty virtual filesystem.
        configured = [
            FilesystemMiddleware(
                backend=backend, tools=["ls", "read_file", "glob", "grep"],
                tool_token_limit_before_evict=None, human_message_token_limit_before_evict=None,
            ),
            *([] if any(item.name == "SubAgentMiddleware" for item in configured) else [NoDelegation()]),
            ModelCallLimitMiddleware(run_limit=ASK_POLICY.model_calls, exit_behavior="error"),
            *configured,
        ]
    elif backend is None:
        raise ValueError("Work 需要受控任务工作区")
    return create_deep_agent(
        model=model, checkpointer=checkpointer, backend=backend,
        middleware=configured, **kwargs,
    )
