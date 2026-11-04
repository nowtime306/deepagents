"""Fixed team generation after genuine sealed model factory and native callback admission."""

from evaluation_team_scripts import native_usage, step
from langchain_core.messages import ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def generate(model, messages):
    audit = getattr(model._state, "team_audit", None)
    if audit is None:
        raise RuntimeError(
            "Fixed team generation requires the installed durable tool observer"
        )
    audit.healthy()
    after_member = any(isinstance(message, ToolMessage) for message in messages)
    amount = (
        15933
        if model.case_id == "AET-W06" and model.role == "lead" and after_member
        else 15
    )
    key = model._state.reserve(model.case_id, model.role, messages, cost=amount)
    try:
        message, actual = step(model.case_id, model.role, messages)
        if actual != amount:
            raise ValueError("Controlled native token script changed")
        message.usage_metadata = native_usage(amount)
        model._state.complete(key)
        return ChatResult(generations=[ChatGeneration(message=message)])
    except BaseException:
        model._state.mark_unknown(key)
        raise
