# 知行平台统一需求基线

版本：1.1（2026-11-02，保留1.0冻结正文并追加扩展入口）。产品经理整理；项目 `D:\project\my_agent\deepagents`，分支 `onwer_agent`。本文件整合已有需求，不重新发明另一套平台；原专题文档保留设计和历史证据，旧的“待开发”不覆盖后续真实实现。

**上一SYS批次状态（本机验收完成）**：冻结时的两个实现缺口SYS-072整套生产部署、SYS-074完整管理分页已完成开发、自验并部署；全后端、整站页面、真实审批跨重启、备份隔离恢复、Git隔离检出及最终数据核对均已执行通过，实际数量与边界见[2026-11-02系统验收记录](system-acceptance-2026-11-02.md)。下文P/A标签及第3节保留该批冻结核对事实，不表示上述两个缺口仍未开发。

**当前扩展批次（不能套用上批完成结论）**：用户已继续授权SYS-081中的身份、工具、图视频等扩展，以及监测大盘、费用对账、质量门禁和拓扑容量，细化为[15项EXP完整需求](platform-expansion-requirements.md)。原SYS-081的X是历史范围，不能据此把本次已授权需求排除。实施、自验、独立审查、QA按[计划](platform-expansion-development.md)、[审查总账](platform-expansion-code-review.md)和[67条扩展用例](platform-expansion-test-cases.md)执行。单轮完整后端596通过，后续内部回调/备份实例保护另有审查与定向复测；默认页面101通过/2隐藏跳过，另3项原生流程及中文提示通过，最终完整生产构建、监控、隔离恢复和数据保护核对已完成，实际版本范围见[扩展交付记录](platform-expansion-acceptance-2026-11-02.md)；真实外部效果按[新增接入清单](platform-expansion-external-checklist.md)待接入，不能用旧458/77或Demo声称当前全批已验收。独立语音/图片理解导航继续隐藏。

## 1. 范围、状态与取舍

现有架构：LangGraph + Deep Agents + Agent Chat UI来源组件 + OpenSandbox；中文界面，私有资源与服务端权限；Windows本机部署。原数据、加密密钥、已发布快照及未提交改动保留，不提交或推送。本轮流程为：产品冻结需求 → 开发实施和自验 → 独立代码审查（失败修复并复审）→ 测试回归 → 使用运维脚本部署验证；无依赖工作并行，新增修复仍按此门槛重新交接。

需求源：[最初实施计划](development-plan.md)、[八阶段路线](agent-platform-roadmap.md)、[剩余阶段历史](remaining-stages-execution.md)、[交付记录](final-delivery-2026-11-01.md)、[最新增量记录](implementation-verification.md)。`development-status.md` 明确为早期历史，其中“Work/OCR/重排/工作流待开发”等已由较新证据覆盖，不能再次列成未开发。

状态含义：

- **V 已实现、有本机证据**：源码与已记录本机验证相符；不代表本轮全系统回归已运行，也不代表真实服务效果通过。
- **A 已实现、仍待指定验证**：缺少特定故障/兼容性或当前版本全量证据，测试必须补证或保留待验。
- **P 已授权、实现未完整**：本轮要开发，有明确原需求依据和缺口；不能改名为“等用户配置”。
- **E 外部接入/效果待验**：协议工程有证据，但真实模型、网站、通知、GitHub或账单待用户接入；本轮继续使用隔离库/协议替身，不使用已有密钥补付费联调。
- **H 用户明确隐藏**：不出现在独立导航，相关能力仍保留。
- **X 未纳入已承诺范围**：原文“按需考虑”的扩展；不得假装已实现，也不因本次整理自动增加范围。

V项验收基础均包含：正确权限＋私有归属、非法输入拒绝、失败不伪装成功、持久数据刷新/重启可见、未知外部操作不自动重放、日志/响应无凭证。涉及写操作必须检查撤权、审批摘要与幂等；涉及历史必须保留旧消息/文件/修订。模块项以下给出特别验收点；完整测试用例需逐SYS编号映射。

## 2. 完整功能需求

### 账号、安全与组织

- **SYS-001 账号生命周期〔V〕**：注册策略（开放/审核/关闭）、审核/拒绝、邀请码、启停账号、本人改密、管理员重置、首次强制改密、会话撤销。验收：邀请码不可重复使用，停用/改密后旧会话失效，最后管理员受保护。证据：`account-permissions-plan.md`；`account_api.py`、`test_accounts.py`、`accounts.spec.ts`。
- **SYS-002 功能角色与运行授权〔V〕**：预设/自定义角色，查看/配置/执行分离，运行调用前复核权限；隐藏按钮不能代替后端校验。验收：无权限直接API仍拒绝，运行中撤权阻止后续模型/工具调用。证据：`permissions.py`、账号及工作执行集成测试。
- **SYS-003 组织和共享〔V〕**：组织成员角色、不可变Agent/工作流版本共享和受限运行；私有资源不自动变组织公共。验收：所有者保护、失败更新不改成员、撤销共享及跨组织隔离。证据：`organization_api.py`、`test_organizations.py`、`management-lists.spec.ts`。
- **SYS-004 认证与数据安全〔V〕**：服务端会话、HttpOnly Cookie、来源/CSRF校验、密码安全哈希、密钥加密、路径/地址/大小限制。验收：越权读取/下载/订阅失败，编辑密钥留空保留、明确清除才删除，前端不回显。证据：`security.py`、`models.py`、`test_api.py`及资源隔离测试。

### 模型、联网、路由与费用

- **SYS-010 页面模型配置〔V/E〕**：十类用途、服务商与真实协议分开配置，新增/编辑/启停/默认/筛选/连接测试；不把服务商列表当成全部原生协议已适配。验收：用途隔离、默认唯一、密钥保留/清除、真实协议探测，不支持组合明确拒绝。证据：`model-types.md`、`model_api.py`、`model_capabilities.py`、`test_model_types.py`、platform模型用例。
- **SYS-011 多模态协议能力〔V/E/H〕**：文本向量、重排、已适配图片理解/生成、音视频理解、语音识别/合成、多模态向量的真实协议路径；图片结果按现有权限存取，供应商限制明确。验收：不能把所有类型按聊天接口检测；维度/格式/大小/来源变化拒绝混用。证据：`multimodal-integration-plan.md`、`test_vision.py`、`test_speech.py`、`test_multimodal.py`；独立页面展示服从SYS-080。
- **SYS-012 联网能力〔V/E〕**：已适配模型原生联网或Agent授权Tavily搜索；引用有来源；未配置不假称已搜索。验收：原生工具参数、工具权限、取消与未知结果行为；真实即时来源质量待U02/U16。证据：`agent-workspace-plan.md`、`test_native_search.py`、`test_ask_images_search.py`。
- **SYS-013 模型路由〔V/E〕**：显式有序候选、接收方同意、能力条件、修订固定、冷却及恢复探测。验收：发送后未知不转发重放，改配置重新同意，实际选中模型可追溯。证据：`routing-billing-operations-plan.md`、`model_routing.py`、`test_model_routing.py`、`routing-billing.spec.ts`。
- **SYS-014 价格与金额预算〔V/E〕**：不可变价格版本、输入/输出/缓存分项、CNY/USD分开预算、预留/结算、迟到幂等用量及未知冻结。验收：并发不超预留，未知不填零，金额估算不冒充账单，额外服务费另说明。证据：`money_api.py`、`test_money.py`、路由费用计划；U15待真实账单。

### 智能对话与Harness

- **SYS-020 会话工作空间〔V〕**：新建/重命名/删除、私有项目、置顶、标题/正文搜索、归档恢复、历史与消息分页。验收：特殊字符、翻页、跨账号隔离，删项目不删会话，运行中限制合理。证据：`conversation-sidebar.tsx`、`test_api.py`、`test_attachments.py`、`workspace.spec.ts`。
- **SYS-021 流式与持久Ask〔V/A〕**：持久入队、独立Worker、租约/心跳、SSE增量/重放、取消、检查点与重启继续。验收：重复事件去重、部分输出保留、取消不被成功覆盖、租约失效不重放未知工具。证据：`test_ask_worker.py`、`test_ask_progress.py`、`remaining-stages-execution.md`；当前版本故障演练按SYS-075补证。
- **SYS-022 对话附件〔V/E〕**：文档与图片上传/粘贴/删除/预览，能力不匹配提示，刷新后可读；资源私有。验收：类型/大小限制、恶意文件、跨账号下载、图片模型选择与外部协议边界。证据：`test_attachments.py`、`test_ask_images_search.py`、platform实际图片用例。
- **SYS-023 中文会话体验〔V〕**：ChatGPT式会话区、折叠过程与独立最终回答、真实用时、仅最终回答操作栏；底部跟随/上翻暂停/返回最新、历史阅读锚点、中文输入法保护。验收：连续片段保序、迟到请求不串会话、390px和键盘操作。证据：`chat-turn-layout.md`、`chat-fluidity-plan.md`、`chat-message-actions.spec.ts`、`chat-fluidity.spec.ts`。
- **SYS-024 统一Harness〔V/E〕**：Ask/Work复用Deep Agents循环、执行策略、预算、上下文压缩、重复无进展停止与诊断，不嵌入第二层模型循环。验收：预算竞争、循环失控、有副作用前停止、真实工具结果与未知结果区分。证据：`harness-integration-plan.md`、`harness.py`及Harness单元/集成测试。
- **SYS-025 Token核算〔V/E〕**：每轮输入/输出/累计、预计输入和输出预留、缓存/推理分项及未报告提示。验收：估算与服务商实报区别、迟到幂等、失败/取消/团队/流程统一账本。证据：`usage-diagnostics-plan.md`、`usage_api.py`、`test_usage.py`、platform本机Harness用例。
- **SYS-026 分支与授权引用〔V〕**：从消息或编辑问题创建独立分支，独立附件/草稿/检查点，显式授权引用其他会话。验收：原历史不变，来源撤销/删除不越权，已发生操作不伪称撤销。证据：`conversation-references-plan.md`、`test_conversation_branches.py`、`conversation-references.spec.ts`、`workflow-branches.spec.ts`。

### Agent、协作及主动任务

- **SYS-030 Agent配置与版本〔V〕**：独立模型、提示词、知识/工具/技能/记忆/联网范围及发布快照。验收：编辑不改变已发布版本、运行前资源有效性复核、私有资源授权。证据：`agent-workspace-plan.md`、`agent_api.py`、`agent_versions.py`、`test_agent_versions.py`。
- **SYS-031 团队与批次〔V/E〕**：角色/目标/模型/成员配置，单层只读委派、上限并行、共享父预算、角色Token、持久批次和独立取消。验收：嵌套/总量限制、撤权、故障及取消传播、跨重启；真实协作质量另验。证据：`agent-teams-plan.md`、`team_api.py`、`test_agent_teams.py`、`teams.spec.ts`。
- **SYS-032 自动任务〔V/E〕**：一次性/间隔/带时区Cron、签名事件、幂等入队、错过/忙碌策略、启停/历史；对话工具创建暂停草稿并由卡片显式启用。验收：不自动扩大写权限、自动来源不能无限自建、重启补偿/多Worker竞争/撤权。证据：`proactive-tasks-plan.md`、`task-entry-and-list-plan.md`、`automation_api.py`、`test_ask_automations.py`、`automations.spec.ts`、`task-entry.spec.ts`。
- **SYS-033 通知〔V/E〕**：站内记录及四类外发协议、凭证加密与投递状态，失败隔离。验收：任务和通知结果分开、未知写入不盲重发，真实群/通道须U12。证据：`test_automation_notifications.py`、`automations.spec.ts`。

### Work、浏览器与仓库

- **SYS-040 持久任务〔V〕**：任务草稿/计划/步骤/进度、独立队列、暂停/继续/取消、跨重启配额及孤儿回收。验收：任务与环境归属、租约防旧实例写入、撤权/预算、未知执行核查。证据：`work_task_api.py`、`work_run_api.py`、`test_work_worker.py`、`test_work_recovery_real.py`。
- **SYS-041 本机执行环境〔V/A〕**：Windows OpenSandbox管理、Coding/Browser镜像、资源配额、环境分配/回收。验收：真实本机代码/浏览器运行、内存/并发限制及服务状态；不能以本机两Worker结果承诺生产容量。证据：`test_sandbox_service.py`、`test_work_backend_real.py`、`scripts/start-opensandbox.ps1`。
- **SYS-042 操作审批与恢复〔V〕**：批准/拒绝/允许范围内改参，摘要固定、单次执行、恢复后重新授权。验收：旧摘要/旧租约拒绝，未知操作不重放，已完成步骤不再执行。证据：`test_approval_edits.py`、`test_work_pause.py`、`test_work_execution.py`。
- **SYS-043 文件与成果〔V〕**：受限工作区路径、文件预览/差异/下载、快照/摘要、安全恢复。验收：穿越/符号链接/跨账号拒绝，命令与成果证据不能由模型自称成功替代。证据：`test_work_inputs.py`、`test_work_execution.py`、`test_workspace_backup.py`。
- **SYS-044 浏览器工作台〔V/E〕**：标签页、访问/点击/输入/滚动/截图/上传下载、同页审批恢复、实时视图、暂停接管、按任务加密登录状态、关闭/清除。验收：域名授权、私有状态隔离、未知操作核查；真实网站登录待U13。证据：`browser-workbench-plan.md`、`browser_api.py`、`browser-workbench.spec.ts`。
- **SYS-045 仓库工作区〔V/E〕**：私有连接、固定SHA/子目录检出、任务分支、差异/删除/模式/命令证据及Git bundle恢复。验收：凭证不进入上下文/成果、不下载未授权内容；真实GitHub待U14。证据：`repository-workspace-plan.md`、`repository_api.py`、`test_repository_workspace_real.py`、`repository-native.spec.ts`。
- **SYS-046 Git外部协作〔V/E〕**：分别批准任务远端分支与草稿PR，未知结果只读核查，Issue/CI反馈持久。验收：无授权不推送、不覆盖默认分支、不自动合并；本项目仍不提交推送。证据：`test_repository_actions.py`、`test_repository_git_protocol.py`、`repositories.spec.ts`。

### 知识、工具、技能与记忆

- **SYS-050 知识库界面〔V〕**：列表搜索/数量/新增，文档优先、六页签和独立OCR、上传真实状态、筛选、失败恢复、只读和窄屏。验收：按需加载、切库隔离、无假进度/假统计。证据：`knowledge-center-requirements.md` KB-01～12、`knowledge-workspace.tsx`、`knowledge-center-ui.spec.ts`。
- **SYS-051 证据检索〔V/E〕**：关键词/文本向量混合/重排，分数与来源、弱证据过滤、无证据状态、降级说明。验收：不得把分数当答案置信度，不屏蔽全部结果刷指标，向量纯语义不被关键词门槛强删。证据：`test_retrieval_evidence.py`、`test_rerank.py`、`test_capabilities.py`；真实效果SYS-076未验收。
- **SYS-052 文档生命周期〔V〕**：TXT/MD/PDF/DOCX/XLSX、4MiB限制、分块策略、去重、增量更新、修订/分块分页、重索引/删除。验收：失败更新保留原修订、重复不重复计费、历史查看不触发模型、Excel公式不执行。证据：`knowledge_documents.py`、`test_knowledge_documents.py`、`knowledge-operations.spec.ts`。
- **SYS-053 OCR〔V/E〕**：图片/扫描件页范围、草稿、原图校对、确认导入和状态/用量。验收：不把未校对内容自动当知识、重复/失败/取消/来源隔离；真实识别质量待U03/U04。证据：`knowledge_ocr.py`、`test_knowledge_ocr.py`、`knowledge-operations.spec.ts`。
- **SYS-054 评测与反馈〔V/E〕**：私有题集、来源/证据标注、检索实验/预算/逐题/分类/对照、本人回答反馈。验收：题集或设置不同不可假装公平对照，检索指标不是生成引用正确率。证据：`evaluation_api.py`、`test_rag_evaluation.py`、`test_message_feedback.py`、`knowledge-operations.spec.ts`。
- **SYS-055 MCP〔V/E〕**：Streamable HTTP、加密凭证、连接/发现、启用、只读工具，管理员Work写工具逐次审批。验收：地址/工具/schema/绑定/撤权、Ask不能写、凭证不回显；真实业务MCP待U08。证据：`test_mcp_wire.py`、`test_mcp_writes.py`；stdio/旧SSE/OAuth见SYS-081，不宣称已支持。
- **SYS-056 Skills〔V/E〕**：SKILL.md/ZIP导入、版本、启停/预览、按授权读取资源及任务内审批脚本。验收：路径穿越/符号链接/宿主文件拒绝、脚本不在宿主执行、撤权和版本隔离。证据：`test_skill_revocation.py`、`test_work_resources.py`、能力中心页面回归。
- **SYS-057 长期记忆〔V/E〕**：手工和授权会话候选、来源、确认/拒绝/编辑、去重/冲突/到期、账号/项目/Agent范围、可选语义召回。验收：外部资料不是持久系统指令，来源删除/撤权可追溯，跨账号不召回。证据：`memory-knowledge-operations-plan.md`、`memory_api.py`、`test_memory_governance.py`、`test_memory_vectors.py`、`memories.spec.ts`。
- **SYS-058 图片知识〔V/E〕**：独立图片向量模型、图片上传/持久索引/文本搜图；绑定模型/维度固定。验收：模型/维度改变拒绝混用、图片/成果私有，未绑定不伪装可查；不承诺图搜图/视频向量。证据：`multimodal_api.py`、`test_multimodal.py`、platform能力中心回归。

### 工作流

- **SYS-060 工作流画布〔V〕**：列表创建、全幅画布、节点库、增删改/复制/连线/布局、草稿保存、调试、版本发布及历史快照。验收：未配置草稿可保存但不可整体执行/发布，修订冲突阻止覆盖，旧结果不贴到新图。证据：`workflow-studio-plan.md`、`workflow-editor.tsx`、`test_workflow_studio.py`、`workflow-studio.spec.ts`。
- **SYS-061 输入输出Schema〔V〕**：中文类型/必填/范围、递归对象/数组、JSON往返，发布Schema固定。验收：非法输入执行前拒绝，发布不随草稿改变，390px无溢出。证据：`workflow-forms-plan.md`、`workflow_schema.py`、`test_workflow_forms.py`、`workflow-forms.spec.ts`。
- **SYS-062 十五类节点及复合执行〔V〕**：开始/结束、LLM、Agent、RAG、MCP、HTTP、条件、预置转换、子流程、迭代、并行、条件循环、审批、Python；根预算和确定性错误出口。验收：节点/模型/Token共享上限、循环上限、分支和变量可用性、资源撤销；不对未知外部失败自动重试。证据：`workflow_definition.py`、`workflow_runtime.py`、`test_workflow_composition.py`、`test_workflow_loops.py`、`workflow-loops.spec.ts`。
- **SYS-063 流程代码/人工审批〔V〕**：持久任务固定图、检查点、固定代码摘要批准、暂停/重启恢复。验收：前置节点不重放，代码确在任务环境执行，修改后旧批准失效。证据：`workflow-approvals-code-plan.md`、`workflow_work.py`、`test_workflow_tasks.py`及跨重启页面用例。
- **SYS-064 单节点调试〔V〕**：每节点卡片/配置入口、显式主输入和MCP额外变量、输入复用、连续重测、输入/输出/耗时/错误/分支/状态。代码审批走独立任务。验收：原图其他节点不执行、重复编号不重复任务、成功不解锁整图发布。证据：`workflow-node-debug-plan.md`、`test_workflow_node_debug.py`、`workflow-node-debug.spec.ts`。
- **SYS-065 流程调用与发布〔V/E〕**：受限外部API Key、不可变发布版本、审计/限流；Ask仅调用授权只读版本。验收：过期/撤销/资源变化拒绝，外部不能借流程绕过审批或写工具。证据：`external_workflows.py`、`test_external_workflows.py`、`test_ask_workflows.py`。

### 运营、可靠性及本轮缺口

- **SYS-070 服务运行保障〔V/A〕**：API/Ask/Work/Ops独立后台、数据库连接/维护锁隔离、守护、安全退出和真实状态。验收：四后台就绪、数据库断连时旧实例停止，恢复不重放未知请求。证据：`service_api.py`、`test_service_host.py`、`test_service_fence.py`、`service-status.spec.ts`；当前版本综合回归待本轮完成。
- **SYS-071 备份与隔离恢复〔V/A〕**：离线一致性备份、未提交源码归档、凭证/附件/修订/Git恢复、定时和有限轮转、容量。验收：备份需完整manifest和校验；活动任务延期；恢复副本不得自动执行旧请求。证据：`backup.py`、`test_workspace_backup.py`、`test_operations_jobs.py`、`operations-backup-native.spec.ts`及2026-11-01真实恢复记录。本轮新脚本回滚演练另记录，不能用旧证据代替。
- **SYS-072 整套生产部署/升级/恢复脚本〔P，P0〕**：本轮用户明确要求交付给开发/测试的一站式入口，覆盖依赖/基础设施/环境镜像、备份/迁移、生产Web、四后台、健康检查、失败恢复和可追溯发布。冻结核对时已有启动脚本和Web专用部署，但`start-platform.ps1`仍启动next dev，没有等价完整生产升级入口。验收：正确工作区/分支/进程归属，失败不继续迁移/假成功，不覆盖凭证或用户数据，隐藏启动，记录恢复边界与实测结果。
- **SYS-073 官方LangGraph开发服务协议基准〔A，可选兼容性〕**：原P0要求官方线程/流/中断/恢复对照，后在U11列为可选且不影响本平台使用；现有平台协议测试不能替代。冻结时未发现完成证据，本轮可用确定性本机服务补线程、消息/工具事件、interrupt/resume、状态/历史、取消基准；若不执行则准确保留未验，不伪装业务缺陷或真实模型效果。证据源：`development-plan.md` P0.3、`integration-checklist.md` U11。
- **SYS-074 管理资源列表可达性〔P，P1〕**：原计划§5接口约定采用列表分页。冻结代码核对：`work_task_api.py`和`workflow_api.py`管理列表固定LIMIT100、`repository_api.py`固定LIMIT100，工作流运行历史及`work_run_api.py`任务运行历史LIMIT50，均无next游标；前端只筛这批，较旧资源不可达。必须补任务/工作流/仓库三管理列表及两类运行历史的续页和稳定顺序，支持完整资源搜索或明确当前搜索范围。验收：超过100条/50次仍可进入旧记录，不重复/串号，非法游标拒绝，追加/刷新与选择状态不混淆。账号/记忆已有offset分页，不必为形式统一推倒重写；明确标为“最近N条”的观测窗口可有上限，但不能伪装全部统计或完整管理列表。自动任务现有每账号最多20个未删除任务的服务端约束，因此列表LIMIT100不导致101个有效任务被截断；批次和触发记录LIMIT100须在开发审计时明确最近窗口语义，若界面宣称完整历史则补续页，不借此放开已有配额。
- **SYS-075 故障和重启证据〔A〕**：持久队列/恢复代码和历史演练已存在，但测试需为本轮最终版本执行受控重启、租约竞争/失效、审批恢复、服务状态检查；真实强杀/未知外部请求不一定可安全自动重做。验收记录应注明实际执行的故障类型、替身调用次数和未演练项，而不是“重启后200”即算全部通过。证据：`test_work_recovery_real.py`、`test_ask_worker.py`、历史Ask/流程重启记录。
- **SYS-076 真实业务质量〔E，未验收〕**：真实50题Recall@10≥90%、引用正确率≥95%以及无答案拒答/成本/语义记忆/复杂OCR/路由收益/账单核对。当前合成关键词默认Recall@10约93.33%、无答案检索60%，不能证明真实达标；实际语料到位后仍可能需要算法开发，不能说只填密钥便完成。证据：`final-delivery-2026-11-01.md`、`integration-checklist.md` U01～U16。

### 明确的隐藏与非承诺扩展

- **SYS-080 独立媒体入口〔H，已实现〕**：按用户最新要求隐藏“语音合成”“图片理解”两个导航，保留组件/接口/模型用途/数据和对话图片、知识库OCR/图片资料。验收：桌面/390px含隐藏DOM均无独立入口，保留路径可到达。证据：`knowledge-center-requirements.md` KB-13、中文modes列表、`knowledge-center-ui.spec.ts`。两个旧独立媒体页面用例暂时skip是范围决定，不是2个未开发功能。
- **SYS-081 后续按需扩展〔X，未实现〕**：OAuth/SSO/MFA、邮箱/短信找回、MCP stdio/旧SSE/OAuth、ACP，以及图搜图/视频向量、任意供应商全部原生协议。依据：八阶段路线与最新交付明确“按需要另定”、专题能力边界明确不支持。本轮不自动引入，但界面/文档不得暗示已支持；如果后续明确纳入，追加独立SYS编号和安全/运维验收，不挤占已承诺缺口。

## 3. 已确认未完成清单与开发顺序

1. **P0 / SYS-072**：整系统生产部署脚本和失败恢复交接；用户本轮明确新增要求，运维优先交付，开发/测试共用。
2. **P1 / SYS-074**：固定首批导致管理记录不可达，开发补服务端分页/搜索及页面续页；先解决真实功能缺口，再跑完整回归。
3. **证据补齐 / SYS-070、071、075**：最终版本的部署、故障/恢复证据，不重复开发已经存在的Work/审批/画布实现。安全上不能演练的部分单独登记。SYS-073按已确认的可选边界保留待验，本轮不新增官方服务器依赖。
4. **外部待接 / SYS-076及各V/E项**：按U01～U16接入清单逐项核对，继续跳过真实外部调用；效果不达标时再形成明确算法修复项。

未将MCP stdio/OAuth和SSO等另定扩展混入本轮必做。也未把早期“未做OCR/工作流/审批/Skills脚本/仓库”的旧状态误当新缺口。这些结论是冻结时的需求与代码核对，不是本轮已开发完成声明。

## 4. 本轮交付与验收门槛

- 产品：本总需求是可追踪基线；已有中文UI规范和知识库交互Demo继续有效，新增分页不以大改UI为由删除既有能力。
- 开发：先写与SYS-072/073/074关联的实施文档，保留兼容API语义、现有ID/密钥/历史；自行执行有意义测试，开发完成后通知测试角色。
- 测试：按全部SYS编号写完整用例与覆盖映射，区分现有自动化、需新增、手工本机、外部待接；等开发停止修改后执行最终版本完整后端与整站前端，再按需要补原生恢复/重启用例。失败修复后复跑受影响范围，不能把跳过、旧结果或定向次数相加冒充全量。
- 运维：提供脚本、先决条件、变量来源、启动/停止/状态、生产部署/备份/升级/回滚步骤，进程/路径归属校验与退出码检查；不得输出凭证、删除用户数据或自动推送本项目。
- 最终记录必须包含：实际通过/失败/跳过数量和原因、真实服务未测项、脚本执行与健康检查、数据/主密钥保留核对、构建与静态问题原样登记。当前全量需重新运行，历史423后端、旧45前端及后续专题定向结果只作旧证据，不直接充当本轮结论。

## 5. 冻结与变更控制

此版本由产品经理在代码/文档核对后冻结。开发若发现新的同类缺口或无法按现有接口安全满足需求，应反馈确切行为、文件与影响，先追加需求/验收边界再实现；不得默默把必做项改为未来工作。实现与测试状态由本轮开发/验收报告逐项更新；本需求保留冻结时缺口事实，避免完成后倒写历史。

## 6. 当前统一交付门槛

按用户新增要求：开发与自验 → 独立代码审查 → 修改问题 → 独立复审通过 → QA验收；QA发现新Bug同样退回修复和复审再复测。全站集成差异也须审查。产品、开发、测试、运维分别维护工作区与交接证据，仅上下游验收等待直接依赖，不要求全部人员串行等待。任何未关闭缺陷、未完成工程或未接入的外部效果均在当前报告明示，不以“全部无Bug”替代可复核结果。


## 智能对话 SSE 本轮范围纠正

按用户澄清，本轮仅增强 SYS-021 智能对话的 SSE 增量、快照与重连。任务工作台及工作流的本轮 SSE 扩展已按开发前源码精确撤回，原功能保留。专项开发、独立审查、隔离 QA 与本机发布记录见 [智能对话 SSE 验收](ask-sse-acceptance.md)，不将旧全域实验算作当前交付。


## 工作流增强本轮交付

参考Dify完成WF-D01～08：场景模板、节点搜索、配置诊断定位、必经变量与受限模板、运行变量复用及可撤销画布编辑。需求与Demo、开发、独立审查、分层QA及当前数据保留核对见[工作流增强验收](workflow-dify-acceptance.md)。仅Ask使用SSE的范围保持；本轮工作流沿用同步200与原运行查询。

## 平台习惯元技能本轮交付

按用户澄清在知行平台实现习惯学习，而非个人Codex安装。显式开启后从未来真实Ask消息积累六类明确通用偏好，三条独立证据生成私有Skill；获准父元技能的智能体受控列出/读取，Work只查用。包含纠正采纳、来源、暂停恢复、遗忘和新消息重新学习，不改旧发布资源快照或扩大执行授权。开发、独立审查、86项后端/15项前端与18个实际浏览器场景、079迁移、本机发布和当前数据密钥核对见[习惯元技能交付记录](habit-skill-acceptance.md)。真实外部模型效果按原要求暂不联调。
