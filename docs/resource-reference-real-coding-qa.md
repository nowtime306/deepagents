# RDC 真实本地 Coding 专项验证

日期：2026-11-03。本文记录本地隔离测试的真实执行结果、失败过程及证据边界，不表示全部 QA、界面验收或生产部署已经完成。仅使用随机命名的 `owner_agent_rdc_root_*_test` 数据库和测试所属资源；未操作生产数据库或用户任务。

本记录根据根执行者提供的测试结果、保留的 pytest 日志和对应测试源码整理。本文整理者没有重新执行以下真实测试。模型均为受控替身，没有真实模型供应商调用。

根执行者本组共 6 项通过，分为以下四组。此计数独立于 401/66 的其他回归结果，也不合并 `rdc_history` 负责的另 18 项真实专项。

## 已完成结果

### 模型配置确认后消费原检查点

- 测试：`services/api/tests/integration_tests/test_rdc_recovery_consumer_real.py`；仅测试观察方式的修复提交为 `1dc9bb412`，没有生产代码差异。
- 结果：`1 passed in 29.16s`。
- 日志：`services/api/.local/team-workspace/rdc/root/owner_agent_rdc_root_ac5a892d_test/result.log`。
- 隔离数据库已由测试 helper 删除；日志保留。

该场景使用真实 PostgreSQL、WorkWorker 队列领取、WorkEngine、LangGraph 持久检查点和本地 Coding 容器。新任务只有模型引用，没有 Agent、Skill、MCP、知识库、团队、工作流或其他资源绑定。

首先在首次模型调用之前，通过真实 `work_pause` 检查点暂停。此时只有一条原始用户消息，没有模型调用、审批或写操作凭据。随后关闭第一套消费者连接、清除该运行的进程内资源凭据缓存，重新构造消费者，并修改模型 URL。

普通 resume 后，第二套消费者重新领取任务，实际进入 `PAUSED / RESOURCE_RECOVERY_CONFIRMATION_REQUIRED`，没有调用模型，也没有推进原检查点。恢复预览及显式确认建立 phase 2 后，消费者从原检查点完成运行。断言同时验证：

- 原始用户消息的 ID 和内容保持，用户消息总数严格为一；
- 模型工厂收到确认后的 URL、revision 2 和 temperature 0.4，模型替身恰好调用一次；
- 唯一一条 `KNOWN` 模型用量关联 phase 2 的资源快照，phase 1 的 `used_at` 仍为空；
- 没有审批或 `operation_executions`，完成后再次 tick 不领取任务，也不重复调用模型。

此处通过重建消费者和清理进程缓存模拟冷恢复，**没有重启真实工作进程或主机**。模型返回最终文本，没有业务写入。本项不证明跨配置阶段的写操作恰好执行一次，也不支持把已经执行写操作的运行自动迁移到新阶段。

### 原审批恢复与成果下载

- 测试：`services/api/tests/integration_tests/test_work_execution.py::test_real_worker_approval_resume_and_artifact_download`。
- 结果：`1 passed in 75.18s`。
- 日志：`services/api/.local/team-workspace/rdc/root/owner_agent_rdc_root_064dd10e_test/result.log`。
- 隔离数据库已由测试 helper 删除；日志保留。

真实 Coding 容器执行受控模型安排的读取、脚本复制和命令执行流程。首次写命令经审批编辑后执行；相同审批决定重复提交，返回保持幂等。随后 verifier 子任务触发第二次独立审批，最终任务完成并下载成果。

断言包括 `changed-once` 在最终输入成果中严格出现一次、两个成功命令和两条成功操作凭据、计算结果 42、脚本成果内容正确，以及运行完成后会话释放、队列不再领取。

这证明该测试中既有审批恢复、重复决定幂等和成果读取路径可在真实容器中工作。它没有经历资源配置阶段切换，不证明跨阶段写入恢复，也没有证明真实进程重启后的写操作恢复。

### stdio 与 ACP 原生协议桥

- 测试：`services/api/tests/integration_tests/test_tool_expansion.py::test_isolated_native_jsonrpc_protocol`，参数为 `stdio`、`acp`。
- 结果：`2 passed in 5.77s`。
- 日志：`services/api/.local/team-workspace/rdc/root/owner_agent_rdc_root_2b276482_test/result.log`。
- 隔离数据库已由测试 helper 删除；日志保留。

测试实际运行 `owner-agent/coding:dev` Docker 容器，禁用网络并限制资源，在容器内启动受控 Python JSON-RPC 服务进程，使用真实标准输入输出和协议桥；不是替换进程启动的单元测试。

stdio 分支验证 discover 成功返回 `tools: []`；ACP 分支完成 initialize、session/new、session/prompt，并收到一条 session/update。两者还验证包含 Unicode 文本的生成命令不超过既有 16000 字符预算及子进程正常退出。

stdio 没有返回可调用工具，也没有执行 MCP `tools/call`，不能据此声称完整 MCP 实际工具调用链已经验收。ACP 服务是测试脚本，不是真实外部 Agent 服务；本项不验证实际供应商或用户配置的协议服务。

### 仓库工作区与固定工作流代码

- 测试：`services/api/tests/integration_tests/test_repository_workspace_real.py` 的 `test_native_git_commit_dirty_delete_ignore_bundle_and_process_quiesce` 和 `test_frozen_repository_fixed_workflow_code_and_approval_restore`。
- 结果：`2 passed in 114.85s`。
- 日志：`services/api/.local/team-workspace/rdc/root/owner_agent_rdc_root_26093234_test/result.log`。
- 隔离数据库已由测试 helper 删除；日志保留。

第一项在真实 Coding 容器内操作临时 Git 仓库，覆盖本地 commit、脏文件、已提交后删除文件、暂存文件、可执行位、ignore/exclude、Git bundle 和工作区成果导出，启动分离子进程后执行既有 quiesce，再释放首个容器并在第二个容器恢复。断言恢复后的提交、分支、内容、删除状态、可执行位及成果正确，忽略文件没有被导出，任务设置的 fsmonitor/config include 没有在可信快照路径执行，也没有带入恢复后的 Git 配置。

第二项通过真实 WorkEngine 队列执行固定工作流代码：绑定冻结的仓库输入，暂停等待审批，批准后恢复并生成代码修改成果。断言最终完成、输出 `done`、模型调用为零、成功代码节点为一、仓库 diff 包含预期修改，以及冻结仓库源未保存测试凭据。

GitHub 仓库来源使用受控 source 替身，没有真实 GitHub 拉取或 push。第一项确实跨两个 Coding 容器恢复工作区，但不是工作进程重启或跨配置阶段的模型/工具写入恢复证明。

## 失败过程与处理

### 本地 Coding 镜像缺失

首轮在 `owner_agent_rdc_root_213599b5_test` 中执行恢复和既有审批测试，结果为 `2 failed in 40.48s`。根执行者根据 manager 错误确认本地 `owner-agent/coding:dev` 缺失，创建时出现镜像 404 / 拉取权限失败；pytest 侧包含创建超时。这是环境失败，不能计为产品恢复逻辑已通过或已证失败。

随后按现有 Dockerfile 构建成功，镜像 ID 为：

`sha256:459420e199a24347bed6091ca0683cc570e7fd4c3737f086c3b43533c89c7ce9`

该失败库的清理脚本限定精确库名，核对 manager inventory 为空后才删除此测试库，保留原日志。证据目录为 `services/api/.local/team-workspace/rdc/root/owner_agent_rdc_root_213599b5_test/`。

### 冷环境拉取超时

`owner_agent_rdc_root_6b97db71_test` 的后续恢复测试为 `1 failed in 39.52s`，创建 Coding 环境遇到 ReadTimeout；根执行者确认属于冷环境出口拉取超时。测试 finally 未能确认释放时保留了待核对证据，没有直接删除数据库掩盖资源状态。

之后只对该库 namespace 进行既有 owned reconcile，确认没有未 RELEASED 的测试会话后删除精确命名的测试库。保留日志及 `services/api/.local/team-workspace/rdc/root/reconcile_failed_cold_pull.py`，未执行全局资源清理。

### 原始 Saver 的消息观察方式错误

`owner_agent_rdc_root_e863a14b_test` 已真实到达模型调用前的 PAUSED，但测试直接读取 `original.checkpoint["channel_values"]["messages"]` 时发生 KeyError，结果为 `1 failed in 18.18s`。

LangGraph 的原始 Saver 数据可能采用 delta/blob 通道，并不保证原始 `channel_values` 已物化 `messages`。测试改为 `ObservedWorkEngine` 只覆盖 `agent_graph`：仍调用真实 `super.agent_graph`，保留其 compiled graph，再通过 `aget_state(...).values["messages"]` 检查消息。原始 Saver 继续用于检查检查点存在和拒绝恢复后未推进。没有修改生产行为，也没有放宽唯一用户消息、检查点、一次模型调用或阶段关联断言。此测试改动由 `rdc_index` 独立复审，随后根执行者取得上述 29.16 秒通过结果。

清理该失败库前，单独核对测试会话和运行均已由 finally 删除、无活动租约，真实 manager 按该库 namespace 查询匹配容器为零。先导出检查点证据，再仅删除 `owner_agent_rdc_root_e863a14b_test`；没有停止容器或清理其他数据库。

- 清理记录：`services/api/.local/team-workspace/rdc/root/owner_agent_rdc_root_e863a14b_test/cleanup-evidence.json`。
- 检查点导出：同目录 `checkpoint-evidence-before-drop.json`，保留 3 条 checkpoints、1 条 checkpoint_blobs、4 条 checkpoint_writes，以及阶段和快照记录。
- 检查点证据 SHA-256：`c184509c90a00c9ffff6978a1f1f5c22ba080c15c3343716e8779e75381158db`。
- 原失败日志 SHA-256：`c82e2823e095788627cf6ef65cd665f85611a0145b3d4c8dc50020505349895d`，清理后未改变。

## 尚未纳入本记录的验收

受限生产页面的界面验收、完整回归、真实供应商调用及生产部署不由本文证明。`rdc_history` 的另 18 项真实专项保持独立记录，本文不推断其结果。后续结果应另附日志和准确范围后追加，不能用上述六项通过代替未执行的验收。
