"""Observable Harness restrictions, loop behavior and legacy checkpoint compatibility."""

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from app.graphs import build_ask
from app.token_estimates import IMAGE_RESERVE, content_tokens, message_tokens


class ToolModel(FakeMessagesListChatModel):
    bound_tools: list[str] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = [
            item.name if hasattr(item, "name") else item.get("name", item.get("type", "")) for item in tools
        ]
        return self


async def test_ask_native_loop_has_no_shell_write_or_delegation():
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "unsafe", "name": "execute", "args": {"command": "whoami"}}]),
        AIMessage(content="权限不足，不执行命令"),
    ])
    graph = build_ask(model, InMemorySaver())
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="运行命令")]},
        {"configurable": {"thread_id": "ask-boundary"}},
    )
    assert {"task", "execute", "write_file", "edit_file", "delete"}.isdisjoint(model.bound_tools)
    assert "read_file" in model.bound_tools
    assert result["messages"][-2].type == "tool"
    assert "execute is not a valid tool" in result["messages"][-2].content
    assert result["messages"][-1].content == "权限不足，不执行命令"


async def test_harness_reads_legacy_ask_checkpoint():
    saver = InMemorySaver()
    model = ToolModel(responses=[AIMessage(content="第一轮"), AIMessage(content="第二轮")])
    config = {"configurable": {"thread_id": "legacy-ask"}}
    await create_agent(model, checkpointer=saver).ainvoke(
        {"messages": [HumanMessage(content="旧版问题")]}, config,
    )
    result = await build_ask(model, saver).ainvoke({"messages": [HumanMessage(content="新版问题")]}, config)
    assert [item.content for item in result["messages"] if item.type == "human"] == ["旧版问题", "新版问题"]


def test_estimates_do_not_treat_image_base64_as_text():
    short = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    large = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 500000}}]
    assert content_tokens(short) == content_tokens(large) == IMAGE_RESERVE
    assert message_tokens([[HumanMessage(content=large)]]) == IMAGE_RESERVE + 8


async def test_run_guard_stops_tool_after_model_returns():
    import pytest

    from app.harness import RunGuard

    checks = 0

    async def guard():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise PermissionError("任务已撤权")

    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "count", "name": "text_length", "args": {"text": "abc"}}]),
        AIMessage(content="不应继续"),
    ])
    graph = build_ask(model, InMemorySaver(), middleware=[RunGuard(guard)])
    with pytest.raises(PermissionError, match="任务已撤权"):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="统计字符")]},
            {"configurable": {"thread_id": "guarded-ask"}},
        )
    assert checks == 2
    assert model.i == 1
