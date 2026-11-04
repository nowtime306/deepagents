"""Private fixed scripts; callers still need genuine sealed models and resource scope."""

from langchain_core.messages import AIMessage, ToolMessage


def tool(name, arguments, identity):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": arguments, "id": identity}]
    )


def step(case_id, role, messages):
    """Return (message, token cost); a member timeout is deliberately unknown."""
    if case_id not in {"AET-W05", "AET-W06"} or role not in {"lead", "reviewer"}:
        raise ValueError("Unregistered team script")
    tools = [message for message in messages if isinstance(message, ToolMessage)]
    if role == "lead":
        if not tools:
            description = (
                "读取 /input.txt 并审阅"
                if case_id == "AET-W05"
                else "先读取 /input.txt，再查询星桥保修年限并保留真实检索结果"
            )
            return tool(
                "task",
                {"subagent_type": "reviewer", "description": description},
                "ae-team-delegate",
            ), 15
        if case_id == "AET-W05":
            # A genuine UNKNOWN delegation must prevent this lead continuation from being reached.
            raise RuntimeError(
                "Unsettled member result cannot become a lead completion"
            )
        # Four 15-token chat calls + query embedding7 + final15933 = 16000.
        return tool(
            "write_file",
            {"file_path": "/budget-after.txt", "content": "MUST_NOT_EXIST"},
            "ae-budget-denied-write",
        ), 15933
    if case_id == "AET-W05":
        if not tools:
            return tool("read_file", {"file_path": "/input.txt"}, "ae-member-read"), 15
        if tools[-1].tool_call_id != "ae-member-read" or "team-evidence" not in str(
            tools[-1].content
        ):
            raise ValueError("Member failure requires its actual fixed input read")
        raise TimeoutError(
            "Controlled member timeout after real input read; no provider retry"
        )
    if not tools:
        return tool("read_file", {"file_path": "/input.txt"}, "ae-member-read"), 15
    if len(tools) == 1:
        if tools[0].tool_call_id != "ae-member-read" or "bounded" not in str(
            tools[0].content
        ):
            raise ValueError(
                "The actual fixed input must be read before member retrieval"
            )
        return tool("search_knowledge", {"query": "星桥 保修"}, "ae-member-query"), 15
    if [message.tool_call_id for message in tools] != [
        "ae-member-read",
        "ae-member-query",
    ]:
        raise ValueError("Member query result does not belong to the emitted tool call")
    return AIMessage(content="查询已完成，保留真实检索和用量证据。"), 15


def native_usage(cost):
    if cost == 15933:
        return {"input_tokens": 15000, "output_tokens": 933, "total_tokens": 15933}
    if cost == 15:
        return {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    raise ValueError("Unregistered controlled token cost")
