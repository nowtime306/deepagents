"""Model purpose and provider metadata shared by validation and the settings UI."""

from typing import Literal

ModelKind = Literal[
    "chat",
    "embedding",
    "multimodal_embedding",
    "vision",
    "image_generation",
    "video",
    "audio",
    "rerank",
    "speech_to_text",
    "text_to_speech",
]
ModelProtocol = Literal["openai", "anthropic", "custom", "dashscope_multimodal"]
ModelProvider = Literal[
    "custom",
    "siliconflow",
    "dashscope",
    "tokenpony",
    "openai",
    "volcengine",
    "modelengine",
    "deepseek",
    "zai",
    "anthropic",
    "gemini",
    "mistral",
    "xai",
]

KINDS = [
    {"id": "chat", "name": "大语言模型", "hint": "用于智能对话和工具调用"},
    {"id": "embedding", "name": "向量模型", "hint": "用于知识库文本向量与混合检索"},
    {"id": "multimodal_embedding", "name": "多模态向量模型", "hint": "支持百炼原生多模态向量协议的图文检索"},
    {"id": "vision", "name": "图片理解模型", "hint": "支持图文聊天协议；请在智能对话上传图片或知识库OCR测试"},
    {"id": "image_generation", "name": "图片生成模型", "hint": "支持返回 PNG 的 base64 图片生成接口"},
    {"id": "video", "name": "视频理解模型", "hint": "支持百炼兼容视频输入；独立页面已隐藏，使用受鉴权API检测"},
    {"id": "audio", "name": "音频理解模型", "hint": "支持音频聊天协议；独立页面已隐藏，使用受鉴权API检测"},
    {"id": "rerank", "name": "重排模型", "hint": "用于知识库检索结果重排，需服务支持 /rerank 接口"},
    {
        "id": "speech_to_text",
        "name": "语音识别模型",
        "hint": "支持 /audio/transcriptions；独立页面已隐藏，使用受鉴权API上传样例检测",
    },
    {
        "id": "text_to_speech",
        "name": "语音合成模型",
        "hint": "支持 /audio/speech；独立页面已隐藏，使用受鉴权API和指定音色检测",
    },
]
PROTOCOLS = [
    {"id": "openai", "name": "OpenAI 兼容接口", "hint": "聊天使用 /chat/completions，文本向量使用 /embeddings"},
    {"id": "anthropic", "name": "Anthropic Messages", "hint": "使用原生 Messages 协议，支持大语言模型对话"},
    {"id": "dashscope_multimodal", "name": "百炼多模态向量", "hint": "百炼原生多模态向量接口，基础地址使用 /api/v1"},
    {"id": "custom", "name": "其他原生接口（仅保存配置）", "hint": "请填写服务商提供的地址，业务调用和连接检测待适配"},
]
PROVIDERS = [
    {"id": "custom", "name": "自定义", "base_url": "", "protocol": "openai"},
    {"id": "siliconflow", "name": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "protocol": "openai"},
    {
        "id": "dashscope",
        "name": "阿里灵积 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "protocol": "openai",
    },
    {"id": "tokenpony", "name": "TokenPony", "base_url": "https://api.tokenpony.cn/v1", "protocol": "openai"},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "protocol": "openai"},
    {
        "id": "volcengine",
        "name": "火山引擎",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "protocol": "openai",
    },
    {"id": "modelengine", "name": "ModelEngine", "base_url": "", "protocol": "custom"},
    {"id": "deepseek", "name": "DeepSeek 官方平台", "base_url": "https://api.deepseek.com", "protocol": "openai"},
    {"id": "zai", "name": "智谱 Z.AI", "base_url": "https://open.bigmodel.cn/api/paas/v4", "protocol": "openai"},
    {"id": "anthropic", "name": "Anthropic Claude", "base_url": "https://api.anthropic.com", "protocol": "anthropic"},
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "protocol": "openai",
    },
    {"id": "mistral", "name": "Mistral AI", "base_url": "https://api.mistral.ai/v1", "protocol": "openai"},
    {"id": "xai", "name": "xAI", "base_url": "https://api.x.ai/v1", "protocol": "openai"},
]


def supports_chat(kind: str, protocol: str) -> bool:
    """Expose only executable text chat configurations to Ask."""
    return kind == "chat" and protocol in {"openai", "anthropic"}


def supports_embedding(kind: str, protocol: str) -> bool:
    """Exclude multimodal or unadapted native protocols from text indexing."""
    return kind == "embedding" and protocol == "openai"


def probe_hint(kind: str, protocol: str) -> str | None:
    """Return an honest explanation when a configuration cannot be tested yet."""
    if (
        supports_chat(kind, protocol)
        or supports_embedding(kind, protocol)
        or (kind == "rerank" and protocol == "openai")
    ):
        return None
    if protocol == "custom":
        return "配置已保存；此原生接口尚未适配连接检测与业务调用"
    if protocol == "dashscope_multimodal":
        return "请在知识库上传图片并检索以测试多模态向量；模型管理暂不提供独立连接检测"
    return next(item["hint"] for item in KINDS if item["id"] == kind)
