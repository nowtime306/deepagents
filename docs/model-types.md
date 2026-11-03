# 多类型模型配置开发任务

用户于 2026-10-29 提供 Nexent 模型设置截图，要求支持不同类型的模型配置。当前平台只有 chat/embedding，本次在现有模型管理中扩展，保留现有数据与密钥。

## 范围与验证

1. 模型用途覆盖截图十类：大语言、向量、多模态向量、图片理解、图片生成、视频理解、音频理解、重排、语音识别、语音合成。数据库迁移、请求校验、列表显示、页面新增/编辑保持一致，并按类型筛选。验证各类型保存、刷新、编辑和旧记录兼容。
2. 服务商选择参考截图，区分服务商与接口协议。预置地址只采用官方文档核实的信息，自定义地址可编辑；不根据服务商标签默认为兼容协议。保留 API Key 服务端加密及留空保留语义。
3. 当前 Ask 只允许可调用的聊天模型，知识库仅选择适用向量模型；防止新类型混入错误用途。默认聊天模型规则保持明确。其他类型可以管理配置，实际业务功能与连接检测支持情况明确展示。
4. 检测按照真实协议处理；尚未实现或需要上传样本的类型明确说明，不发送聊天请求后误报连接正常。优先验证已有聊天和向量功能不回归，新增类型不得因配置保存成功就被标记为连接成功。
5. 完成针对性后端和实际浏览器验证、生产构建，应用迁移并重启本机服务，补充使用说明和开发记录。外部真实模型联调仍按用户要求跳过。

## 额度约束

2026-10-29 22:06（北京时间）额度已使用 91%，继续完整开发可能低于用户要求的 8% 保留线，因此本次尚未修改功能代码。额度重置时间为 2026-10-30 00:00:48（北京时间），安排 00:02 后在原线程自动续作；恢复前重新检查实际额度。已有 00:45 使用重置卡任务独立保留。

## 2026-10-30 实现结果

额度恢复后已在 `onwer_agent` 完成配置管理。管理员打开“设置 → 模型管理”，添加或编辑模型时可以选择截图对应的十类用途和十三个服务商选项，页面支持按类型、服务商和名称筛选。

服务商包括 SiliconFlow、阿里灵积 DashScope、TokenPony、OpenAI、火山引擎、ModelEngine、DeepSeek、智谱 Z.AI、Anthropic Claude、Google Gemini、Mistral AI、xAI 和自定义。选择服务商会填入常用地址及协议，地址始终可编辑。ModelEngine 的地址取决于实际部署，因此不预填；阿里/火山等默认地址是指定地区的常用接口，需按用户账号、地域与套餐调整。服务商选择不保证其提供全部十类模型。

接口协议与服务商分开保存：OpenAI 兼容、Anthropic Messages 和其他原生接口。Google Gemini 默认采用其官方 OpenAI 兼容入口；Claude 使用原生 Messages，而不是发送 OpenAI 请求。其他原生接口可先保存配置，页面明确标记业务调用与检测尚未接入。

### 当前调用与检测范围

- 大语言模型：OpenAI 兼容和 Anthropic 原生协议可用于现有智能对话、工具调用及连接检测；实际能力取决于所填模型。默认对话模型只能选择这些可调用配置。
- 文本向量模型：OpenAI 兼容 `/embeddings` 可用于知识库与连接检测。
- 多模态向量、图片理解、图片生成、视频理解、音频理解、重排、语音识别、语音合成：本次完成配置保存、编辑、启停、筛选和密钥管理；相应输入/输出、推理工作流及协议适配尚未交付，因此检测按钮明确显示“检测待接入”。保存配置不会标记连接正常。
- 其他原生协议不会出现在 Ask 或知识库的可执行模型选择器中；非聊天用途不能误用于对话，非文本向量用途不能误用于当前知识库。

### 数据与验证

迁移 `004_model_types.sql` 扩展用途约束，添加 `provider` 和 `protocol`，保留原模型 ID、名称、地址、用途和加密凭证。旧记录默认标记为自定义服务商/OpenAI 兼容接口，保持此前行为。模型类型、服务商、协议、启停与密钥仍保存在数据库，通过页面配置。

后端 **34 项测试通过**：新增十类记录创建/编辑往返、留空保留密钥、用途隔离、默认约束、未知分类拒绝及不支持检测不发请求/不误标成功。Anthropic 使用已安装的官方 SDK 与 `httpx2.MockTransport` 验证原生认证、Messages 地址和 SSE 解析；不接触外部服务。

浏览器 **5 项测试通过**：通过实际 Next.js/FastAPI/PostgreSQL 页面添加十类模型，刷新后筛选、编辑、验证凭证不回显/留空保留、地址预填和手机布局；同时保留原模型管理与能力中心回归。测试仅删除自身生成的记录。TypeScript、Ruff 和生产构建通过。真实外部模型联调仍按用户要求跳过。

### 接口来源

地址与协议核对日期：2026-10-30。这里只预置服务地址，不预置可能过期的具体模型 ID。

- [SiliconFlow 快速上手](https://api-docs.siliconflow.cn/docs/userguide/quickstart)、[TokenPony 快速开始](https://docs.tokenpony.cn/)。
- [阿里百炼 Base URL](https://help.aliyun.com/en/model-studio/base-url)、[火山方舟接口文档](https://docs.volcengine.com/docs/ark/context-management?lang=zh)。
- [DeepSeek 首次调用](https://api-docs.deepseek.com/quick_start/agent_integrations/)、[智谱 OpenAI 兼容说明](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)。
- [Claude API 概览](https://platform.claude.com/docs/en/api/overview)、[LangChain ChatAnthropic](https://docs.langchain.com/oss/python/integrations/chat/anthropic)、[Gemini OpenAI 兼容说明](https://ai.google.dev/gemini-api/docs/openai)。
- [Mistral 接口文档](https://docs.mistral.ai/api/endpoint/models)、[xAI 接口文档](https://docs.x.ai/developers/rest-api-reference/inference)。
