"""Record safe context composition and stop repeated identical tool results."""

import hashlib
import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.errors import fail
from app.token_estimates import content_tokens


class AskObserver(AgentMiddleware):
    def __init__(self, emit) -> None:
        self.emit = emit
        self.completed: list[tuple[str, str]] = []
        self.compaction = None

    async def abefore_model(self, state, runtime):
        messages = state.get("messages", [])
        composition = {role: 0 for role in ["system", "human", "ai", "tool"]}
        for message in messages:
            role = getattr(message, "type", "other")
            if role in composition:
                composition[role] += content_tokens(message.content)
        await self.emit(
            "context.observed",
            {
                "message_count": len(messages),
                "estimated_tokens": composition,
                "note": "图状态消息的粗估，不含供应商内部展开和隐藏推理",
            },
        )
        summary = state.get("_summarization_event")
        if summary:
            marker = (summary.get("cutoff_index"), summary.get("created_at_index"))
            if marker != self.compaction:
                self.compaction = marker
                await self.emit(
                    "context.compacted",
                    {
                        "cutoff_index": marker[0],
                        "created_at_index": marker[1],
                        "summary_tokens_estimate": content_tokens(summary["summary_message"].content),
                    },
                )

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        key = hashlib.sha256(json.dumps([call["name"], call["args"]], sort_keys=True, default=str).encode()).hexdigest()
        previous = self.completed
        if len(previous) == 2 and previous[0][0] == previous[1][0] == key and previous[0][1] == previous[1][1]:
            await self.emit("tool.repetition_stopped", {"name": call["name"], "identical_results": 2})
            fail(422, "ASK_NO_PROGRESS", "相同工具调用连续得到相同结果，已停止重复执行；请调整问题或补充资料")
        result = await handler(request)
        if isinstance(result, ToolMessage):
            fingerprint = hashlib.sha256(str(result.content).encode()).hexdigest()
            self.completed = [*self.completed, (key, fingerprint)][-2:]
        return result
