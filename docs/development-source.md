# AI Agent 平台完整二次开发方案

技术架构：LangGraph + Deep Agents + Agent Chat UI + OpenSandbox

这套方案的目标不是开发一个简单聊天机器人，而是构建一个支持智能聊天、自主执行任务、可视化编排、工作流发布和第三方调用的通用 AI Agent 平台。

我建议采用 8 个开发阶段，先实现可用的 Ask + Work，再完善固定工作流和平台管理功能。

## 一、明确最终产品形态

最终产品包含三种主要使用模式：

Ask — 智能对话模式

类似 ChatGPT 普通聊天，支持多模型切换、RAG 知识检索、MCP 工具、Skills、多模态文件上传、会话历史和长期记忆。

Work — 自主任务模式

类似 Cursor Agent 与 ChatGPT Work。用户提供目标，AI 自主拆解任务、动态规划、调用子 Agent、执行命令、操作浏览器、修改文件、验证结果并交付成果。

Workflow — 固定工作流模式

类似 Dify 工作流。用户通过中文可视化画布拖拽节点、连接流程、调试和发布工作流，并生成 API 供其他系统调用。

### 核心产品要求

| 项目       | 目标                                       |
| -------- | ---------------------------------------- |
| 部署方式     | 优先本地、自托管                                 |
| 开发语言     | Python + TypeScript                      |
| UI 语言    | 全部简体中文                                   |
| Agent 模型 | 支持多模型、多 Agent                            |
| Work 编排  | 动态任务计划、执行、验证、重规划                         |
| Workflow | 固定执行图、可视化编辑、版本发布                         |
| 沙箱       | OpenSandbox，隔离代码和浏览器环境                   |
| 知识库      | 混合检索、跨文档召回、引用溯源                          |
| 扩展能力     | MCP、Skills、工具注册、后续 A2A                   |
| 数据存储     | PostgreSQL + 对象存储                        |
| 部署平台     | Windows 11 + WSL2 + Docker 开发，后续迁移 Linux |

## 二、最终技术栈

| 层级          | 选型                       | 职责                |
| ----------- | ------------------------ | ----------------- |
| 前端          | Agent Chat UI + Next.js  | Ask 聊天基础界面        |
| Work 前端     | Next.js 自定义组件            | 任务进度、终端、文件、审批     |
| Workflow 前端 | React Flow               | 工作流拖拽编排           |
| API         | FastAPI                  | 平台业务接口、鉴权、执行控制    |
| Ask Agent   | LangChain `create_agent` | 模型、工具、RAG 对话      |
| Work Agent  | Deep Agents              | 任务规划、工具调用、子 Agent |
| 工作流引擎       | LangGraph                | 状态图、条件、循环、持久化     |
| 沙箱          | OpenSandbox              | 命令、文件、代码、浏览器      |
| 浏览器         | Playwright + Chromium    | 浏览器交互和自动化         |
| 向量存储        | Qdrant                   | 知识库向量检索           |
| 数据库         | PostgreSQL               | 平台业务数据和检查点        |
| 缓存/任务协调     | Redis                    | 按需引入，不能替代持久队列     |
| 文件存储        | 本地文件系统，后续 MinIO          | 上传文件、成果物、快照       |
| 测试          | pytest + Playwright      | 后端、浏览器和端到端测试      |

Deep Agents 官方定位就是基于 LangGraph 的多步骤 Agent Harness，已经提供文件操作、子 Agent、Skills、上下文管理和人工审批等能力。我们应该复用这些能力，而不是重新写一个通用 Agent 循环。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+1



### 一项重要的架构约定

LangGraph 作为唯一的任务状态编排引擎，但 Ask、Work 和固定 Workflow 使用三种独立的执行图。

这样三种模式可以共用模型、工具、知识库和执行基础设施，又不会彼此耦合。

## 三、整体系统架构设计

Next.js 中文 Web 平台

Ask 对话 · Work 任务 · Workflow 画布 · 管理中心

FastAPI Platform API

身份认证、权限、API、任务路由、SSE 事件、版本发布

LangGraph 执行层

Ask Graph

LangChain Agent

Work Graph

Deep Agents

Flow Graph

StateGraph

共享业务能力

Model Registry

RAG

Tools / MCP

Skills

Agents

Memory

权限 / 审批

Artifacts

OpenSandbox

Coding / Browser / Files

Storage

PostgreSQL / Qdrant / 文件

### 后端采用模块化单体，而不是一开始拆微服务

建议将 FastAPI 作为统一平台 API，LangGraph 和 Deep Agents 作为其内部执行模块。

OpenSandbox 独立运行，因为它需要管理容器生命周期，安全边界与普通业务服务不同。

第一版保持以下进程边界即可：

| 服务                           | 是否独立部署       |
| ---------------------------- | ------------ |
| Web Frontend                 | 是            |
| Platform API + Agent Runtime | 暂时合并         |
| OpenSandbox Server           | 是            |
| PostgreSQL                   | 是            |
| Qdrant                       | 知识库阶段再部署     |
| Redis / Worker               | 长任务可靠调度阶段引入  |
| MinIO                        | 文件需要对象存储时再引入 |

避免一开始维护过多基础设施。

### 一个必须提前解决的兼容问题

Agent Chat UI 官方主要连接 LangGraph Server，并不是任意一个 FastAPI 接口都可以直接接入。它使用线程、运行和流式消息相关协议。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



因此确定以下路线：

- 本地原型：先用 LangGraph 开发服务器验证 Agent Chat UI。
- 正式自托管：通过后端 API 适配层连接 FastAPI，必要时修改 Agent Chat UI 的 Stream Hook。
- 长期约束：所有前端请求必须经过平台身份认证和资源权限检查。

官方也警告过：未经鉴权直接公开 Agent Chat UI 的 API Passthrough，可能允许外部访问其他线程和数据。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



这部分应该在第一阶段处理，不能到准备上线时才发现前后端协议不匹配。

## 四、八个开发阶段总览

P0

工程基础与统一架构

1–2 周

项目结构、中文化、协议验证

P1

Ask 智能聊天

2–3 周

多模型、会话、工具调用

P2

OpenSandbox 执行环境

2–3 周

代码、文件、浏览器操作

P3

Work 动态任务执行

3–5 周

规划、子任务、验证、重规划

P4

任务可靠性与审批

2–3 周

检查点、断线恢复、成果物

P5

RAG、MCP、Skills 能力中心

2–4 周

知识库、技能、工具配置

P6

固定工作流设计器

3–5 周

React Flow、编译、调试

P7

工作流发布与平台化

3–5 周

对外 API、权限、监控、部署

工期仅是单名熟悉 Python/TypeScript 的开发者全职投入时的粗略估算，不是承诺。P0–P4 优先形成 Work MVP；P5–P7 再建设完整平台。部分开发可并行，实际时间取决于现有代码复用情况和测试要求。

# 第一部分：基础开发与 Work MVP

## P0：工程基础与项目初始化

优先级最高

目标：搭建能够持续二开的工程结构，跑通前后端、模型调用和 LangGraph 状态流转。

### 开发任务

| 模块        | 具体任务                       |
| --------- | -------------------------- |
| 前端        | Fork Agent Chat UI，保留聊天功能  |
| 中文化       | 导航、聊天、设置、提示、错误信息全部简体中文     |
| 后端        | 初始化 FastAPI，划分独立业务模块       |
| LangGraph | 建立 Ask、Work、Workflow 三个图入口 |
| 模型配置      | 接入一个 OpenAI 兼容 API 模型      |
| 数据库       | PostgreSQL，初始化迁移机制         |
| 协议        | 验证 Chat UI 的线程、流式消息与中断事件   |
| 环境        | Docker Compose、本地环境变量、日志   |

推荐项目目录：

```
ai-agent-platform/
├── apps/
│   └── web/                      # Agent Chat UI 二开
│       ├── app/
│       ├── components/
│       ├── features/
│       │   ├── ask/
│       │   ├── work/
│       │   ├── workflows/
│       │   └── admin/
│       └── locales/              # 简体中文资源
│
├── services/
│   └── api/
│       ├── app/
│       │   ├── api/              # FastAPI 路由
│       │   ├── agents/           # Ask / Work Agent
│       │   ├── graphs/           # LangGraph 执行图
│       │   ├── workflows/        # 固定工作流
│       │   ├── sandbox/          # OpenSandbox Adapter
│       │   ├── knowledge/        # RAG
│       │   ├── tools/            # MCP / Tools
│       │   ├── skills/           # Skills 管理
│       │   ├── models/           # 模型管理
│       │   ├── runtime/          # 事件、执行、审批
│       │   ├── storage/          # 数据库、文件
│       │   └── security/         # 用户权限
│       ├── tests/
│       └── pyproject.toml
│
├── infra/
│   ├── compose/
│   ├── sandbox-images/
│   └── config/
│
├── skills/
├── scripts/
└── docs/
```

Python 建议使用 3.11 或 3.12，并锁定依赖版本。Deep Agents 当前 SDK 要求 Python 3.11+。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



### P0 验收标准

- 前端可以连接本地后端。
- 能进行一轮完整的流式聊天。
- LangGraph 能保存、读取、恢复测试线程状态。
- 所有新增页面使用简体中文。
- 不依赖 Agno 运行。
- 前端没有暴露 LLM API Key 或后台管理密钥。

交付成果：一个可运行的项目骨架。

## P1：Ask 智能聊天模式

目标：完成一个具备多模型、会话历史、工具调用能力的 AI 聊天系统。

这一阶段使用 LangChain 的 `create_agent`，不需要使用完整 Deep Agents。

### 前端功能

| 功能       | 需求             |
| -------- | -------------- |
| 新建对话     | 创建独立会话         |
| 会话历史     | 查看、重命名、删除      |
| 模型切换     | 选择已配置的 LLM     |
| Agent 切换 | 选择不同专业 Agent   |
| 流式输出     | 实时显示回答         |
| 工具展示     | 查看调用工具及执行结果    |
| 文件上传     | PDF、Word、图片等附件 |
| Markdown | 表格、代码高亮、复制     |
| 停止生成     | 取消当前模型运行       |

### 后端功能

建议采用：

```
Ask Agent
 ├── Model Router
 ├── Conversation History
 ├── Tool Registry
 ├── Knowledge Retriever（后续接入）
 └── Memory Store（后续接入）
```

其中模型管理不要写死在 Agent 定义中，而是做统一的 Model Registry。

一个 Agent 的配置可以包含：

```
{
  "agent_id": "general-assistant",
  "name": "通用助手",
  "mode": "ask",
  "model_id": "model-001",
  "tool_ids": [],
  "knowledge_base_ids": [],
  "skill_ids": [],
  "system_prompt": "你是一个专业的AI助手"
}
```

模型配置单独管理，用户切换模型不需要修改 Agent 源代码。

### P1 验收标准

- 可以连续对话，重新打开页面后历史不丢失。
- 支持至少两个不同模型配置。
- 支持注册并调用一个自定义 Tool。
- Agent 可以正确返回工具调用结果。
- 同一用户的不同会话独立。
- 不同用户无法访问彼此会话。

交付成果：可用的 Ask MVP。

## P2：OpenSandbox 沙箱执行环境

目标：让 AI 有能力安全地操作文件、代码和浏览器，为 Work 模式提供实际执行能力。

### 1. 先建立两种 Sandbox Profile

根据你目前的 32GB 内存电脑，继续使用之前确定的资源预算。

| Profile | CPU 上限 | 内存上限 | 主要工具                          |
| ------- | ------ | ---- | ----------------------------- |
| Coding  | 4 vCPU | 5GiB | Git、Python、Java、Maven、Node.js |
| Browser | 2 vCPU | 3GiB | Playwright、Chromium、文件处理      |

本地总并发上限设置为 2 个，每类最多 1 个。WSL2 上限建议 18GB RAM、10 个逻辑处理器；GPU 暂不分配给沙箱。

### 2. 建立 Sandbox Adapter

不要让 Deep Agents 或固定 Workflow 直接依赖 OpenSandbox 的所有 SDK 细节。

后端建立统一接口：

```
class SandboxService:    async def create(self, task_id, profile): ...    async def execute(self, sandbox_id, command): ...    async def read_file(self, sandbox_id, path): ...    async def write_file(        self, sandbox_id, path, content    ): ...    async def upload(self, sandbox_id, file): ...    async def download(self, sandbox_id, path): ...    async def destroy(self, sandbox_id): ...
```

这是建议的应用层接口，而不是 OpenSandbox SDK 原有类定义。

Deep Agents 支持可替换的文件系统 Backend，Sandbox Backend 可以向 Agent 暴露文件读写和 `execute` 工具。你需要做的是将 OpenSandbox 的 SDK 适配到这套接口。

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain

+1



OpenSandbox 官方也提供 Deep Agents、LangGraph、Playwright 和 Chrome 示例，可以用于减少适配工作。

[image](https://www.google.com/s2/favicons?domain=https://open-sandbox.ai\&sz=32)

OpenSandbox



### 3. 编程沙箱功能

第一版实现：

- Git Clone、Git Status、Git Diff。
- 文件读取、搜索、修改。
- Shell 命令执行。
- Java Maven 测试、Python pytest、Node.js 构建。
- stdout / stderr 输出。
- 命令超时与退出码。
- 修改文件的 Diff 展示。
- 下载生成的文件。

### 4. 浏览器沙箱功能

第一版实现：

- 打开 URL、点击、输入、滚动。
- DOM 内容读取。
- 截图和页面快照。
- 页面下载、文件上传。
- 多标签页管理。
- 常见超时和导航错误处理。
- 将截图提供给 Work UI 预览。

Playwright 是首选交互方式；需要远程可视化浏览器时，再加入 VNC 或类似方式。

### 5. 安全边界

禁止直接将 Windows 的 `C:\Users`、`D:\work` 等主机目录整体挂进沙箱，也不能暴露宿主 Docker Socket。

对于执行代码、访问未知网站和处理上传文件的任务，沙箱应使用非特权用户、资源限制、有限的文件权限和受控网络。浏览器访问还需要限制内部 IP、云元数据地址和本地管理接口，避免 SSRF。

### P2 验收标准

- 创建两个不同 Profile 的沙箱。
- Coding Sandbox 成功修改文件并执行测试。
- Browser Sandbox 成功打开网页、操作并截图。
- 两个沙箱不能互相访问文件。
- 任务结束后可以下载成果并销毁沙箱。
- 沙箱无法读取平台 API Key、数据库凭证及宿主敏感文件。

交付成果：独立的沙箱执行服务与统一适配层。

## P3：Work 动态任务执行引擎

项目核心功能

目标：用户输入一个目标，AI 像 Cursor 一样自主规划、执行、验证和调整任务。

这里以 Deep Agents 为核心，不再从零开发完整的 Agent Harness。

### 1. Work 执行流程

用户输入任务目标

Task Manager

创建任务、分配模型、权限和执行预算

Deep Agents Planner

分析目标、生成待办任务和执行计划

动态任务循环

选择任务

任务依赖与优先级

执行任务

Tools / Skills / Sandbox

验证结果

规则校验 / 测试

调整计划

补充 / 修改子任务

生成最终回复与成果物

### 2. Work 的核心模块

| 模块               | 职责              | 实现                 |
| ---------------- | --------------- | ------------------ |
| Task Manager     | 主任务生命周期与配额      | 自建                 |
| Planner          | 任务计划及拆解         | Deep Agents        |
| Executor         | 调用 Tool、子 Agent | Deep Agents        |
| Subagent Manager | 多 Agent 委派      | Deep Agents        |
| Verifier         | 验证执行结果          | 规则 + AI            |
| Replanner        | 根据结果更新计划        | Deep Agents + 状态同步 |
| Sandbox Manager  | 创建、分配、回收环境      | OpenSandbox        |
| Run Manager      | 运行状态、事件、取消      | LangGraph + 自建     |
| Artifact Manager | 管理输出文件          | 自建                 |

这里要避免重复设计：Deep Agents 本身已经具有任务规划与 Todo 机制。你的 Task Manager 应该同步和持久化 Agent 的计划，供用户查看和控制，而不是默认再调用一次 LLM 生成另一套相互冲突的计划。

只有需要显式依赖关系、并行调度或严格任务顺序时，才增加独立的 DAG Scheduler。

### 3. Work Task 数据结构

建议统一使用以下任务模型：

```
{
  "task_id": "task_001",
  "mode": "work",
  "goal": "分析Java项目并修复接口问题",
  "status": "RUNNING",
  "plan_version": 2,
  "sandbox_id": "sandbox_001",
  "steps": [
    {
      "id": "step_001",
      "title": "定位问题代码",
      "status": "SUCCEEDED",
      "depends_on": []
    },
    {
      "id": "step_002",
      "title": "修复接口逻辑",
      "status": "RUNNING",
      "depends_on": ["step_001"]
    }
  ]
}
```

`plan_version` 很重要。AI 每次调整计划都递增版本，前端可以看到计划如何变化，也便于定位某一次执行失败的原因。

### 4. Work 页面设计

参考 Cursor 的交互方式，但采用你自己的简体中文界面。

Work · 修复项目接口问题

示意：运行中

任务

项目扫描

接口分析

代码修复

验证测试

执行过程

已找到相关接口

正在分析 SQL...

工具调用及日志

工作空间

文件树

代码 Diff

终端 / 浏览器

成果文件

响应式示意布局；实际桌面端建议左侧任务、中间执行对话、右侧工作空间。

### 5. Work 的两组验收场景

场景 A：Cursor 式编程

> 读取 Java 项目，定位某个接口异常，修改代码并执行相关测试。

必须能完成：

定位文件 → 分析代码 → 修改文件 → 查看 Diff → 执行测试 → 根据测试结果再次修改 → 汇总结果。

场景 B：ChatGPT Work 式浏览器和文件操作

> 访问三个公开网站，收集产品信息，整理成 Excel 并生成一份比较报告。

必须能完成：

打开网页 → 提取内容 → 交叉验证 → 生成 Excel → 保存报告 → 提供可访问的成果物。

### P3 验收标准

- Agent 能动态生成不少于三个子任务。
- 子任务失败时，可以修改后续计划。
- 能主动调用 Coding 和 Browser 沙箱。
- 能调用至少一个专业子 Agent。
- 前端实时显示任务状态和工具执行情况。
- 有最大步骤数、执行时间和 Token 预算限制。
- 不把“LLM 说已经完成”直接当作任务成功，必须检查实际产物或执行证据。

交付成果：第一版真正能自主执行任务的 Work 模式。

# 第二部分：可靠执行与共享能力

## P4：任务持久化、人工审批与成果物管理

目标：让 Work 不仅能执行，还能安全、稳定地完成长时间任务。

这是从 Demo 升级为真正可用产品的关键阶段。

### 1. 统一任务状态

建议使用以下状态：

| 状态                 | 含义       |
| ------------------ | -------- |
| `PENDING`          | 已创建，等待执行 |
| `RUNNING`          | 正在执行     |
| `WAITING_APPROVAL` | 等待人工确认   |
| `PAUSED`           | 用户主动暂停   |
| `SUCCEEDED`        | 执行成功     |
| `FAILED`           | 执行失败     |
| `CANCELLED`        | 用户取消     |

运行状态和任务步骤状态分别维护。

LangGraph Checkpointer 负责保存执行图状态，PostgreSQL 业务表负责保存任务状态、权限、查询索引和历史记录。这两部分不能混为一谈。

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain



### 2. 任务恢复

采用 PostgreSQL Checkpointer。

需要特别注意：LangGraph 恢复的是执行状态，不是沙箱里的全部文件和进程。

因此还要处理：

- LangGraph Checkpoint 与沙箱 ID 关联。
- 工作空间文件定期保存。
- 沙箱到期后重新创建环境并恢复文件。
- 重试操作不能重复执行已经提交的业务写入。
- 断开浏览器连接不应自动终止后台任务。

对于短时间暂停，可以保留同一个沙箱；对于长时间暂停，可以保存 Git 工作树状态、文件快照和成果物，再释放沙箱资源。

### 3. 人工审批 HITL

LangGraph 官方的 `interrupt()` 和 `Command(resume=...)` 提供暂停与恢复能力。

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain



建议设计三级权限：

| 操作                             | 处理        |
| ------------------------------ | --------- |
| 检索知识库、读取已授权文件                  | 自动执行      |
| 修改工作空间代码、运行受限测试                | 按任务授权策略执行 |
| 删除重要文件、推送 Git、发送消息、提交表单、修改外部系统 | 必须审批      |

审批必须由后端执行强制校验，不能仅依靠模型提示词。

审批页面显示：目标操作、影响范围、参数、文件差异、可能的副作用，以及“批准 / 拒绝 / 修改参数”。

### 4. 成果物管理

统一设计 Artifact：

| 类型           | 示例             |
| ------------ | -------------- |
| `file`       | Excel、PDF、Word |
| `code_diff`  | 代码修改差异         |
| `screenshot` | 浏览器截图          |
| `report`     | Markdown 分析报告  |
| `archive`    | ZIP 项目文件       |
| `log`        | 执行日志           |

文件必须从沙箱导出到平台管理的存储目录或对象存储，再提供下载。

### P4 验收标准

任务运行中关闭浏览器，重新登录仍能查看进度；任务暂停后可以审批并继续；沙箱销毁后仍能下载已保存成果；API 重试不会重复提交不可逆操作。

交付成果：稳定的 Work MVP。

到这一阶段，你已经拥有可以真正使用的 Ask + Work 产品，而不是只有对话和工具调用的演示程序。

## P5：RAG、MCP、Skills 与 Agent 能力中心

目标：三种模式都能复用同一套知识、工具、技能和 Agent。

建议这一阶段集中完成能力标准化，避免 Ask、Work 和固定 Workflow 各自实现一遍。

### 1. RAG 知识库

第一版采用：

`文档上传 → 解析 → 分块 → Embedding → Qdrant → Hybrid Search → Reranker → 引用答案`

建议支持：

- PDF、Word、Markdown、TXT、Excel。
- 多文档统一检索。
- 文档元数据、版本、生效时间和权限过滤。
- 检索分数、片段来源、页码。
- Agent / 用户 / 知识库关联。
- 检索调试与质量评测。

复杂 PDF 和扫描件可以先增加 Docling 等解析器，暂不引入完整的 RAGFlow 平台。

知识库要成为一个独立检索 Tool，Ask、Work 和 Workflow 共用。

### 2. MCP 工具中心

采用官方 LangChain MCP 接入能力，将 MCP Server 暴露的工具注册到 Tool Registry。当前 `langchain[mcp]` 已提供 `MCPAdapter`，但相关接口仍标记为 Beta，应锁定版本并测试兼容。

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain



建议管理字段：

| 字段            | 用途             |
| ------------- | -------------- |
| MCP Server 名称 | 中文显示名称         |
| Transport     | HTTP / stdio 等 |
| 连接配置          | 服务器地址或启动参数     |
| 授权信息          | 加密保存，前端不回显     |
| Tools         | 自动发现的工具        |
| 使用范围          | 指定 Agent、用户或组织 |
| 审批策略          | 哪些工具操作需要人工确认   |

远程 MCP 必须限制可访问地址；stdio MCP 如果需要执行外部程序，应放在受限执行环境，而不是让用户任意配置宿主机命令。

### 3. Skills 管理

Deep Agents 原生支持包含 `SKILL.md` 的技能目录，并允许附带 scripts、references 和 assets 等资源。

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain



建议实现：

- 上传 Skill 目录或 ZIP。
- 解析 `SKILL.md` 名称与描述。
- 技能版本管理。
- Agent 与 Skill 绑定。
- 按需加载技能说明。
- Skill 权限与安全检查。

Skill 中的脚本默认仍需在沙箱执行，不能因为它属于 Skill 就获得更高权限。

### 4. 多 Agent 管理

建议第一版支持创建自定义 Agent，并配置：

`Agent = Model + Instructions + Tools + Skills + Knowledge + Permissions`

Work 模式可以将专业 Agent 作为 Deep Agents 子 Agent 调用。

例如：

- Coding Agent
- Browser Agent
- Document Agent
- Research Agent
- Data Analysis Agent

Deep Agents 已支持子 Agent 委派与上下文隔离，避免你自行搭建另一套完整的多 Agent 通信系统。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



### 5. Memory 记忆

建议明确区分：

| 类型         | 存储方式                         | 隔离           |
| ---------- | ---------------------------- | ------------ |
| 会话短期记忆     | LangGraph Checkpointer       | Thread       |
| 用户长期记忆     | LangGraph Store / PostgreSQL | User         |
| Agent 私有记忆 | Store                        | Agent + User |
| 组织共享知识     | Knowledge Base               | Organization |

长期记忆不等于 RAG 文档库。前者记录持续有用的上下文，后者维护可检索的知识资料。

LangGraph 官方也将 Checkpointer 与 Store 分为两种不同的持久化机制。

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain



### P5 验收标准

同一个知识库能被 Ask、Work、Workflow 调用；MCP Tool 和 Skill 可在中文管理界面绑定 Agent；不同用户和 Agent 的私有知识、记忆互不泄露；能够查看每次知识检索的来源。

交付成果：统一 Agent 能力管理中心。

# 第三部分：固定工作流与平台发布

## P6：可视化固定工作流引擎

目标：用户可以像 Dify、n8n 一样，在 Web 页面创建、调试和保存工作流。

工作流引擎使用 LangGraph，前端画布使用 React Flow。

React Flow 是 MIT 许可的开源节点编辑组件，可以商用和二开。它只负责画布和交互，实际执行仍由你开发的 Workflow Compiler 和 LangGraph 完成。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+1



### 1. 工作流编辑器界面

建议采用以下布局：

病历结构化工作流

草稿

节点库

开始

LLM

Agent

RAG

条件

HTTP

结束

开始

RAG 检索

LLM 生成

结束

节点配置

知识库

医疗知识库

Top K

5

超时

30 秒

示意布局，正式编辑器由 React Flow 实现拖拽与连线。

### 2. 节点系统

我建议分两批开发，避免第一版节点太多。

| 第一批节点       | 作用              |
| ----------- | --------------- |
| Start / End | 输入输出            |
| LLM         | 调用语言模型          |
| Agent       | 执行 Agent        |
| RAG         | 知识库检索           |
| Tool / MCP  | 调用工具            |
| HTTP        | 外部 API 请求       |
| Condition   | 条件分支            |
| Transform   | 变量映射及 JSON 数据转换 |

第二批再增加并行、循环、代码执行、人工审批、子工作流等节点。

注意，HTTP 节点不应允许任意访问平台内部网络；Transform 节点建议先提供安全表达式或预置转换器，不允许执行任意 Python 代码。

### 3. Workflow JSON 规范

前端画布数据要转成一份平台自己管理的工作流定义：

```
{
  "schema_version": "1.0",
  "workflow_id": "wf_001",
  "name": "病历结构化",
  "nodes": [
    {
      "id": "start",
      "type": "start",
      "config": {}
    },
    {
      "id": "llm_1",
      "type": "llm",
      "config": {
        "model_id": "model_001",
        "prompt": "根据输入生成结构化病历"
      }
    },
    {
      "id": "end",
      "type": "end",
      "config": {}
    }
  ],
  "edges": [
    {"source": "start", "target": "llm_1"},
    {"source": "llm_1", "target": "end"}
  ]
}
```

正式版本还需要输入输出 Schema、变量引用、权限、超时、重试及错误分支。

### 4. Workflow Compiler

你需要新增一个编译模块：

`Workflow JSON → 校验 → 节点注册表 → StateGraph → compile() → 执行`

编译时必须检查：

- 节点类型是否已注册。
- 是否具有合法的开始和结束路径。
- 输入输出类型是否匹配。
- 条件分支是否完整。
- 是否存在非法循环或不可达节点。
- Tool / Agent / Knowledge 是否有权限调用。
- 循环次数和执行预算是否有限制。

例如，`llm` 类型对应 `LLMNodeExecutor`，`rag` 对应 `RAGNodeExecutor`。

不要让前端提交 Python 代码，然后由后端直接执行生成的代码。 所有节点必须来自服务端可信的注册表。

### 5. 工作流调试

第一版至少支持：

- 输入测试参数。
- 执行整条工作流。
- 查看节点输入、输出、耗时与错误。
- 展示节点运行状态。
- 查看 LLM Token 消耗。
- 查看工具调用结果。

后续可加入节点级重试和检查点回放，但要避免重新执行已产生不可逆副作用的节点。

### P6 验收标准

用户无需修改 Python 代码，就能在中文画布创建一条工作流；流程可保存、重新打开、测试运行；条件分支正确；错误节点可定位；工作流不允许越权调用 Agent 或工具。

交付成果：固定工作流设计器与执行引擎。

## P7：工作流发布、外部 API 与平台化

目标：工作流不仅能在内部使用，还可以发布给其他人和第三方业务系统调用。

### 1. 工作流版本管理

建议采用：

`DRAFT → TESTED → PUBLISHED → DEPRECATED`

一个工作流可以有多个发布版本：

| 工作流   | 版本 | 状态  |
| ----- | -- | --- |
| 病历结构化 | v1 | 已发布 |
| 病历结构化 | v2 | 已发布 |
| 病历结构化 | v3 | 草稿  |

发布后版本不可原地修改，只能创建新版本；历史运行必须继续指向当时发布的版本。

### 2. 外部 API 设计

以下是建议的平台 API，不是 LangGraph 原生路由。

运行工作流

```
POST /api/v1/workflows/{workflow_id}/runs
Authorization: Bearer <API_KEY>
Idempotency-Key: <UNIQUE_REQUEST_ID>
Content-Type: application/json
```

```
{
  "version": "v1",
  "inputs": {
    "text": "患者主诉右下后牙疼痛两天"
  }
}
```

返回：

```
{
  "run_id": "run_001",
  "status": "PENDING",
  "workflow_id": "wf_001",
  "version": "v1"
}
```

其他接口

| API                                     | 功能       |
| --------------------------------------- | -------- |
| `GET /api/v1/runs/{run_id}`             | 查询状态和结果  |
| `GET /api/v1/runs/{run_id}/events`      | SSE 订阅进度 |
| `POST /api/v1/runs/{run_id}/cancel`     | 取消运行     |
| `POST /api/v1/runs/{run_id}/resume`     | 恢复运行     |
| `POST /api/v1/approvals/{id}/decisions` | 审批决策     |
| `GET /api/v1/workflows/{id}/versions`   | 查询版本     |
| `POST /api/v1/workflows/{id}/publish`   | 发布新版本    |

第三方 API 调用采用异步任务模型，尤其适合需要多步骤、浏览器操作或长时间执行的工作流。

### 3. 工作流可以给 Work 模式调用

建议把已发布的 Workflow 注册成 Tool。

例如：

> 用户要求 Work 批量处理 100 个病历文档。

Work 可以识别并调用已发布的“病历结构化工作流”，获取结果后继续完成下一步任务。

调用时要设置权限、并发和预算限制，防止工作流相互调用形成无限循环。

### 4. 平台管理

最后完善：

| 管理功能        | 内容                    |
| ----------- | --------------------- |
| 用户与组织       | 用户、组织、成员、角色           |
| Agent 管理    | 新建、配置、发布、授权           |
| Workflow 管理 | 版本、发布、下线、共享           |
| API Key     | 创建、撤销、作用域、过期时间        |
| 运行管理        | 状态、耗时、失败、重试           |
| 模型管理        | 模型提供商、密钥、配额           |
| 工具管理        | MCP、Skills、HTTP Tools |
| 成本统计        | Token、沙箱运行、调用次数       |
| 审计日志        | 用户、工具、执行操作记录          |

### P7 验收标准

- 工作流可以发布多个不可变版本。
- 外部系统可以凭 API Key 调用指定版本。
- 不同 API Key 只能调用授权的资源。
- 可以查询运行进度和最终结果。
- 支持撤销 Key、限流和调用日志。
- 不暴露用户会话、平台凭证和其他组织的数据。
- Work 可以把授权的工作流作为工具调用。

交付成果：具备第三方接入能力的 AI Agent 平台。

# 第四部分：关键工程设计

## 一、建议的数据库表

建议 PostgreSQL 的平台业务表按领域划分。

| 业务模块    | 表                                                |
| ------- | ------------------------------------------------ |
| 用户与组织   | `users`、`organizations`、`organization_members`   |
| Agent   | `agents`、`agent_versions`                        |
| 模型      | `model_providers`、`model_configs`                |
| Ask     | `conversations`、`conversation_messages`          |
| Work    | `work_tasks`、`work_task_steps`、`work_task_plans` |
| Work 运行 | `task_runs`、`run_events`                         |
| 沙箱      | `sandbox_instances`、`sandbox_sessions`           |
| 工作流     | `workflow_definitions`、`workflow_versions`       |
| 工作流执行   | `workflow_runs`、`workflow_node_runs`             |
| 知识库     | `knowledge_bases`、`documents`、`document_chunks`  |
| 扩展能力    | `tools`、`mcp_servers`、`skills`                   |
| 审批与权限   | `approval_requests`、`resource_permissions`       |
| 成果物     | `artifacts`                                      |
| 对外 API  | `api_credentials`、`api_call_logs`                |
| 审计      | `audit_logs`                                     |

这是一套目标数据模型，不建议 P0 就把所有表创建出来。各阶段只添加当期需要的表。

另外，LangGraph Checkpointer 会管理自己的检查点表，不需要你再次实现图状态的完整持久化逻辑。

## 二、统一运行事件协议

前端 Ask、Work、Workflow 都需要流式事件，但事件内容不同。

建议定义统一的事件信封：

```
{
  "schema_version": 1,
  "event_id": 1024,
  "run_id": "run_001",
  "thread_id": "thread_001",
  "event": "step.started",
  "timestamp": "2026-10-29T08:00:00Z",
  "data": {
    "step_id": "step_002",
    "title": "执行测试"
  }
}
```

核心事件：

| 事件                                | 含义        |
| --------------------------------- | --------- |
| `run.started`                     | 任务开始      |
| `message.delta`                   | 流式文本      |
| `plan.updated`                    | Work 计划更新 |
| `step.started`                    | 子任务开始     |
| `step.completed`                  | 子任务完成     |
| `tool.started` / `tool.completed` | 工具调用      |
| `approval.required`               | 等待人工审批    |
| `artifact.created`                | 新成果文件     |
| `run.completed`                   | 任务成功      |
| `run.failed`                      | 任务失败      |

建议使用 SSE 推送，事件持久化时为每条事件分配可重放的递增序号，并支持断线后从最后事件继续获取。事件持久化和重连逻辑由平台负责，不能仅依赖浏览器连接一直在线。

## 三、长任务调度与恢复

P0 和 P1 可以让 FastAPI 进程直接运行短任务。

从 P3、P4 开始，Work 可能运行较长时间，需要可靠调度：

FastAPI 接收请求

PostgreSQL 持久化任务

Worker 认领任务

租约 / 心跳 / 并发限制

LangGraph 执行 + OpenSandbox

记录事件、状态与成果物

第一版可以采用 PostgreSQL 任务表、租约和专用 Worker；业务增长后，再评估 Redis 队列、Celery、Temporal 等方案。

要特别注意：

- 进程重启后如何重新认领任务。
- 同一任务不能被两个 Worker 同时执行。
- 任务取消如何传递到正在运行的命令。
- Worker 崩溃时如何回收沙箱。
- 工具调用失败如何重试。
- 已经成功的外部写操作不能重复执行。

LangGraph 官方明确建议对有副作用的操作进行幂等设计，尤其是在使用持久执行和中断恢复时。

[image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

Docs by LangChain



## 四、权限与安全设计

平台可以将工具权限分成以下等级：

| 等级             | 能力            | 默认策略     |
| -------------- | ------------- | -------- |
| Read           | 知识检索、已授权文件读取  | 自动执行     |
| Compute        | 受限计算、数据分析     | 允许或按配额执行 |
| Write          | 修改工作空间文件      | 按任务授权    |
| External Write | 发送消息、修改外部业务数据 | 人工审批     |
| Admin          | 修改权限、密钥、平台配置  | 管理员授权    |

同时防范提示词注入、恶意文件、恶意网页、SSRF、命令越权、密钥泄露、跨租户数据访问等问题。

沙箱只能作为执行隔离的一环，不能替代权限系统。

本地 Docker 沙箱适合你自己做开发和验证。将来允许不可信的外部用户执行任意代码时，要考虑独立执行主机和更强的隔离运行时。

# 第五部分：开发顺序、资源和验收

## 一、哪些功能先做，哪些暂时不做？

| 功能              | MVP 必须 | 完整版     |
| --------------- | ------ | ------- |
| Ask 多模型聊天       | 是      | 完善      |
| 会话持久化           | 是      | 完善      |
| Work 动态规划       | 是      | 完善      |
| Work 代码执行       | 是      | 完善      |
| Work 浏览器操作      | 是      | 完善      |
| 沙箱隔离            | 是      | 强化      |
| 动态重规划           | 是      | 完善      |
| 任务审批与恢复         | 是      | 完善      |
| 成果物下载           | 是      | 完善      |
| RAG 知识库         | 基础版    | 多数据源与权限 |
| MCP / Skills 管理 | 基础接入   | 中文管理中心  |
| 可视化 Workflow    | 否      | 是       |
| 工作流 API 发布      | 否      | 是       |
| 多租户完整管理         | 否      | 是       |
| 原生 A2A          | 否      | 后续扩展    |
| Workflow 模板市场   | 否      | 后续扩展    |

我不建议你在首个版本里开发计费系统、工作流市场、复杂组织管理、Kubernetes、完整桌面远程控制。这些会明显拖慢真正有价值的 Work 功能。

## 二、各阶段完成后的产品能力

| 完成阶段 | 你得到什么                  |
| ---- | ---------------------- |
| P0   | 可运行的项目骨架               |
| P1   | 可用的 AI Ask 聊天          |
| P2   | AI 可以安全执行代码与浏览器操作      |
| P3   | 具备动态任务能力的 Work 原型      |
| P4   | 可暂停、恢复、审批的 Work MVP    |
| P5   | 统一知识库、Agent、MCP、Skills |
| P6   | 中文固定工作流设计器             |
| P7   | 可对外发布和调用的完整平台          |

### 推荐的两个里程碑

里程碑 1：Work MVP

完成 P0–P4

用户可以输入自然语言任务，让 AI 自主拆解、修改代码、操作浏览器、生成文件，并在必要时进行审批、暂停和恢复。

里程碑 2：AI Agent 平台

完成 P5–P7

支持知识库、Agent、Skills、MCP、可视化固定工作流及 API 发布，形成可复用、可扩展的平台。

## 三、你的电脑部署建议

你的 i5-14600KF、32GB RAM、RTX 5060 Ti 16GB 可以先完成 P0–P7 的本地功能开发，但不要把“本地可运行”理解为“能够同时支撑大量用户”。

建议本地开发阶段保持以下配置：

| 资源              | 建议                          |
| --------------- | --------------------------- |
| WSL2 RAM 上限     | 18GB                        |
| WSL2 处理器上限      | 10 个逻辑处理器                   |
| Coding Sandbox  | 4 vCPU / 5GiB               |
| Browser Sandbox | 2 vCPU / 3GiB               |
| 并发沙箱            | 最多 2 个                      |
| LLM             | 优先云端 API                    |
| GPU             | 暂时留给本地 Embedding / Reranker |
| 持久化             | PostgreSQL                  |
| Docker          | WSL2 + Docker Desktop       |

P0–P4 不需要 Kubernetes。等需要公网多用户、沙箱资源池、动态扩容时，再考虑把 OpenSandbox 迁移到 Linux 服务器或 Kubernetes。

## 四、建议采用的开发规范

为了让后续使用 Cursor 或 Codex 辅助二开时不容易失控，建议固定以下规则：

- 一次开发只处理一个阶段或一个明确功能。
- 优先扩展官方接口，不直接修改第三方框架内部源码。
- 不随意升级依赖，统一使用锁文件。
- 每个 API 必须有明确输入输出 Schema。
- 每个任务状态变更必须记录事件。
- 所有文件和命令操作必须经过沙箱适配层。
- 所有跨用户数据操作必须有后端权限校验。
- 只验证当前修改模块，不默认运行全量构建。
- 复杂功能先实现单用户可用，再扩展并发和租户隔离。

测试方面，建议每个阶段至少包含单元测试、组件集成测试和一组真实业务场景测试。

正式对外开放前，再增加鉴权渗透测试、工具越权测试、沙箱逃逸风险评估、并发压力测试、故障注入和数据库备份恢复演练。

## 六、优先参考的官方项目与文档

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

[LangGraph](https://github.com/langchain-ai/langgraph?utm_source=chatgpt.com)

统一状态图、工作流和检查点运行引擎

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

[Deep Agents](https://github.com/langchain-ai/deepagents?utm_source=chatgpt.com)

Work 自主执行 Harness、Skills、子 Agent

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

[Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui?utm_source=chatgpt.com)

Next.js 聊天界面基础工程

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

[Deep Agents UI](https://github.com/langchain-ai/deep-agents-ui?utm_source=chatgpt.com)

额外参考其文件展示和调试界面，不替换既定前端

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

[OpenSandbox](https://github.com/opensandbox-group/OpenSandbox?utm_source=chatgpt.com)

沙箱生命周期、编程、浏览器与文件执行

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

[React Flow](https://github.com/xyflow/xyflow?utm_source=chatgpt.com)

固定工作流可视化画布

## 七、最终开发决策

## 建议从 P0 开始，优先交付 P0–P4

P0：工程初始化与前后端协议验证

P1：Ask 多模型聊天和会话管理

P2：OpenSandbox 编程与浏览器执行

P3：Deep Agents 动态 Work 模式

P4：长任务恢复、人工审批和成果物

P5：RAG、MCP、Skills 和 Agent 管理

P6：React Flow 工作流设计与调试

P7：工作流发布、第三方 API 和平台化

展开选中阶段的详细开发设计&#x20;

我认为这套产品真正的核心竞争力不是聊天功能，也不是简单拖拽工作流，而是 Work 的自主执行能力。

所以建议以 P0 → P1 → P2 → P3 → P4 为第一条主线，让 AI 先真正具备持续执行任务、调用工具、操作工作空间和验证成果的能力。然后再将这些能力沉淀成固定工作流、知识库、Skills 和对外 API。

这样最终得到的是一个既能像 Cursor 一样自主工作，又能像 Dify 一样编排、发布和复用工作流的统一 AI Agent 平台。