"""Executable protocol boundaries, independent of provider availability or configured credentials."""

from app.model_catalog import KINDS, PROTOCOLS, PROVIDERS


def capability(kind: str, protocol: str, provider: str) -> dict:
    state, entry, probe = "unsupported", "此用途与协议组合尚未实现", False
    if protocol == "custom":
        state, entry = "configuration_only", "仅保存配置，尚无对应业务适配器"
    elif (kind == "chat" and protocol in {"openai", "anthropic"}) or (
        kind in {"embedding", "rerank"} and protocol == "openai"
    ):
        state, probe = "executable", True
        entry = "智能对话" if kind == "chat" else "知识库的文本入库/检索测试"
    elif kind == "multimodal_embedding" and protocol == "dashscope_multimodal":
        state, entry = "business_sample", "知识库 → 图片资料；需实际图片样例检测"
    elif protocol == "openai" and kind == "vision":
        state, entry = "business_sample", "智能对话上传图片，或知识库OCR；模型必须真实支持图片"
    elif protocol == "openai" and kind in {"audio", "speech_to_text", "text_to_speech", "image_generation"}:
        state, entry = "business_sample", "独立媒体页面已隐藏；使用相应受鉴权API做样例检测"
    elif protocol == "openai" and kind == "video" and provider == "dashscope":
        state, entry = "business_sample", "百炼兼容视频输入；独立页面已隐藏，使用受鉴权API"
    return {"kind": kind, "protocol": protocol, "provider": provider, "state": state, "probe_supported": probe,
            "entry": entry, "verification": "仅说明工程适配范围，真实服务权限/效果/价格尚需逐模型联调"}


def matrix() -> list[dict]:
    return [capability(kind["id"], protocol["id"], provider["id"])
            for provider in PROVIDERS for kind in KINDS for protocol in PROTOCOLS]
