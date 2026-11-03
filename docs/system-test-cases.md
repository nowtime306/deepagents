# 全系统测试用例与回归执行手册

版本：1.2，2026-11-02。测试角色根据产品冻结的[统一总需求](system-requirements.md)编写。本文件的TC编号稳定，原专题测试文档和历史通过数字只作证据，不冒充本轮全量结果。

## 测试基线与执行门槛

项目为D:\project\my_agent\deepagents，onwer_agent。开发自验和冻结后执行完整后端，再按整系统脚本发布冻结版本，最后执行整站及原生恢复验收；本轮实际结果和边界见文末。全部改动保持未提交、未推送。

默认用例共有前提：使用专用账号/库/任务/模型替身，不修改用户原资源；有正向、非法输入、无权限/跨账号、失败恢复和刷新持久化验证。涉及密码、密钥只读取配置路径，不打印值。执行前保存新的保护摘要和原绑定/价格/注册策略，不能用旧轮次的模型hash覆盖合法当前配置。服务端拒绝与界面隐藏分别验证。

用例中的“覆盖”是现有自动化位置或计划位置；是否本轮运行及通过以执行记录为准。SYS-073官方协议基准为可选待验；SYS-076真实效果继续暂缓；SYS-081为另定扩展。独立语音合成/图片理解两页面暂时隐藏，旧两页面用例保留test.skip，SYS-080新的隐藏/保留能力断言必须实际执行。

## 完整用例

### TC-001 注册、审核与邀请码

- 需求：SYS-001。
- 前提：隔离账号/一次性邀请码。
- 步骤：分别在开放、审核、关闭策略注册；审核/拒绝；重复和过期邀请码再次提交。
- 期望：策略生效，待审不登录，邀请码不能复用，错误不泄露账户详情。
- 覆盖/执行类型：test_accounts.py；accounts.spec.ts。

### TC-002 改密、重置与会话撤销

- 需求：SYS-001。
- 前提：两个独立登录会话。
- 步骤：本人改密；管理员重置；使用旧会话/旧密码再访问；首次强制改密期间访问其他功能。
- 期望：旧会话失效，强制改密不能绕过，明文密码不进入日志。
- 覆盖/执行类型：test_accounts.py；accounts.spec.ts。

### TC-003 账号启停与最后管理员

- 需求：SYS-001、SYS-002。
- 前提：管理员和普通账号。
- 步骤：停用普通账号后访问既有API/运行；尝试禁用最后管理员；恢复账号。
- 期望：撤权后后续访问被拒绝，最后管理员保持可用，恢复不扩大权限。
- 覆盖/执行类型：test_accounts.py。

### TC-004 角色查看、配置、执行分离

- 需求：SYS-002。
- 前提：仅read角色与执行角色。
- 步骤：对同一资源访问列表、配置PUT、执行POST；绕过隐藏按钮直接请求；运行前撤权。
- 期望：服务端分别校验权限，无权操作403；已有实例不能借旧授权继续副作用。
- 覆盖/执行类型：test_accounts.py；test_approval_edits.py；accounts.spec.ts。

### TC-005 组织成员与版本共享

- 需求：SYS-003。
- 前提：两组织、三个账号和已发布版本。
- 步骤：增加/更新成员；非法角色更新；移除所有者；共享/撤销Agent和流程版本；跨组织运行。
- 期望：失败更新不改变原成员，所有者受保护，未共享资源及撤销版本不能运行。
- 覆盖/执行类型：test_organizations.py；management-lists.spec.ts。

### TC-006 认证、来源与密钥保留

- 需求：SYS-004。
- 前提：隔离登录与测试密钥。
- 步骤：无Cookie/错误Origin调用写接口；跨账号订阅/下载；编辑密钥留空后读取状态，明确清除再读取。
- 期望：拒绝未授权/CSRF，Cookie安全；仅状态可见，留空保留、清除删除，不回显密钥。
- 覆盖/执行类型：test_api.py；test_security.py；test_models.py。

### TC-010 十种用途与服务商配置

- 需求：SYS-010。
- 前提：只连接本机协议替身。
- 步骤：逐类新增/编辑/筛选/启停；改变供应商与协议组合；设置两个默认；刷新重查。
- 期望：用途与协议独立，默认唯一；非法组合拒绝，既有数据兼容。
- 覆盖/执行类型：test_model_types.py；platform.spec.ts模型用例。

### TC-011 按协议连接检测与多模态边界

- 需求：SYS-010、SYS-011。
- 前提：文本向量/重排/图片/语音协议替身。
- 步骤：记录各类型请求端点与body；无实现的组合请求检测；错维度/不支持格式调用。
- 期望：不把所有类型当聊天检测；真实实现请求正确端点，未知组合明确说明，维度不混用。
- 覆盖/执行类型：test_model_protocols.py；test_rerank_protocol.py；test_vision.py；test_speech.py；test_multimodal.py。

### TC-012 联网开关、授权与引用

- 需求：SYS-012。
- 前提：原生搜索/Tavily替身。
- 步骤：开关开/关分别发送；Agent未授权搜索；返回来源和未知结果；取消执行。
- 期望：仅授权时发送搜索参数/工具，来源可点击，未执行不得假称联网，未知不重放。
- 覆盖/执行类型：test_native_search.py；test_ask_images_search.py；platform.spec.ts联网用例。

### TC-013 候选路由同意与冷却

- 需求：SYS-013。
- 前提：独立路由fixture、两个本机模型。
- 步骤：配置有序候选并同意；改修订撤销同意；模拟发送前拒绝与发送后超时；恢复探测。
- 期望：能力/同意匹配，修订固定；发送后未知不转发重放，冷却和选中记录可追溯。
- 覆盖/执行类型：test_model_routing.py；test_model_retry_policy.py；routing-billing.spec.ts。

### TC-014 多币种价格和金额预算

- 需求：SYS-014。
- 前提：隔离价格/预算fixture。
- 步骤：并发预留；生成不可变价格版本；缓存/输入/输出分项结算；迟到重复usage；未知冻结。
- 期望：不超预留，CNY/USD不混算，幂等账本，未知不记零，旧价格不被新配置覆盖。
- 覆盖/执行类型：test_money.py；routing-billing.spec.ts。

### TC-020 会话项目、搜索与分页

- 需求：SYS-020。
- 前提：两个私有项目、多页会话。
- 步骤：创建/重命名/置顶/搜索标题正文；分页；删除项目；刷新。
- 期望：顺序稳定，跨页可达，无重复；删项目不删会话，其他账号不可见。
- 覆盖/执行类型：test_conversation_workspace.py；workspace.spec.ts。

### TC-021 归档与恢复

- 需求：SYS-020。
- 前提：带附件和消息会话。
- 步骤：归档后从最近列表查看；切换归档并恢复；验证附件和历史。
- 期望：最近与归档分离，恢复后历史/附件不变，归档限制生效。
- 覆盖/执行类型：test_conversation_archive.py；platform.spec.ts附件用例。

### TC-022 流式帧、重连与取消

- 需求：SYS-021。
- 前提：SSE与持久Ask替身。
- 步骤：拆分帧、心跳、重复event_id、断线重连；生成中取消；最后事件与迟到进度竞态。
- 期望：片段保序去重，部分输出保留，取消不被成功覆盖，最终历史不被迟到响应替换。
- 覆盖/执行类型：test_ask_progress.py；test_ask_worker.py；chat-message-actions.spec.ts。

### TC-023 Ask跨API/Worker重启

- 需求：SYS-021、SYS-075。
- 前提：专用零外部调用的Ask重启fixture。
- 步骤：入队后受控停止/重启；恢复检查点；核对模型/工具次数与持久历史。
- 期望：不丢队列，完成阶段不重放，未知工具进入核查；不能只以200作为恢复通过。
- 覆盖/执行类型：test_ask_worker.py；tests.manual.verify_ask_restart（原生单独执行）。

### TC-024 图片、文档与粘贴附件

- 需求：SYS-022。
- 前提：本机模型/接口替身。
- 步骤：上传合法图片/文档、粘贴并删除；超大小/非法文件；刷新；跨账号下载；选择文字模型发送图片。
- 期望：真实附件与缩略图可见，越权/大小拒绝，不支持图片时阻止；不发真实供应商请求。
- 覆盖/执行类型：test_attachments.py；test_ask_images_search.py；platform.spec.ts实际图片用例。

### TC-025 会话交互与中文输入

- 需求：SYS-023。
- 前提：长历史、延迟流式替身。
- 步骤：上翻后继续输出；返回最新；分页插入；中文IME确认、Shift+Enter；快速切会话和新建。
- 期望：阅读不被拉走，锚点保持，IME不误发，旧草稿/旧响应不串会话，390px无整页溢出。
- 覆盖/执行类型：chat-fluidity.spec.ts。

### TC-026 轮次过程、最终回答与用时

- 需求：SYS-023。
- 前提：含多工具步骤和中断轮次。
- 步骤：展开/收起过程，观察运行中/完成/失败；刷新；检查复制/反馈/分支按钮。
- 期望：仅最终答案显示操作栏，用时由真实运行时间决定，自动任务卡入口不被折叠藏住。
- 覆盖/执行类型：chat-message-actions.spec.ts。

### TC-027 统一Harness预算和无进展停止

- 需求：SYS-024。
- 前提：确定性工具模型。
- 步骤：Ask/Work分别执行多轮工具循环；耗尽Token/步骤；重复无进展工具；上下文压缩。
- 期望：使用统一循环，副作用前阻止超预算，停止原因可诊断，不再嵌套额外模型循环。
- 覆盖/执行类型：test_harness.py；test_harness_usage.py；test_work_budget.py。

### TC-028 逐轮Token、估算与未知用量

- 需求：SYS-025。
- 前提：本机有/无usage响应。
- 步骤：发送前观察估算；两轮聊天、团队/工作流调用；重复和迟到usage；取消/失败。
- 期望：输入输出/累计可追溯，估算不冒充实报，缺失不补零，账本幂等。
- 覆盖/执行类型：test_usage.py；test_usage_details.py；platform.spec.ts Harness用例。

### TC-029 分支与授权引用

- 需求：SYS-026。
- 前提：带附件的源会话、另一个账号。
- 步骤：从消息分支/编辑提问；导入引用；撤销/删除来源；比对原历史和分支用量。
- 期望：源历史不变，分支独立，引用必须授权，撤权不泄露来源，已发生动作不伪称撤销。
- 覆盖/执行类型：test_conversation_branches.py；test_conversation_references.py；workflow-branches.spec.ts；conversation-references.spec.ts。

### TC-030 Agent配置和不可变发布

- 需求：SYS-030。
- 前提：独立Agent及模型/资源。
- 步骤：修改提示、知识/MCP/技能/记忆/联网；发布后再编辑；原版本运行；撤销资源。
- 期望：发布快照不随草稿改变，运行前复核资源授权，旧指令/模型版本可查。
- 覆盖/执行类型：test_agent_versions.py；test_agent_bindings.py；platform.spec.ts Agent发布。

### TC-031 团队只读委派与持久批次

- 需求：SYS-031。
- 前提：多角色只读团队、本机模型。
- 步骤：按角色运行；并行上限；嵌套委派；取消一批次；撤权/共享预算耗尽；刷新。
- 期望：限制并发/层级，父预算共享，角色Token可追踪，取消传播且跨账号不读批次。
- 覆盖/执行类型：test_agent_teams.py；teams.spec.ts。

### TC-032 调度、时区、竞争及事件幂等

- 需求：SYS-032。
- 前提：隔离自动任务、可控时钟。
- 步骤：一次/间隔/Cron；夏令时边界；签名事件重复；错过/忙碌策略；多Worker竞争和重启。
- 期望：不重复入队，规则与时区一致，权限每次复核，自动来源不能无限创建新任务。
- 覆盖/执行类型：test_automations.py；test_schedule_clock.py；automations.spec.ts。

### TC-033 对话创建暂停自动任务

- 需求：SYS-032。
- 前提：专用本机工具模型与Agent版本。
- 步骤：对话创建任务；查看卡片；启用；Worker运行一次；重复工具调用和自动来源调用。
- 期望：先暂停后显式启用，卡片保留，幂等且自动来源无自建权限，无真实模型调用。
- 覆盖/执行类型：test_ask_automations.py；task-entry.spec.ts。

### TC-034 站内与外发通知隔离

- 需求：SYS-033。
- 前提：本机通知接收替身。
- 步骤：四类外发协议；成功/失败/未知；取消任务；凭证变更与重试。
- 期望：任务结果和通知投递分开，未知不盲重发，日志无凭证；真实群未验。
- 覆盖/执行类型：test_automation_notifications.py；automations.spec.ts。

### TC-040 任务草稿及持久运行

- 需求：SYS-040。
- 前提：独立任务、真实本机Coding镜像。
- 步骤：保存/编辑草稿，启动、暂停、继续、取消并刷新；重启Worker。
- 期望：计划/事件/状态持久，已完成阶段不再执行，任务环境归属一致。
- 覆盖/执行类型：test_work_tasks.py；test_work_worker.py；test_work_pause.py；platform.spec.ts Work用例。

### TC-041 租约、配额与孤儿环境

- 需求：SYS-040、SYS-041。
- 前提：隔离DB和本机执行环境。
- 步骤：并发争抢租约、过期/撤权、重启后核对配额、释放失联环境。
- 期望：旧实例不能写，配额跨重启保留，远端缺失确认后才回收，不释放其他用户环境。
- 覆盖/执行类型：test_durable_sandboxes.py；test_work_recovery_real.py；test_work_budget.py。

### TC-042 Windows管理与双镜像

- 需求：SYS-041。
- 前提：已部署OpenSandbox、Coding/Browser镜像。
- 步骤：分别执行固定本机代码和浏览器命令；内存/并发不足；管理服务暂不可用。
- 期望：真实容器执行，受资源限制且恢复说明明确；不把本机容量当生产SLA。
- 覆盖/执行类型：test_sandbox_service.py；test_work_backend_real.py；test_browser_daemon_real.py。

### TC-043 审批摘要、拒绝和改参

- 需求：SYS-042。
- 前提：待审批固定命令/浏览器动作。
- 步骤：用旧摘要批准；拒绝；仅允许的参数修改后批准；重复批准；重建引擎恢复。
- 期望：旧摘要/旧租约拒绝，批准内容固定，副作用仅一次，不允许修改后沿用旧批准。
- 覆盖/执行类型：test_approval_edits.py；test_work_execution.py；browser-workbench.spec.ts。

### TC-044 输入、成果与工作区路径

- 需求：SYS-043。
- 前提：专用任务输入、符号链接/穿越样例。
- 步骤：上传、预览、差异和下载；非法路径/链接；删除及空文件；核对执行输出和成果摘要。
- 期望：路径受限且私有，成果证据真实，恢复数据与摘要一致，无宿主执行。
- 覆盖/执行类型：test_work_inputs.py；test_work_execution.py；test_workspace_backup.py。

### TC-045 浏览器授权、接管和私有状态

- 需求：SYS-044。
- 前提：browser_workbench_fixture。
- 步骤：访问/点击/输入/截图、上传下载；域名外访问；暂停接管；关闭/清除登录状态。
- 期望：操作与审批匹配，实时视图不泄露其他任务，任务状态加密；未知动作不重放。
- 覆盖/执行类型：test_browser_controls.py；test_browser_network_real.py；browser-workbench.spec.ts。

### TC-046 固定SHA检出与任务Git成果

- 需求：SYS-045。
- 前提：repository_browser_fixture、本机Git服务。
- 步骤：检出SHA/子路径；审批固定脚本；提交新增/删除/模式变化；查看差异和bundle。
- 期望：只取授权内容，删除可追溯，凭证不进入上下文/成果，任务分支与快照可恢复。
- 覆盖/执行类型：test_repository_workspace_real.py；test_repository_git_protocol.py；repository-native.spec.ts。

### TC-047 外部分支、草稿PR与反馈

- 需求：SYS-046。
- 前提：本机GitHub协议替身。
- 步骤：无授权推送；分别批准分支和草稿PR；返回未知后只读核查；Issue/CI反馈。
- 期望：不覆盖默认分支、不自动合并、不对未知写入盲重发；本项目不提交推送。
- 覆盖/执行类型：test_repository_actions.py；repositories.spec.ts。

### TC-050 知识库列表、分区和按需加载

- 需求：SYS-050。
- 前提：接口替身、多个知识库。
- 步骤：名称/描述搜索；默认文档；六页签/OCR；无数据/失败重试；切库迟到响应；390px。
- 期望：分区清楚、真实统计、按需取数，只读不被无关MCP/Skills接口阻断，旧结果不串库。
- 覆盖/执行类型：knowledge-center-ui.spec.ts。

### TC-051 关键词/向量/重排证据

- 需求：SYS-051。
- 前提：固定本机向量/重排替身与标注样例。
- 步骤：关键词和混合检索；弱关键词阈值；无答案；模型失败；来源删除/撤权。
- 期望：分数不是置信度，保留语义召回，不屏蔽全部结果刷指标，无证据说明真实。
- 覆盖/执行类型：test_retrieval_evidence.py；test_rerank.py；test_capabilities_api.py。

### TC-052 文档去重、修订与分块

- 需求：SYS-052。
- 前提：专用KB及五种格式样例。
- 步骤：4MiB/编码/格式校验；重复上传；替换修订；失败替换；查看旧分块及分页；重索引/删除。
- 期望：重复不重复计费，失败保留原修订，历史查看无模型调用，Excel公式不执行。
- 覆盖/执行类型：test_document_revisions.py；test_knowledge_usage.py；knowledge-operations.spec.ts。

### TC-053 OCR校对、取消和导入

- 需求：SYS-053。
- 前提：本机视觉协议替身、真实PDF渲染。
- 步骤：指定页范围；识别后修改文本；确认导入；重复/取消/失败；跨账号看图。
- 期望：未校对不入库，页码/原图对应，状态及用量真实，不自动重传未知请求。
- 覆盖/执行类型：test_knowledge_ocr.py；knowledge-operations.spec.ts。

### TC-054 题集、实验、对照与反馈

- 需求：SYS-054。
- 前提：专用KB/题集/证据标注。
- 步骤：预算内运行两次实验；设置/资料修订不同再对照；逐题/分类查看；本人答案反馈。
- 期望：异源对照明示差异，检索指标不充当生成引用正确率，预算与来源真实。
- 覆盖/执行类型：test_knowledge_evaluation.py；test_rag_evaluation.py；test_message_feedback.py；knowledge-operations.spec.ts。

### TC-055 MCP连接发现与只读工具

- 需求：SYS-055。
- 前提：本机Streamable HTTP MCP。
- 步骤：地址限制、加密凭证、发现、启用、schema输入；绑定/撤权；Ask尝试写工具。
- 期望：接口/工具/资源当前授权有效才调用，Ask不能写，凭证不回显。
- 覆盖/执行类型：test_mcp_wire.py；test_capabilities_api.py；platform.spec.ts能力中心。

### TC-056 MCP写工具审批与未知结果

- 需求：SYS-055。
- 前提：管理员Work、本机写工具服务。
- 步骤：发现写工具并启用；每次批准；撤销工具/绑定；模拟未知结果后重复运行。
- 期望：只在管理员Work逐次审批后执行，变更后旧批准失效，未知不自动重放。
- 覆盖/执行类型：test_mcp_writes.py。

### TC-057 Skills资源与任务脚本

- 需求：SYS-056。
- 前提：合法SKILL.md/ZIP和恶意归档。
- 步骤：导入/更新版本、预览、启停；穿越/符号链接归档；批准任务脚本；撤权。
- 期望：版本与资源私有，恶意路径拒绝，脚本只在任务环境执行，宿主文件不可读。
- 覆盖/执行类型：test_skill_revocation.py；test_work_resources.py；platform.spec.ts能力中心。

### TC-058 记忆治理和可选语义召回

- 需求：SYS-057。
- 前提：专用记忆、会话/项目/Agent范围。
- 步骤：手工CRUD；候选提取并确认/拒绝；冲突/重复/过期；来源撤销；跨账号语义检索。
- 期望：候选不自动永久生效，范围/来源可追溯，不把外部资料当系统指令，不跨账号召回。
- 覆盖/执行类型：test_memory_governance.py；test_memory_vectors.py；memories.spec.ts。

### TC-059 图片知识模型与索引

- 需求：SYS-058。
- 前提：本机多模态向量替身。
- 步骤：绑定模型、上传/查图、变更模型或维度、跨账号查看/删除；未绑定时查询。
- 期望：绑定和维度固定，不能混索引，图片资源私有，未绑定明确提示，不承诺图搜图。
- 覆盖/执行类型：test_multimodal.py；platform.spec.ts能力中心。

### TC-060 画布编辑、草稿和快照

- 需求：SYS-060。
- 前提：空白/问答模板、多个修订。
- 步骤：创建、节点增删改复制/连线/布局保存刷新；不完整草稿；冲突修订；查看旧运行。
- 期望：不完整可暂存不可整图执行/发布，冲突不覆盖，历史结果不贴当前修改图。
- 覆盖/执行类型：test_workflow_studio.py；workflow-studio.spec.ts。

### TC-061 输入输出Schema与发布固定

- 需求：SYS-061。
- 前提：对象/数组/数值Schema。
- 步骤：中文表单编辑与JSON往返；非法范围/必填；发布后改草稿；390px。
- 期望：执行前拒绝非法输入，输出校验，发布Schema不漂移，无页面溢出。
- 覆盖/执行类型：test_workflow_schema.py；test_workflow_forms.py；workflow-forms.spec.ts。

### TC-062 十五节点和复合根预算

- 需求：SYS-062。
- 前提：确定性图及本机协议替身。
- 步骤：逐类执行；条件双出口、转换错误出口；迭代/并行/循环上限；资源撤销与预算竞争。
- 期望：变量必须可用，根预算共享，循环有上限，未知外部失败不自动重试。
- 覆盖/执行类型：test_workflow_nodes.py；test_workflow_composition.py；test_workflow_loops.py；workflow-loops.spec.ts。

### TC-063 流程审批跨重启恢复

- 需求：SYS-063、SYS-075。
- 前提：专用零模型固定流程。
- 步骤：运行开始→人工审批；实际重启API/Ask/Work；继续代码审批；核对前置计数与成果。
- 期望：恢复同一运行，前置执行仅一次，代码固定摘要在环境内运行，模型0次。
- 覆盖/执行类型：test_workflow_tasks.py；workflow-task-start.spec.ts→workflow-task-resume.spec.ts（串行原生）。

### TC-064 全部节点独立测试

- 需求：SYS-064。
- 前提：含未配置相邻节点的草稿。
- 步骤：十五类分别单测；MCP额外变量复用；代码/审批独立任务；重复run_id；连续重测。
- 期望：只运行所选节点及其配置子流程，不运行原图邻居；成功不解锁整图发布，耗时/错误真实。
- 覆盖/执行类型：test_workflow_node_debug.py；workflow-node-debug.spec.ts。

### TC-065 API Key和Ask授权流程调用

- 需求：SYS-065。
- 前提：已发布只读版本与API Key。
- 步骤：到期/撤销/限流/版本下线/资源修订变化；跨账号调用；外部调用持久审批图。
- 期望：授权有效才执行，版本不可变，外部API不能绕过写操作审批。
- 覆盖/执行类型：test_external_workflows.py；test_ask_workflows.py；workflow-branches.spec.ts。

### TC-070 服务状态与守护围栏

- 需求：SYS-070。
- 前提：四后台均只有一个本项目实例。
- 步骤：状态读取失败再恢复；数据库断连/维护锁；旧围栏实例写入；安全停止/启动。
- 期望：显示真实四后台状态，连接恢复不冒充重复实例，旧实例停止且未知任务不重放。
- 覆盖/执行类型：test_service_host.py；test_service_fence.py；service-status.spec.ts。

### TC-071 备份完整性与隔离恢复

- 需求：SYS-071。
- 前提：无活动任务、专用验收资源。
- 步骤：归档未提交源码/数据/附件/修订；校验manifest；损坏备份拒绝；恢复到新隔离库；检出Git快照。
- 期望：原库不恢复覆盖，副本禁用旧请求自动执行，源文件/私有附件/凭证摘要完整。
- 覆盖/执行类型：test_workspace_backup.py；test_operations_jobs.py；operations-backup-native.spec.ts及manual恢复脚本。

### TC-072 整套生产部署成功路径

- 需求：SYS-072。
- 前提：正确分支、现有凭证、已通过开发自验。
- 步骤：按运维runbook执行基础设施/双镜像/备份迁移/四后台/生产Web；读取发布记录。
- 期望：仅项目归属进程受控，隐藏启动，production Web与API ready，数据/密钥保留。
- 覆盖/执行类型：scripts/deploy-platform.ps1实际整套生产部署及scripts/check-platform.ps1；见本轮记录。

### TC-073 部署失败与回滚边界

- 需求：SYS-072。
- 前提：专用演练目录/隔离数据，已验证旧构建。
- 步骤：模拟类型/构建/迁移命令失败或就绪失败；核对后续命令是否执行及恢复记录。
- 期望：失败返回非零，不假成功；不继续错误迁移、不自动恢复覆盖生产DB；可恢复前端回到验证旧包。
- 覆盖/执行类型：新增脚本用隔离替身/本机安全演练；未能安全演练的分支明确待验。

### TC-074 官方LangGraph协议基准

- 需求：SYS-073。
- 前提：需要独立官方服务/固定响应环境。
- 步骤：按U11对线程、状态/历史、消息/工具事件、interrupt/resume、取消作对照。
- 期望：必须是官方服务实测，不用平台替身冒充；本轮不增CLI依赖。
- 覆盖/执行类型：可选待验，不纳入本轮通过数量，不报为业务实现缺陷。

### TC-075 三个资源管理列表全量可达

- 需求：SYS-074。
- 前提：隔离账号分别有至少105个任务/工作流/仓库。
- 步骤：首批→加载下一页→进入最旧记录；跨页搜索；刷新/新增后重新翻页；跨账号和非法cursor。
- 期望：旧记录可达，稳定顺序不重复不串号，搜索范围明确，非法游标拒绝，原选择不混淆。
- 覆盖/执行类型：test_core_pagination.py、test_resource_pagination.py、management-pagination.spec.ts、resource-pagination.spec.ts；实际覆盖超过旧窗口及跨页搜索，不仅首100条。

### TC-076 两类运行历史超过50次

- 需求：SYS-074。
- 前提：同任务/同流程分别至少55次测试运行。
- 步骤：进入第2页并查看最早事件；切父资源、刷新、删除/新增后续页；非法和其他账号cursor。
- 期望：旧运行可查看，输入/输出/快照对应同一ID，切资源不残留前一页，游标稳定且受权限约束。
- 覆盖/执行类型：test_core_pagination.py、management-pagination.spec.ts；历史与选中ID持久化/续页失败重试使用确定性图和页面替身，不调用真实模型。

### TC-077 观察窗口和配额语义

- 需求：SYS-074。
- 前提：自动任务20配额、触发/批次记录超过窗口。
- 步骤：检查列表文案总数与“最近N条”；超过配额尝试创建；若宣称全历史则跨页访问。
- 期望：配额不因补分页放开，观察窗口不能冒称完整总量；完整管理记录必须可达。
- 覆盖/执行类型：test_resource_pagination.py、resource-pagination.spec.ts及既有test_automations.py/团队测试；自动任务配额仍为20，批次/触发历史可加载后续页。

### TC-078 租约竞争、未知结果与恢复证据

- 需求：SYS-075。
- 前提：隔离DB、可控本机工具/Worker。
- 步骤：并发抢同一任务；旧租约完成；工具已发送但无确认后重建引擎；已批准流程重启。
- 期望：最多一个有效执行者，未知转核查不重放，旧实例无写权，记录实际工具次数。
- 覆盖/执行类型：test_ask_worker.py；test_work_worker.py；test_work_recovery_real.py；原生流程恢复。

### TC-079 真实业务效果与账单

- 需求：SYS-076。
- 前提：用户提供U01～U16真实配置/50道标注题/标准账单。
- 步骤：真实召回/无答案/生成引用、复杂OCR、模型工具/协作质量、通知/网站/GitHub和账单对照。
- 期望：分别报告Recall@10≥90%/引用≥95%目标及未达项，不将合成或协议替身结果宣称真实达标。
- 覆盖/执行类型：外部待接，本轮不发真实请求、不计通过或失败。

### TC-080 临时隐藏独立媒体导航

- 需求：SYS-080。
- 前提：管理员和只读账号、桌面/390px。
- 步骤：检查全部DOM中语音合成/图片理解独立导航；访问对话图片、知识库OCR/图片资料。
- 期望：两独立入口不存在，组件/接口/模型用途保留，原图片路径仍可到达。
- 覆盖/执行类型：knowledge-center-ui.spec.ts KB-13；platform.spec.ts实际图片/能力中心。

### TC-081 另定生态扩展不伪装支持

- 需求：SYS-081。
- 前提：当前接口与产品文档。
- 步骤：核对SSO/MFA、MCP stdio/SSE/OAuth、ACP、图搜图/视频向量等是否被宣称支持。
- 期望：未纳入功能明确边界，不以供应商名暗示全部原生协议；追加需求后再开发验收。
- 覆盖/执行类型：范围核对，非本轮编码/外部质量通过项。

## 回归执行顺序与可运行命令

### 阶段0：冻结与保护

开发确认SYS-072/SYS-074实现、自测通过后记录提交外的当前源码/构建版本（本项目不提交推送）。先使用运维[整系统部署说明](system-deployment.md)准备服务与镜像；必须四后台就绪、Web3100和API8100可访问，OpenSandbox18180可分配Coding/Browser环境。普通Chrome期间不并发部署、重启或原生备份测试。

管理员本机凭证继续从services/api/.env.admin读取；若用户已改密不能用该旧文件覆盖真实密码。日志、截图、trace可能包含输入或Cookie/凭证，保存在本机，不将未经检查的trace/error-context正文贴入报告。保护摘要只记录hash，不输出原正文或密钥。

在services/api目录，以从未使用的新路径保存本轮基线：

~~~powershell
.\.venv\Scripts\python.exe -m tests.manual.verify_preserved_data snapshot --file .local/system-regression-before.json
if ($LASTEXITCODE -ne 0) { throw '保护摘要保存失败，停止回归' }
~~~

若路径已存在，不覆盖旧证据，改用带时间的本轮路径。该helper涵盖账号、模型、会话、消息、任务运行、用量和主密钥；知识/仓库/工作流只操作测试新建且记录ID的资源，若需原资源全量对照，开发另存这些元数据摘要。合法用户同时使用可能新增记录或改变模型，核对差异来源，不能为“hash全绿”回写原数据。

### 阶段1：完整后端（隔离数据库）

本机PostgreSQL须已存在专用owner_agent_test；禁止把生产URL直接传给测试。代码内fixture还会检查数据库名末尾_test；URL去掉query，避免因末尾校验导致集成测试整体skip。只有TEST_DATABASE_URL和RUN_LOCAL_SANDBOX正确才算完整原生覆盖。

在services/api执行，URL仅在进程环境内构造，不输出：

~~~powershell
@'
import os
from urllib.parse import urlsplit, urlunsplit
import pytest
from app.config import Settings
url = urlsplit(Settings.from_env().database_url)
os.environ["TEST_DATABASE_URL"] = urlunsplit((url.scheme, url.netloc, "/owner_agent_test", "", ""))
os.environ["RUN_LOCAL_SANDBOX"] = "1"
raise SystemExit(pytest.main(["-q", "--tb=short"]))
'@ | .\.venv\Scripts\python.exe -u - *> ../../.local/system-backend-full.log
if ($LASTEXITCODE -ne 0) { throw '后端回归失败，保留日志并停止后续发布' }
~~~

用例会使用真实本机执行环境、浏览器和隔离数据库，模型为确定性替身；少量真实公网example.com网络策略测试不等于真实模型/业务网站联调。按日志统计pass/fail/skip，任何因缺少DB/执行环境导致的skip需补前置后重跑，不能称0失败即全部验收。

### 阶段2：完整整站Chrome

先读取本机实际容量。若能同时容纳Browser预留3GiB与Coding预留5GiB，在services/api依次准备三份专用fixture，每步失败立即停止：

~~~powershell
.\.venv\Scripts\python.exe -m tests.manual.routing_billing_fixture create
if ($LASTEXITCODE -ne 0) { throw '路由费用fixture准备失败' }
.\.venv\Scripts\python.exe -m tests.manual.browser_workbench_fixture create
if ($LASTEXITCODE -ne 0) { throw '浏览器fixture准备失败' }
.\.venv\Scripts\python.exe -m tests.manual.repository_browser_fixture create
if ($LASTEXITCODE -ne 0) { throw '仓库fixture准备失败' }
~~~

分别生成.local/routing-billing-fixture.json、browser-ui-fixture.json、repository-browser-fixture.json。若文件已存在，先核查对应运行，调用该模块cleanup而非覆盖或删除未知ID。以本轮前已清理过的旧apps/web/test-results元数据做唯一归档，不能把旧ID当本轮清理范围。需留意account-test-ids.json、automation-test-ids.json、chat-automation-test-ids.json、memory-test-ids.json、knowledge-operations-test-ids.json、harness-test-ids.json、team-test-ids.json、workflow-studio-test-ids.json。

容量不足8GiB时必须串行：先create routing和Browser，单跑browser-workbench.spec.ts→check→cleanup，再create Git并运行其余默认用例。默认suite用`--grep-invert 'native browser viewing, exclusive manual input, safe close and mobile layout'`只排除已经单独通过的这一项；两批声明用例合起来仍为默认全库存，不能将重复执行再累计成覆盖数量。之后完成Workflow开始/实际重启/恢复/清理，再重建Browser并单跑、check种好私有状态，最后原生备份。保持容量限制，不关闭其他应用、不提高配额。

本轮最初同时准备两类环境时，Git fixture在120轮等待后仍PENDING，error_code=WORK_CAPACITY_WAIT、每15秒退避、模型调用0；实测budget为7,802,269,696字节，小于3+5GiB。Browser验证并释放后同一Git运行正常到WAITING_APPROVAL。因为fixture已记录ID，未重复create；用[一次性精确续接脚本](../.local/complete-recorded-git-fixture.py)校验owner/run/task/workflow/repository、名称前缀、代码摘要、0调用、待审批状态和无既有绑定，再在事务内仅补离线source。复跑时优先按上一段预先串行，勿将该一次性脚本当通用重试入口。

在apps/web执行：

~~~powershell
$env:NODE_OPTIONS='--require ./scripts/use-typescript6.cjs'
.\node_modules\.bin\tsc.cmd --noEmit
if ($LASTEXITCODE -ne 0) { throw '全站类型检查失败' }
.\node_modules\.bin\playwright.cmd test --trace retain-on-failure *> ../../.local/system-browser-full.log
if ($LASTEXITCODE -ne 0) { throw '整站回归失败，保留测试数据及轨迹定位' }
~~~

默认playwright.config.ts为单Worker、Chrome、1440×900，相关用例自行切390px。此配置明确排除三个需要串行重启/备份的测试文件，不是它们已通过；另两个独立媒体页面test.skip符合SYS-080。其他原生仓库或管理员初始化缺失导致skip均需先修复环境。预期数量必须在开发冻结后以实际collection/结果填写，不沿用旧45/423等数字。

### 阶段3：重启、恢复及原生备份（单独串行）

现有默认配置testIgnore：workflow-task-start.spec.ts、workflow-task-resume.spec.ts、operations-backup-native.spec.ts。可用本机临时配置覆盖排除；显式保留完整testDir、单Worker、Chrome、baseURL，testIgnore设空，testMatch仅当前文件，避免误启动全部用例。不要直接只传文件名但仍沿用排除配置，导致“没有测试”被当通过。

执行顺序：
1. 无旧services/api/.local/workflow-task-browser.json的活动fixture时，运行workflow-task-start.spec.ts，确认固定流程停在审批，模型调用0。
2. 通过运维脚本受控重启相关API/Ask/Work，先停止Ask/Work再停API；启动API后等待/health/ready，再启动Ask/Work，最后check-platform确认后台各1实例。保留前后host/child PID证据；不能强杀用户其他运行或自动回放未知任务。
3. 运行workflow-task-resume.spec.ts，批准固定代码、核对HELLO-code、原始转换仅一次、成果计数1。
4. 执行python -m tests.manual.workflow_task_browser_fixture check，再cleanup；每步检查退出码。
5. 若本轮承诺SYS-021最终版本的Ask恢复证据，按tests.manual.verify_ask_restart的参数帮助创建专用替身fixture并检查调用次数；未执行必须留A状态，不能用流程恢复替代。
6. operations-backup-native.spec.ts最后执行。它会暂时停启平台、隔离恢复新数据库并核对源码/私有文件，不得与普通Chrome并发。成功检查4后台各1实例；没有安全条件时记录“待演练”及具体原因。
7. 若需更强Git恢复证据，运行tests.manual.verify_repository_restore指向本轮校验通过的备份；始终新隔离DB/新环境，不恢复覆盖owner_agent。

### 阶段4：按记录清理与数据核对

普通前端finally已尽量通过API删除测试源；后台用量、故障中断或原生环境可能需补清理。先根据每份metadata核实ID/专用名称/归属，再在services/api运行对应模块：
- tests.manual.cleanup_account_browser：account-test-ids.json。
- tests.manual.cleanup_automation_browser：automation-test-ids.json与chat-automation-test-ids.json分别调用。
- tests.manual.cleanup_memory_browser：memory-test-ids.json。
- tests.manual.cleanup_knowledge_browser：knowledge-operations-test-ids.json。
- tests.manual.cleanup_recorded_usage：harness-test-ids.json、team-test-ids.json、workflow-studio-test-ids.json分别调用；只允许已删除模型/源运行的账本。
- 三个fixture模块cleanup；工作流重启fixture用其专用cleanup。Browser/Git环境释放需收到远端确认后再删除本地行或文件。

例如：

~~~powershell
.\.venv\Scripts\python.exe -m tests.manual.cleanup_recorded_usage ../../apps/web/test-results/harness-test-ids.json
if ($LASTEXITCODE -ne 0) { throw '测试账本归属校验未通过，不扩大删除范围' }
~~~

每个helper只接收对应本轮metadata，不遍历所有用户资源“清空测试库”。若清理失败保留证据并定位，未知运行不能自动重试。清理结束再比较新基线；区分新增合法用户数据和原记录变化，不回退跨轮次模型hash漂移。检查Web/API就绪、四后台状态、没有本轮遗留运行与环境。

## 发布与结果判定

- 开发自验先于测试；最终完整后端和默认整站前端需在同一冻结版本，之后有修复则重跑受影响范围，有系统性变动时重新全量。
- 所有必做需求至少有对应TC，新增SYS-074分页和SYS-072部署不能靠旧覆盖推断通过。真实效果E、可选A、明确H/X分开报告。
- 结果报告记录实际命令/版本、pass/fail/skip、首轮失败原因和修复、原生故障演练类型与工具计数、未执行分支、外部待接、数据摘要、清理和部署健康检查。
- 生产构建和增量检查需通过；既有无关静态告警单独登记，不顺手重构或将其隐去。
- 本轮已完成下述全量及定向回归。旧专题结果、重复运行和本轮库存分别计数，外部/可选边界不报为通过。

## 本轮执行记录

### 执行结果与发现

1. 完整services/api后端：Settings在进程内派生owner_agent_test，RUN_LOCAL_SANDBOX=1，python -u pytest -q --tb=short，**458 passed，0 failed/skip，1018.54秒**。实际本机网络执行使用require_escalated；首次受限进程未产生测试结果，已仅终止该会话再重启，未并发执行两套测试。日志[system-backend-full.log](../.local/system-backend-full.log)。
2. 开发审查补充：伪造cursor的整数id首轮复现2 failed，修复类型校验后核心/资源分页6 passed（13.26秒）；部署gate的EOF最终锁校验修复后16 passed（2.99秒）。这些定向结果由开发交付并纳入最终冻结版本，不能掩盖首次发现。完整Ruff、TypeScript检查通过，前端全src/tests ESLint无错误、保留38项既有warnings。
3. 整套生产发布：scripts/deploy-platform.ps1退出0，platform-deployment.status=deployed/phase=complete（2026-11-02 12:55:59本地时间）；生产Web构建、四后台真实心跳、完整健康检查通过。部署前备份20261102T045448Z-7eddb865。见[整系统验收记录](system-acceptance-2026-11-02.md)。
4. 默认Chrome库存79项按容量串行执行：Browser首批1 passed（19.2秒）；其余78项首轮**75 passed、2 skipped、1 failed**（4.8分钟）。两个skip仅为用户要求隐藏的语音合成/图片理解独立页面；隐藏入口和保留对话图片能力的实际断言通过。日志[首批Browser](../.local/system-browser-native-first.log)、[默认剩余78项](../.local/system-browser-full.log)。
5. 唯一首轮页面失败为teams.spec.ts旧选择器`.message-column > .run-diagnostics`。团队最终回答、输入22/输出6 Token及3次调用已通过，实际首个超时发生在展开诊断，finally取消请求又掩盖它。按run对应chat-turn展开process后定位诊断，保留所有团队/子调用Token/权限/390px断言；仅finally增加15秒清理窗口，metadata先保存，替身总在finally关闭。团队及聊天流畅度/消息操作**8 passed（10.8秒）**，TS和该文件ESLint通过。失败trace/metadata已保存在.local/system-browser-team-first-failure；未输出含凭证error-context。最终默认79库存为**77项通过、2项明确隐藏skip、0项未解决失败**，8项复测中重复项不加到79。日志[team/chat复测](../.local/system-team-chat-retest.log)。
6. Workflow开始1 passed（17.3秒）→实际API/Ask/Work停止重启→恢复1 passed（38.2秒）。API child50924→14216，Ask46724→40640，Work50064→56080，host也全部变化。先启动依赖Worker时API尚未ready被脚本正确拒绝，随后等待API ready再启动，最终完整健康检查成功。审批恢复后输出HELLO-code，upper完成事件1次、代码执行1次、计数文件为1、模型调用0，专用check/cleanup成功。日志[开始](../.local/system-workflow-start.log)、[恢复](../.local/system-workflow-resume.log)、[前PID](../.local/system-workflow-restart-before.json)、[后PID](../.local/system-workflow-restart-after.json)。
7. Git本机页面及check成功：固定脚本1次、模型调用0、Git bundle1；Browser重建后再次1 passed（19.0秒），check证实同页面、私有状态加密、模型调用0。最后operations-backup-native **1 passed（46.8秒）**，备份20261102T052050Z-44bc868c；records_match=true、files_verified=46、credentials_decryptable=7、referenced_files_verified=7，恢复后四后台各1实例。日志[Browser重种](../.local/system-browser-native-reseed.log)、[原生备份](../.local/system-operations-backup-native.log)。
8. 既有verify_repository_restore针对本轮备份恢复到新库owner_agent_restored_27f7ecdb和新容器，**exit0**：native_git_restored、deleted_files_remain_deleted、local_commit_preserved全true，模型调用0。隔离副本未启动平台，验证库在finally删除，受保护文件副本保留。日志含SDK sandbox404提示，最终helper已成功确认释放；不将这条已处理提示单独计为失败。日志[Git隔离恢复](../.local/system-native-git-restore.log)。
9. 普通Chrome全部按metadata与归属清理，首轮失败团队源资源和3条账本已额外精确清理；本轮普通账本Harness4/团队3/流程1清理成功。三个nativefixture与Workflow fixture全部清理、远端环境释放已确认，四个fixture metadata文件均不存在。新基线六表原ID/hash完全保留且数量相同：users1、model_configs4、conversations9、conversation_messages75、task_runs14、model_usage32；vault摘要相同。未回滚跨轮次旧模型hash。证据[摘要对照](../.local/system-regression-preservation.json)。
10. 最终scripts/check-platform.ps1退出0：API ready、Web production-ready、sandbox authenticated、双镜像present、API/Ask/Work/Ops owned-alive、migrations current。日志[最终健康检查](../.local/system-final-health.log)。真实外部模型和业务质量按用户要求未调用。
11. 补齐TC-023真实Ask跨API独立重启：原verify_ask_restart保持90秒放行限制，第一轮因编排阶段追加只读检查超时退出，未计通过；该轮finally后六表及密钥摘要仍一致，日志已归档[首轮超时](../.local/system-ask-restart-first-timeout.log)。完成预检后按原helper受控重跑，ready出现即仅受管停止/启动API、ready后写指定.resume，**exit0**：状态断言SUCCEEDED、local_double_calls=1、输入10/输出3 Token、1次调用、external_providers_called=false。API host40156→46312/child51264→41948，Ask host27372/child40464及实例78c64059-e3bb-45f1-af94-edf426df39f1不变，Work/Ops也不变。知识检索和语义记忆均仅工具触发，本机替身无tool_calls，未调用外部模型。helper自行清理后六表原ID/hash及数量、vault再次一致，全健康重查退出0。证据[Ask重启](../.local/system-ask-restart.log)、[前PID](../.local/system-ask-restart-before.json)、[后PID](../.local/system-ask-restart-after.json)。

### TC执行状态映射

以下覆盖51项SYS需求的62个稳定TC，状态是本轮证据范围，不能把用例中的外部预期自动推断为已实测。L表示本机接口/页面/数据库自动化已验证，N表示含真实本机执行环境或实际服务重启，P表示供应商或外部调用仅使用协议替身，A/E/H/X分别表示可选待验/外部待接/明确隐藏/另定扩展。一个TC可同时为L与P，表示平台逻辑通过而真实外部效果尚未验。

- **TC-001～TC-006：L。** 账号注册审核、改密撤销、角色、组织共享、来源和凭证隔离由完整后端及accounts/platform页面覆盖；六表及主密钥最终摘要保留。
- **TC-010：L；TC-011～TC-012：L+P；TC-013～TC-014：L。** 十用途和页面配置、路由同意/费用/个人预算已验；联网和各种供应商原生协议用本机接收端验证契约，真实模型联网/费率账单不在通过范围。
- **TC-020～TC-022：L；TC-023：L+N。** 会话项目/分页/归档/流式重连取消已验；除隔离后端的Ask重建/租约恢复，还实际执行独立verify_ask_restart：模型请求进行中仅API进程变化，Ask实例不变，SUCCEEDED、调用1次、Token10/3，专用数据清理后保护摘要和全健康再次通过。首轮编排超时与受控重跑证据分开保留。
- **TC-024：L+P；TC-025～TC-026：L；TC-027～TC-028：L+P；TC-029～TC-030：L。** 实际图片/文档上传与持久化、中文输入、消息过程/最终答案、分支引用、Agent发布通过；图像理解、模型工具循环及Token上报使用确定性响应，真实视觉质量/账单仍E。
- **TC-031：L+P；TC-032～TC-033：L；TC-034：L+P。** 团队独立子调用及Token/权限、自动任务竞争与对话创建已验；通知签名由本机接收器验证，不代表真实外发通道到达。
- **TC-040～TC-041：L+N；TC-042：N；TC-043～TC-044：L+N；TC-045～TC-046：N；TC-047：L+P。** Work、容量退避、双镜像、审批/路径/成果、Browser接管加密、Git本地差异和隔离检出实测；GitHub远程分支/PR使用本机协议替身。
- **TC-050：L；TC-051：L+P；TC-052：L；TC-053～TC-056：L+P；TC-057：L+N；TC-058～TC-059：L+P。** 新知识UI、文档版本/分块和技能脚本已验；向量/重排/OCR/题集评测/记忆/图片知识及MCP真实SDK本机传输契约已验。真实复杂OCR、外部MCP服务、50道标注题召回率和引用质量为E。
- **TC-060～TC-062：L+P；TC-063：N；TC-064～TC-065：L+P。** 画布/Schema/十五节点/复合预算/发布API和逐节点测试通过；模型、远程工具内容采用确定性替身；固定流程实际跨重启恢复另有N证据。
- **TC-070～TC-072：N；TC-073：L+A。** 实际服务围栏、空闲备份隔离恢复和整套生产发布已验；失败锁/非零返回由16项gate验证，未对生产主动注入构建/迁移失败或执行真实前端回滚，破坏性故障分支仍待安全演练。
- **TC-074：A。** 官方LangGraph独立服务基准本轮未执行，未新增其CLI依赖，不使用平台协议替身冒充官方兼容结论。
- **TC-075～TC-077：L。** 三管理列表及两类运行历史的窗口外数据、搜索、续页/失败重试/选中ID、非法cursor和跨账号由分页后端与页面测试覆盖；原自动任务20配额保留。
- **TC-078：L+N；TC-079：E。** 租约竞争/未知结果拒绝重放由完整后端及实际Workflow恢复验证；真实模型/业务网站/GitHub/通知质量和标准账单对照未接入。
- **TC-080：H+L；TC-081：X。** 两独立媒体导航按用户要求隐藏，其历史两页面用例明确skip；隐藏检查及原对话图片/知识OCR路径保留通过。SSO/MFA、MCP其他传输/OAuth等另定扩展不算本轮已实现或失败。

本轮没有未解决的本机回归失败；这不等同于真实外部效果、可选官方基准和未演练的故障分支均已验收。完整待接与范围说明以[统一总需求](system-requirements.md)及[整系统验收记录](system-acceptance-2026-11-02.md)为准。


