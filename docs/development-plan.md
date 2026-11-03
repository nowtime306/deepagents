# AI Agent 平台开发实施文档

版本：1.0  
编写日期：2026-10-29  
项目：`D:\project\my_agent\deepagents`  
开发分支：`onwer_agent`（基于本地 `main`）  
技术架构：LangGraph + Deep Agents + Agent Chat UI + OpenSandbox  
界面语言：简体中文  
文档状态：保留2026-10-29的原开发基线。后续实现与当前验收以 [最新交付记录](final-delivery-2026-11-01.md) 和 [八阶段迭代计划](agent-platform-roadmap.md) 为准；以下早期调整按日期保留。

### 2026-10-29 实施调整：页面化模型管理

按用户最新要求，参考本机 Nexent 的模型添加/编辑、连接检测和默认模型交互，提前实现中文“设置 → 模型管理”。模型显示名、服务地址、实际模型 ID、调用参数、启停和默认选择保存在 PostgreSQL；API 密钥加密保存且不回显。新增/编辑立即生效，不再从 `MODEL_CONFIGS` 或模型密钥环境变量读取模型配置。

管理写操作仅管理员可用，普通用户可选择启用模型聊天。保留本机文件中的平台加密主密钥用于解密数据库凭证，该文件不是模型配置，需随数据库一起备份。首批适配 OpenAI 兼容接口，不在没有实际适配器时展示其他协议已可用。真实模型联调按用户要求先跳过，不阻塞其他开发任务。

## 1. 文档依据与交付目标

本文件根据用户提供的完整《AI Agent 平台完整二次开发方案》重新编写，作为后续编码、联调和验收的依据。完整原文保存在 [development-source.md](development-source.md)，与用户附件逐字节一致；原方案中的界面复制残留不作为实现要求。

整体目标是建设一个本地优先、可自托管的通用 Agent 平台，支持三种模式：

- **智能对话（Ask）**：多模型聊天、会话历史、附件、工具调用、知识检索与来源引用。
- **自主任务（Work）**：理解目标、生成计划、调用专业子 Agent、操作代码和浏览器、处理文件、验证并交付成果。
- **固定工作流（Workflow）**：通过中文画布编辑流程、调试节点、发布不可变版本，并向第三方提供受控 API。

优先完成 P0–P4 的 Work MVP，再完成 P5–P7 的能力中心和工作流平台。首版不建设计费系统、模板市场、Kubernetes 集群、原生 A2A 或完整远程桌面。

### 1.1 项目现状与实施边界

编写时已确认：

- 当前目录是 Deep Agents 官方源码仓库，核心 SDK 位于 `libs/deepagents/`，其声明版本为 `0.7.32`。
- `onwer_agent` 已从本地 `main` 创建；分支起点为 `89dc4ebc4523b543fd5aef1fd4f034f6802947d6`。
- Agent Chat UI 已下载到临时参考目录 `.upstream-agent-chat-ui/`，参考提交为 `2624b821a4542e115a7e8eb8917e4cfb42397ce6`；尚未整合为平台前端。
- 平台应用、数据库迁移、执行适配器和中文 UI 尚未实现，不能将已有 SDK 能力记为平台验收通过。
- 本机还运行着其他项目的数据库等容器。后续 Compose 使用独立项目名、数据卷和端口，不复用其他项目凭证或数据。

本项目通过新增应用目录扩展官方 SDK。未经具体任务需要，不修改 `libs/` 的框架内部代码，也不改动同级 Agno、Agent UI、Nexent 项目。

### 1.2 本文确定的工程决策

以下是为落地原方案补充的设计决策，属于实施约定：

1. Ask、Work、Workflow 各自维护独立 LangGraph 图入口，共享基础设施。
2. 前端沿用 Agent Chat UI 的交互组件，平台请求统一接入 FastAPI；不承诺任意 FastAPI 接口兼容 LangGraph SDK。
3. 先用官方 LangGraph 开发服务建立协议基准，再改造前端 Stream Provider 接入平台协议。开发服务仅用于本机联调，不成为公开鉴权旁路。
4. P0 即建立可信用户身份、资源归属、数据库迁移和 PostgreSQL Checkpointer；P7 再增加完整组织与外部 API Key 管理。
5. P3 引入独立 Worker 和数据库任务认领；P4 完成崩溃恢复、幂等、审批及资源回收。
6. 复用 Deep Agents 的 Todo、工具循环、子 Agent 和 Skills；平台同步计划，不再额外生成第二套计划。
7. P3 允许服务端配置一个受控 MCP 和一个本地 Skill，用于验证接入；P5 完成中文管理、版本与授权。
8. 原方案同时将基础 RAG 列为 MVP 必须项、又安排在 P5。本实施文档将 P5 拆为先行 P5a 基础检索、后续 P5b 能力管理：P0–P4 交付 Work MVP；需要宣称具备完整基础知识能力时，必须再通过 P5a 验收。

## 2. 架构与工程组织

### 2.1 服务职责

- **Web / Next.js**：中文交互、消息渲染、任务视图、工作流画布；不保存模型或沙箱管理密钥。
- **Platform API / FastAPI**：身份认证、权限、输入校验、业务资源、运行创建、SSE、审批和成果下载。
- **Agent Runtime / LangGraph**：构建并运行三类执行图，接入 Checkpointer；短任务可在 API 进程执行，长任务由 Worker 执行同一套运行模块。
- **Worker**：认领持久任务、续租、推进运行、处理取消、同步事件、导出成果和清理沙箱。
- **OpenSandbox Server**：管理沙箱生命周期和执行环境；与平台业务进程分离。
- **PostgreSQL**：业务表、事件、运行租约、授权和图检查点。业务表通过迁移管理，检查点表由对应组件管理。
- **Qdrant**：P5a 启用，保存知识检索索引；文档归属和授权仍以 PostgreSQL 为准。
- **文件存储**：首版使用平台专属目录，后续按需要切换 MinIO。沙箱临时文件不能直接充当持久成果。

Redis 暂不作为必要依赖；后续需要缓存或高吞吐队列时再评估。引入队列不能省略持久任务记录和运行恢复协议。

### 2.2 目录规划

以下为目标结构，按阶段创建，不在 P0 预先生成所有空模块：

```text
deepagents/
├── libs/                         # 原有官方 SDK，保持现有组织
├── apps/
│   └── web/
│       └── src/
│           ├── app/
│           ├── components/
│           ├── features/
│           │   ├── ask/
│           │   ├── work/
│           │   ├── workflows/
│           │   └── admin/
│           ├── lib/api/
│           └── locales/zh-CN/
├── services/
│   └── api/
│       ├── app/
│       │   ├── api/v1/
│       │   ├── agents/
│       │   ├── graphs/
│       │   ├── runtime/
│       │   ├── sandbox/
│       │   ├── models/
│       │   ├── security/
│       │   ├── storage/
│       │   ├── knowledge/
│       │   ├── tools/
│       │   ├── skills/
│       │   └── workflows/
│       ├── migrations/
│       ├── tests/unit_tests/
│       ├── tests/integration_tests/
│       ├── pyproject.toml
│       └── uv.lock
├── infra/
│   ├── compose/
│   ├── sandbox-images/
│   └── config/
├── skills/
├── scripts/
└── docs/
    ├── development-source.md
    └── development-plan.md
```

前端通过保留 LICENSE 和来源记录的源码导入整合 Agent Chat UI，不在主仓库内保留嵌套 Git 仓库作为正式应用。记录导入提交，后续按需合并上游变更。

### 2.3 依赖基线

- 后端优先采用 Python 3.12；以各包实际 `requires-python` 为准，使用 `uv` 和锁文件。
- 新 API 包通过本地 editable 依赖复用 `libs/deepagents`，不重复安装另一套行为不同的核心 SDK。
- 前端继承选定 Agent Chat UI 提交的 Next.js、React、SDK 版本及 pnpm 锁文件，不凭记忆重新指定版本。
- FastAPI、数据库驱动、迁移工具、OpenSandbox SDK、Qdrant 客户端和 React Flow 按引入阶段安装、锁定并说明用途。
- 安装前验证版本兼容性；官方示例只作为参考，不把本文中的平台接口误当成第三方 SDK 方法。
- MCP 接入方式以所锁定 LangChain 版本的真实接口为准；原文提到的具体 Beta 类名需验证后使用。

## 3. 中文 UI 与交互规范

### 3.1 中文化范围

一级导航使用“智能对话、任务工作台、工作流、能力中心、运行记录、设置”。模式首次展示时可括注 Ask、Work、Workflow，普通按钮使用中文。

中文覆盖导航、表单、占位提示、空状态、加载状态、成功提示、校验错误、权限错误、弹窗、工具提示、无障碍标签、日期和文件操作。图标按钮必须有中文标签。

状态统一显示为“待执行、运行中、等待审批、已暂停、已完成、执行失败、已取消”。API 枚举继续使用英文，展示层集中映射到 `zh-CN` 资源。

模型标识、代码、文件路径和原始终端输出保留原文；在外围提供中文解释。后端错误使用稳定错误码和中文默认文案，不将堆栈或凭证返回给页面。

### 3.2 页面功能

**智能对话**：左侧会话列表，中间聊天，顶部模型和 Agent 选择；支持新建、重命名、删除、附件、停止生成、Markdown、代码复制、工具详情和引用来源。

**任务工作台**：左侧计划与步骤，中间对话和执行时间线，右侧文件、差异、终端、浏览器、成果物。审批显示操作目标、参数、影响范围及批准、拒绝、修改参数入口。

**工作流**：节点库、拖拽画布、节点配置、运行调试、发布版本。保存草稿和发布是两个独立操作。

**能力中心**：模型、Agent、知识库、MCP、Skills、记忆；仅展示已实现能力，尚未开放的功能明确标注状态。

移动端采用抽屉或标签页切换三栏内容；桌面端优先验证 1440×900，同时检查 1280 宽度和 390 宽度下的主要操作。

## 4. 统一领域模型与持久化

### 4.1 身份和资源归属

P0 使用本地管理员引导账户和服务端会话认证，密码保存安全哈希；浏览器使用 HttpOnly 会话 Cookie。写请求检查来源并防护 CSRF。开发模式不得允许客户端任意指定 `user_id` 冒充用户。

所有会话、任务、运行、附件、知识库、成果和审批具有可信的 `owner_id`；组织模式启用后增加 `organization_id`。查询、列表、下载、SSE 和恢复都执行后端权限检查。

P1 用第二个测试用户验证隔离，不等到 P7 才实现资源归属。数据库里的 UUID 和不可猜测 ID 不替代授权。

### 4.2 核心资源

- **Conversation**：用户、模式、标题、Agent、创建时间、归档或删除标记。
- **Run**：会话、模式、状态、配置快照、执行预算、租约、开始结束时间、错误码、取消请求和检查点关联。
- **Message**：会话、运行、角色、类型化内容块、工具调用关联和顺序。规定唯一消息标识，避免重放重复入库。
- **WorkTask**：目标、当前运行、计划版本、执行证据、成果列表。
- **Plan / Step**：版本、步骤 ID、标题、状态、依赖；步骤状态单独维护。由 Agent Todo 同步并保存快照，不从自然语言猜测完成状态。
- **SandboxSession**：运行、环境类型、沙箱 ID、工作区、TTL、最近活动、快照和生命周期状态。一项任务可分别拥有 Coding 与 Browser 沙箱。
- **Artifact**：运行、类型、文件名、媒体类型、大小、校验摘要、存储键、归属和创建时间。
- **Approval**：运行、操作 ID、类型化参数、参数摘要、影响范围、审批人、决策、有效期和消费状态。
- **ModelConfig / AgentConfig**：模型提供商、模型 ID、凭证引用、工具/知识库/技能绑定、系统提示和权限策略。

运行创建时冻结模型、Agent、工具、技能和工作流版本引用。冻结的是配置和密钥引用，不将密钥明文复制到检查点或事件。

### 4.3 分阶段建表

- **P0–P1**：`users`、`auth_sessions`、`model_configs`、`agents`、`conversations`、`conversation_messages`、`task_runs`、`run_events`、`uploads`。
- **P2–P4**：`work_tasks`、`work_task_plans`、`work_task_steps`、`sandbox_sessions`、`approval_requests`、`artifacts`、`operation_executions`、`audit_logs`。
- **P5**：`knowledge_bases`、`documents`、`document_chunks`、`mcp_servers`、`tools`、`skills`、`agent_versions`、资源绑定与授权表；长期记忆使用独立命名空间。
- **P6–P7**：`workflow_definitions`、`workflow_versions`、`workflow_node_runs`、`api_credentials`、`api_call_logs`、`organizations`、`organization_members` 和共享资源权限。

所有阶段使用迁移脚本，禁止靠启动时随意创建业务表替代迁移。必要约束包括资源外键、运行事件唯一序号、幂等键唯一约束和单次审批消费约束。

## 5. API 与前后端协议

以下路由是本平台设计契约，不是 LangGraph Server 或 OpenSandbox 原生接口。P0 输出 OpenAPI Schema，并由前端类型检查和契约测试保持一致。

### 5.1 API 分组

- 系统：`GET /health/live`、`GET /health/ready`。存活与数据库/关键依赖就绪分开判断。
- 认证：`POST /api/v1/auth/login`、`POST /api/v1/auth/logout`、`GET /api/v1/auth/me`。
- 配置：`GET /api/v1/models`、`GET /api/v1/agents`；管理写接口按对应阶段开放。
- 会话：`POST /api/v1/conversations`、`GET /api/v1/conversations`、`GET/PATCH/DELETE /api/v1/conversations/{id}`、`GET /api/v1/conversations/{id}/messages`。
- 上传：`POST /api/v1/uploads`；下载或引用附件时重新检查归属。
- 发起聊天：`POST /api/v1/conversations/{id}/runs`。
- 创建任务：`POST /api/v1/tasks`、`GET /api/v1/tasks/{id}`。
- 运行：`GET /api/v1/runs/{id}`、`GET /api/v1/runs/{id}/events`、`POST /api/v1/runs/{id}/cancel`、`POST /api/v1/runs/{id}/pause`、`POST /api/v1/runs/{id}/resume`。
- 审批：`GET /api/v1/runs/{id}/approvals`、`POST /api/v1/approvals/{id}/decisions`。
- 工作区：`GET /api/v1/tasks/{id}/workspace`、`GET /api/v1/tasks/{id}/diff`；所有文件读取转交沙箱适配层。
- 成果：`GET /api/v1/runs/{id}/artifacts`、`GET /api/v1/artifacts/{id}/download`。
- 工作流：`POST/GET /api/v1/workflows`、`GET/PUT /api/v1/workflows/{id}`、`POST /api/v1/workflows/{id}/test-runs`、`POST /api/v1/workflows/{id}/publish`、`GET /api/v1/workflows/{id}/versions`、`POST /api/v1/workflows/{id}/runs`。

列表采用游标分页；请求体使用明确 Schema，限制文本长度、附件大小和节点数量。未实现的路由不以空成功响应冒充可用功能。

### 5.2 运行请求与错误

创建任务示例：

```json
{
  "goal": "读取上传的示例项目，修复问题并运行测试",
  "agent_id": "coding-assistant",
  "model_id": "configured-model-001",
  "upload_ids": ["upload_001"],
  "sandbox_profiles": ["coding"],
  "budget": {"max_steps": 40, "timeout_seconds": 1800, "max_tokens": 80000}
}
```

返回 HTTP 202，包含 `task_id`、`run_id`、`status`、`events_url`。环境类型和预算只是用户请求；后端按权限及服务端上限收紧，客户端不能传入任意沙箱 ID、宿主路径或凭证。

统一错误结构：

```json
{
  "error": {
    "code": "RUN_STATE_CONFLICT",
    "message": "当前运行状态不允许此操作",
    "request_id": "request_001",
    "details": {"status": "SUCCEEDED"}
  }
}
```

约定 401 表示未认证，404 用于不存在或无权获知的资源，409 用于状态或幂等冲突，422 用于输入校验，429 用于配额限制。服务端记录脱敏诊断日志。

### 5.3 SSE 事件契约

创建运行和订阅运行分离；浏览器关闭只断开订阅，不等同取消执行。

```text
id: 12
event: message.delta
data: {"schema_version":1,"event_id":12,"run_id":"run_001","thread_id":"thread_001","event":"message.delta","timestamp":"2026-10-29T08:00:00Z","data":{"message_id":"msg_001","text":"正在分析"}}

```

`event_id` 按运行单调递增，数据库唯一键为 `(run_id, event_id)`。SSE 的 `id` 与信封字段一致；客户端使用 `Last-Event-ID`，首次或手工恢复可用 `after_event_id`，同时提供时以请求头为准。

核心事件包括：

- 生命周期：`run.started`、`run.paused`、`run.resumed`、`run.completed`、`run.failed`、`run.cancelled`。
- 消息：`message.delta`、`message.completed`。
- 计划和步骤：`plan.updated`、`step.started`、`step.completed`、`step.failed`。
- 工具：`tool.started`、`tool.completed`、`tool.failed`。
- 审批与成果：`approval.required`、`approval.decided`、`artifact.created`。
- 工作流：`node.started`、`node.completed`、`node.failed`。

状态更新与对应业务事件在同一业务事务中保存，落库后再推送。图检查点与业务事务不假定天然原子，P4 通过运行版本、操作记录和恢复对账处理两者之间的故障窗口。

重连先补历史再跟随新事件，交界处不得漏事件；客户端按 ID 去重。心跳不生成业务事件。完整消息与增量共享消息 ID，`message.completed` 用于校准最终消息内容。

### 5.4 Agent Chat UI 改造

现有参考源码 `src/providers/Stream.tsx` 使用 LangGraph SDK `useStream`，并依赖服务探测、Thread 等能力。迁移时必须：

1. 用最小 Ask 图连接官方开发服务，记录线程、消息、工具调用、中断及恢复的实际行为。
2. 增加平台 API 客户端与 SSE 解析器，统一 Thread/Conversation 映射。
3. 替换 Stream Provider 和会话数据提供者的网络依赖，保持渲染组件可复用。
4. 覆盖停止生成、刷新重连、历史消息、审批和错误处理。
5. 移除产品路径中的任意服务地址和后台管理密钥输入，所有平台请求经过认证。

不完整仿造 LangGraph Server 全套接口，不同时维护两套生产线程存储。若选定 SDK 提供可复用传输扩展，可在验证行为一致后减少改造代码。

## 6. Ask、Work 与共享能力

### 6.1 Ask 图

使用 LangChain `create_agent` 构建，注入已授权模型、工具、Checkpointer 和必要提示。每轮加载同一会话的图状态，不将其他会话历史拼接进来。

模型注册至少支持两个配置项，可来自同一 OpenAI 兼容服务。配置保存 `base_url`、`model_name`、凭证引用和可用能力；密钥仅由服务端解析。附件按模型能力处理，文档附件不等于长期知识库入库。

P1 先提供一个无外部副作用的自定义 Tool 验证调用链。停止生成需要真正传播取消信号，并正确保留已输出消息，不能只停止页面动画。

### 6.2 Work 图

使用 Deep Agents 作为 Harness，配置任务级 backend、专业子 Agent、工具、审批策略和检查点。[官方 Backend 文档](https://docs.langchain.com/oss/python/deepagents/backends)支持通过可替换后端提供文件操作与沙箱执行，具体协议以仓库和锁定依赖为准。

执行顺序为：创建任务与预算 → 加载配置快照 → 获取必要沙箱 → Deep Agents 规划和执行 → 同步 Todo 与工具证据 → 验证 → 导出成果 → 结束或继续调整。

至少配置一个专业子 Agent，例如测试验证或资料整理 Agent。子 Agent 继承任务归属、工具权限和共享预算，不能借委派获得更高权限或不计入 Token 消耗。

复杂验收场景要求生成三个以上可观察步骤；简单任务不强制凑步骤数。计划变化递增 `plan_version`，保留旧版本供追踪。

任务成功必须有对应证据：编程任务保存 Diff、测试命令和退出码；网页任务保存来源、采集内容和截图；文档任务检查实际文件可读、格式正确且可下载。模型自述“完成”不直接写入 `SUCCEEDED`。

### 6.3 运行状态与控制

初始状态为 `PENDING`，Worker 认领后进入 `RUNNING`。合法转换：

- `PENDING → RUNNING / CANCELLED`。
- `RUNNING → WAITING_APPROVAL / PAUSED / SUCCEEDED / FAILED / CANCELLED`。
- `WAITING_APPROVAL → RUNNING / FAILED / CANCELLED`，批准或修改参数后恢复；拒绝作为工具拒绝结果交回 Agent，由其调整计划或终止。
- `PAUSED → PENDING / CANCELLED`，恢复后重新排队认领。

`SUCCEEDED`、`FAILED`、`CANCELLED` 是终态，不直接恢复；重试创建新运行并关联原运行。终态转换使用事务和版本检查，禁止旧 Worker 将取消结果覆盖为成功。

暂停和取消是请求：API 返回已接受，不立即声称后台已停止。Worker 在安全边界保存状态并中止可取消操作，完成后再记录对应状态。外部副作用已发生时无法通过取消撤回，必须返回实际结果。

执行预算覆盖主 Agent、子 Agent、模型与工具：默认上限建议 40 个执行步骤、30 分钟、80,000 Token；这些是待实测的开发初值，不是模型能力保证。图递归上限与业务步骤数分开设置。Token 按可用 usage 汇总；在途请求可能超出剩余额度，必须预留输出预算并限制后续调用，缺失 usage 时标记估算。

### 6.4 长任务调度与恢复

P3 起，创建运行时在事务中落库，由 Worker 使用行锁和租约认领，避免依赖 FastAPI 内存任务完成长时间工作。

初始建议心跳每 10 秒、租约 60 秒；使用数据库时间和递增租约代次。Worker 执行每个有副作用的操作前检查租约，续租失败停止领取新操作。仅有数据库租约仍不能保证旧进程和在途外部请求已停止，接管时必须核查未完成操作。

P4 故障恢复流程：检查过期租约 → 核对操作结果 → 检查沙箱存活与快照 → 读取图检查点 → 对账业务状态 → 安全继续或进入等待人工处理。

有副作用的操作记录唯一操作 ID 和外部幂等键。外部服务支持幂等时复用同一键；执行结果不确定且无法查询时禁止盲目重试。检查点恢复不等于外部操作“恰好执行一次”。

### 6.5 审批约束

使用 LangGraph 中断和恢复机制，平台将审批请求持久化后展示。[官方中断文档](https://docs.langchain.com/oss/python/langgraph/interrupts)说明恢复依赖持久状态和同一线程标识；实现需注意恢复时节点可能重新执行。

自动读取已授权资料；工作区写入和受限计算按任务策略；Git 推送、发送消息、提交表单、删除重要文件及修改外部业务数据必须审批。

批准必须绑定规范化参数摘要、具体目标、运行和操作 ID；执行前再次检查身份、参数、时效和单次消费。批准后修改参数必须重新审批，重复提交决策返回已有结果。

不能只依赖工具名称或提示词分类：通用 Shell、浏览器脚本和代码执行可能绕过“外部写入”工具。默认环境不注入外部写凭证；网络出口限制与专门受控工具配合执行审批。无法约束的通用操作按高风险操作审批或拒绝，不宣称已实现绝对语义识别。

## 7. OpenSandbox、浏览器与成果物

### 7.1 环境与资源

本地目标机器为 Windows 11、i5-14600KF、32GB 内存、RTX 5060 Ti 16GB。部署路径为 Windows → WSL2 → Docker Desktop → OpenSandbox。

- WSL2 建议上限 18GB 内存、10 个逻辑处理器。
- Coding 沙箱：4 vCPU、5GiB；提供 Git、Python、Java、Maven、Node.js。
- Browser 沙箱：2 vCPU、3GiB；提供 Playwright、Chromium 和文件处理依赖。
- 总沙箱并发最多 2，每类最多 1；容量不足时排队，不能无限新建容器。
- 初始 TTL 60 分钟，任务完成后导出并回收；空闲 10 分钟可回收，有活动命令或等待快照的环境不误判为空闲。
- 长任务在到期前续期；审批等待导致沙箱释放时，恢复需要重建环境并校验文件。
- GPU 不分配给沙箱，后续本地 Embedding/Reranker 单独评估。

以上是初始资源预算。沙箱额度共 8GiB，WSL 还要容纳平台、数据库、文件缓存和已有容器；启动前检查实际余量。系统繁忙时降为单沙箱，不保证两类始终同时运行。

### 7.2 适配器契约

平台 SandboxService 提供 `create`、`execute`、`read_file`、`write_file`、`upload`、`download`、`renew`、`destroy`。这些是平台方法，第三方调用在实现层映射。

调用者传任务上下文，平台从可信映射解析沙箱 ID。禁止 Agent 自由指定其他任务的沙箱 ID。

命令结果包含命令 ID、退出码、stdout、stderr、耗时、超时状态和输出截断标记；输出限额之外保存日志成果，避免塞满图状态。

文件路径限定到任务工作区，处理 `..`、绝对路径、符号链接和 ZIP 解压穿越。不得将宿主用户目录、平台凭证、平台数据库配置或宿主 Docker Socket 挂入工作沙箱。

OpenSandbox 管理服务自身如需容器管理权限，按官方受支持部署方式单独配置；此权限不传递到 Agent 执行容器。[OpenSandbox 官方项目](https://github.com/opensandbox-group/OpenSandbox)是部署和 SDK 行为的核验依据。

### 7.3 浏览器工具

封装打开、点击、输入、滚动、读取 DOM、截图、上传、下载和标签页管理。每个操作设置超时，页面或进程失效时返回可解释错误。

浏览器使用任务专属配置和下载目录。出站规则覆盖导航、重定向、子资源和下载；阻断本地地址、私有网段、云元数据地址及平台管理服务。仅在打开 URL 前检查一次不能构成完整防护。

Coding 与 Browser 文件通过平台传输或明确的任务级文件机制交换，不默认挂载可跨任务共享的目录。

### 7.4 成果持久化

导出顺序：沙箱文件 → 平台临时区 → 校验大小/格式/摘要 → 原子写入存储 → 创建 Artifact → 发送事件 → 返回授权下载地址。

持久成果类型包括文件、报告、截图、代码差异、压缩包和日志。下载地址引用 Artifact ID，不接受任意文件系统路径。

快照描述需包含运行、沙箱类型、文件清单和摘要；文件快照不承诺恢复进程内存或未完成命令。导出失败必须保留错误和可重试信息，不能先销毁唯一副本再报告成功。

## 8. 知识库、MCP、Skills 与记忆

### 8.1 P5a 基础知识检索

流程为上传 → 解析 → 分块 → Embedding → Qdrant → 混合检索 → 重排序 → 来源引用。

先支持 TXT、Markdown、PDF、DOCX、XLSX；图片和扫描 PDF 在 OCR/Docling 接入后才标注支持。解析失败或不支持的内容要明确报告。

知识片段保留知识库、文档、分块、页码或工作表位置、版本、生效时间和权限元数据。中文关键词检索策略必须实测，不能仅因启用混合检索就宣称中文精确召回合格。

检索 Tool 接收用户查询和已授权知识库范围，服务端计算过滤条件。跨文档比较可拆分查询、融合去重并补检索；来源标识由后端验证，缺失页码时不生成虚假定位。

文档索引使用版本标识和状态，避免将旧向量与新分块混用。删除先撤销可检索状态，再清理向量和文件；失败清理可重试，但不能继续召回已撤权内容。

### 8.2 P5b 能力管理

- MCP：发现工具、中文显示名、受控连接配置、Agent 绑定、审批策略和连通性测试；凭证加密或引用外部密钥，不回显。远程地址受限，stdio 进程在受限环境执行。
- Skills：导入目录或 ZIP，解析 `SKILL.md`，验证路径与包大小，记录版本和摘要，绑定 Agent；技能脚本通过沙箱执行。
- Agent：配置模型、指令、工具、知识库、Skills、权限，发布版本；运行固定引用已选版本。
- 记忆：会话状态由 Checkpointer 管理，长期记忆使用 Store/数据库，按用户或用户+Agent 命名空间隔离；支持查看和删除。

知识库提供文档证据，长期记忆保存持续有用上下文，两者生命周期和权限分别管理。Ask、Work、Workflow 复用同一检索及工具授权入口。

## 9. 固定工作流与发布

### 9.1 定义与编译

工作流包含 `schema_version`、输入输出 JSON Schema、节点、边及执行预算。画布坐标和样式与执行定义分开保存。

第一批节点为 Start、End、LLM、Agent、RAG、Tool/MCP、HTTP、Condition、Transform。首个纵向切片先跑通 Start → LLM → End，然后逐项补齐第一批节点。

节点声明输入输出类型和资源引用。变量引用采用明确的路径解析及预置转换器；不使用 Python `eval` 或任意脚本作为 Transform。

Compiler 验证唯一节点 ID、合法类型、一个入口、可达出口、引用存在、变量来源和类型、分支完整、资源权限及预算。首版只接受无环流程，拒绝不可达节点和悬空边；并行、循环、代码、人工审批及子工作流作为第二批能力，不以默认支持全部 LangGraph 能力代替验证。

分支节点使用有限操作符和明确默认出口；HTTP 节点执行受控网络策略和超时限制。资源授权在保存/发布及运行时均检查。

### 9.2 调试和版本

草稿保存使用修订号避免覆盖；调试绑定草稿修订号，显示节点输入输出、耗时、错误、工具和 Token 用量。敏感字段脱敏。

生命周期为 `DRAFT → TESTED → PUBLISHED → DEPRECATED`。测试通过后修改执行定义会使测试状态失效；发布要求当前修订通过测试。

发布生成不可变版本和内容摘要，已有运行固定引用版本。发布事务检查修订号，避免测试旧稿后发布新稿。下线默认只阻止新运行，不隐式中断在途任务。

### 9.3 第三方 API

第三方使用独立 API Key，只授权特定工作流和版本，不具备访问内部会话的默认权限。Key 随机生成，服务端保存摘要；限制作用域、有效期、速率和并发，支持撤销。

发起工作流返回 202 和运行 ID。`Idempotency-Key` 按调用凭证和路由作用域唯一，同键同请求返回原运行，同键不同请求返回 409。创建记录与运行入队在同一业务事务完成。

Work 调用已发布工作流时，继承用户身份、权限和剩余预算；记录父子运行，限制深度和总调用次数，防止递归放大。

## 10. 分阶段开发清单与验收

所有阶段按“实现 → 定向测试 → 实际联调 → 记录证据”完成。没有真实环境验证的项目标记“待联调”，不能只凭 Mock 测试判定通过。

### P0：工程基础与协议闭环

任务：

1. 整合 Agent Chat UI 到 `apps/web`，保留上游许可证和提交记录，建立中文资源。
2. 初始化 API、配置、日志、认证、PostgreSQL 迁移和 Checkpointer 生命周期。
3. 创建 Ask、Work、Workflow 图工厂入口；后两者可明确标记待接入，不开放虚假执行。
4. 配置一个 OpenAI 兼容模型，用官方开发服务验证原始流协议，再完成平台传输适配。
5. 提供本机启动说明、示例配置、独立 Compose 和最小健康检查。

验收：登录后完成一轮真实流式对话；刷新可读历史；进程重启后读取测试线程检查点并继续；未登录不能读会话或订阅事件；浏览器无模型/管理密钥；新增界面中文；不依赖 Agno。

### P1：Ask MVP

任务：会话新建、重命名、删除；至少两个模型配置；Agent 选择；Markdown、附件、工具调用、停止生成；历史和消息归档；完善 API Schema。

验收：连续多轮保留上下文，两个会话互不串话；两个用户无法访问彼此详情、列表、事件、附件；自定义 Tool 的实际结果正确显示；停止后服务端不继续无边界调用；不支持的附件给出中文说明。

### P2：OpenSandbox 执行能力

任务：Coding/Browser 镜像、平台适配器、任务绑定、资源配额、TTL/续期、文件传输、命令日志、浏览器截图、Diff 和初步成果导出。

验收：Coding 修改示例文件并运行测试；Browser 打开测试网页、填写输入、截图和下载；两类沙箱同时受资源限制；第三个沙箱请求排队；跨任务访问、路径逃逸和敏感宿主目录读取被拒绝；任务结束可回收。

### P3：动态 Work 原型

任务：引入数据库认领 Worker；Deep Agents backend；Todo 同步与计划版本；专业子 Agent；工具/步骤事件；三栏工作台；验证器；执行时间、步骤和 Token 预算；服务端配置的基础 MCP/Skill 接入。

验收 A：提供一个带确定失败测试的示例代码仓库，Agent 定位、修改、查看 Diff、执行测试；首次修复失败时根据证据调整，最终保存通过记录。

验收 B：访问三个受控公开测试站点，提取产品资料并生成 Excel 和 Markdown 报告；校验表格内容、来源和可下载文件。

两场景均须观察到三个以上任务步骤、至少一次专业子 Agent 调用、实时事件及预算约束。任务是否成功由实际证据决定。

### P4：可靠性、审批与持久成果

任务：完善租约和恢复对账；暂停、取消、恢复；持久审批；副作用幂等记录；SSE 重放；快照重建；成果独立存储和回收失败处理。

验收：关闭页面不终止任务，重新打开可续看；Worker 在模型调用、工具执行、审批前后等故障点退出后可对账；审批拒绝/改参/重复提交符合规则；两 Worker 不能同时有效推进同一运行；沙箱销毁后仍能下载成果；结果不确定的外部操作不会自动重复提交。

通过 P0–P4 后交付 Work MVP；未通过 P5a 时，不将知识库问答列为已完成能力。

### P5a：基础 RAG

任务：文档管理、解析索引、混合检索、重排序、权限过滤、跨文档补检索、来源引用、检索调试。

验收：同一检索能力可由 Ask、Work 调用，P6 再验证 Workflow 接入；建立不少于 50 道标注问题，覆盖精确词、语义、表格、跨文档、版本冲突和无答案。初始质量目标为 Recall@10 ≥ 90%、引用正确率 ≥ 95%，另报告跨文档证据覆盖率、拒答表现和耗时；结果须基于实际测试集，不能作为框架保证。

### P5b：能力中心

任务：中文模型/Agent/MCP/Skills 管理、版本绑定、凭证引用、知识授权和长期记忆管理。

验收：页面能配置并实际调用受控 MCP、Skill 和知识库；私有记忆和知识不跨用户泄露；撤销权限后新运行和已有运行的新操作均重新检查授权；凭证不会出现在配置响应和日志中。

### P6：工作流设计与调试

任务：React Flow 中文画布、节点配置、Schema、Compiler、草稿保存、分支、节点事件、第一批节点执行和调试。

验收：无需改 Python 创建、保存、重新打开并执行一条“开始 → 检索 → LLM → 条件 → 结束”流程；分支命中和默认出口正确；节点失败可定位；非法环、悬空引用、越权资源和任意代码表达式被拒绝。

### P7：发布与平台化

任务：测试门禁、不可变版本、发布下线、外部 Key、调用限流、幂等、运行查询、审计、成本展示、组织角色和 Work 调用工作流。

验收：发布 v1/v2 后旧运行仍关联原版本；不同 Key 仅访问授权版本和运行；同幂等请求不重复创建任务；撤销 Key 立即生效；Work 可调用授权版本；备份恢复、授权测试和并发测试通过后才能对外开放。

## 11. 本地部署、测试与交付规范

### 11.1 部署规划

代码继续保留在用户指定 Windows 路径。使用 Windows 本地前后端进程加 WSL2/Docker 服务完成初期开发；若目录挂载性能影响构建，再单独评估迁移至 WSL Linux 文件系统，不擅自移动项目。

建议开发端口为 Web 3100、API 8100、PostgreSQL 55432、OpenSandbox 8180、Qdrant 6335；仅为候选值，启动前检查占用。仅将需要访问的端口绑定到本机，容器之间通过服务名通信。

P0 启动 Web/API/PostgreSQL；P2 增加 OpenSandbox；P3 增加 Worker；P5a 增加 Qdrant。MinIO、Redis 和本地模型按需要启用。

示例环境配置包括数据库连接、会话密钥、模型服务地址/凭证引用、OpenSandbox 地址/凭证、文件根目录和资源配额。真实配置不入库，镜像不内嵌密钥。

分别验证宿主、WSL、容器拉取、模型 API 与浏览器出网。容器内 `127.0.0.1` 指向容器自身，不能直接照搬 Windows 代理地址。

### 11.2 测试层次

- 单元测试：状态转换、授权、预算、事件序号、编译器、路径限制、参数摘要、索引过滤和幂等冲突。
- 集成测试：真实 PostgreSQL 迁移/检查点/任务认领，OpenSandbox 文件和命令，浏览器与 Qdrant 检索。
- 前端测试：中文空状态/错误/审批、消息渲染、SSE 分包解析/重连/去重、任务与节点状态同步。
- 端到端：Ask 多轮聊天、Work 编程、浏览器生成文件、审批恢复、工作流发布调用。
- 故障验证：断开 SSE、停止 Worker、沙箱到期、模型超时、磁盘写入失败、外部结果未知。

测试数据使用独立库、目录和容器，不清理其他项目数据。真实模型和网络联调记录使用配置、执行时间与脱敏证据；离线测试可用替身，但报告中说明范围。

每阶段通过后记录实际命令、结果、未验证项和下一步。只执行与改动相关的验证，不因文档修改运行全仓测试。

### 11.3 完成标准

一个功能必须同时具备：可运行实现、明确输入输出、可信授权、中文交互、必要的持久化与事件、相关测试、启动或使用说明，以及实际验收证据。

阶段状态使用“未开始、开发中、待联调、已验收”；不能将文档存在、接口返回 200、模型生成完成说明或单独 Mock 通过视为阶段完成。

交付文件按需包括迁移脚本、锁文件、环境示例、Compose、镜像定义、API Schema、测试报告和阶段进度。保留用户已有改动，不自动提交或推送代码。

## 12. 首批实施顺序与依赖检查

从以下顺序开始，每批形成可验证的小闭环：

1. **P0.1 工程与配置**：整合前端、新建 API 包、独立数据库、最小迁移；验证启动和健康检查。
2. **P0.2 身份与会话**：实现登录、会话归属、模型注册；验证未认证和跨用户拒绝。
3. **P0.3 协议与图**：验证官方基准，完成平台流适配和持久 Ask 图；验证首轮流式聊天、刷新与重启恢复。
4. **P0.4 中文和交付**：覆盖入口、设置、错误与空状态，完成环境样例和启动说明；验证浏览器页面与密钥边界。
5. **P1 功能补齐**：两模型、多轮、附件、工具和停止生成，验收 Ask MVP。
6. **P2–P4**：沙箱 → 动态 Work → 审批恢复和成果，验收 Work MVP。
7. **P5a/P5b–P7**：基础检索 → 能力中心 → 工作流 → 发布平台。

模型 API 可用性、OpenSandbox 镜像/SDK 兼容性、服务端受控出网能力是联调前置条件。缺失时可以继续离线契约和组件开发，相关验收保留待联调状态，不填入虚假通过记录。

本开发文档不包含当前编程助手的订阅额度或重置卡逻辑；它们属于执行会话管理，不作为平台运行依赖，也不赋予开发中的 Agent 额外账户权限。

## 13. 官方核验依据

本文的路由、业务模型、调度规则和阶段拆分是项目设计；第三方接口以实际锁定版本为准。

- [LangGraph 持久化与记忆](https://docs.langchain.com/oss/python/langgraph/add-memory)：图检查点与长期记忆的选型依据。
- [LangGraph 中断与恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)：审批暂停、线程关联与恢复行为。
- [Deep Agents Backend](https://docs.langchain.com/oss/python/deepagents/backends)：可替换文件后端与沙箱执行能力。
- [Agent Chat UI 官方仓库](https://github.com/langchain-ai/agent-chat-ui)：前端复用及协议改造基准。
- [OpenSandbox 官方仓库](https://github.com/opensandbox-group/OpenSandbox)：沙箱部署、生命周期和 SDK 核验入口。

参考记录日期为 2026-10-29。未在本文验证的 Beta 接口、镜像标签与具体版本兼容性，在对应阶段锁定依赖时再核验。


## 14. 实施与验收入口

2026-10-31 的实现、测试证据、启动和备份恢复范围见 [implementation-verification.md](implementation-verification.md)。已接入 Ask、持久 Work、审批/恢复、能力中心、画布第一批节点和发布平台；真实模型、官方开发服务基准与其余未实现项继续单独跟踪，不将原计划整体标成已验收。
