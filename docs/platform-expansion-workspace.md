# 扩展批次协作工作区

2026-11-02。共享项目 `D:\project\my_agent\deepagents`，分支 `onwer_agent`；用户明确要求角色并行，无上下游依赖不相互等待。原工程包含大量未提交/未跟踪功能，不通过空worktree或提交快照分割工作，以免丢失当前基线；使用专属目录、文件所有权和模块冻结交接。

团队过程工作区：`.local/team-workspace/platform-expansion/`，product、development、qa、operations四个子目录保存各自日志、样本和交接证据，不存明文用户密钥。正式文档、源码及Demo仍保存在产品约定路径，不用重复副本作为真相。

## 所有权

- 产品：`docs/platform-expansion-requirements.md`、`apps/web/public/demos/platform-expansion.html`。研究、需求和Demo并行，不等程序员完成。
- 开发主协调：`docs/platform-expansion-development.md`、本工作板、监测API/采样/中文大盘及集成入口；先修测试已报Bug。后续身份、工具、计费/媒体按模块分配独占文件和迁移编号，不能多人同时编辑main.py等入口；分模块作者提供注册片段，协调者集成。
- 测试：`docs/platform-expansion-bugs.md`、`docs/platform-expansion-test-cases.md`及新验收用例。已确定需求立即写用例；模块自验、独立代码审查和必要复审通过后即可验收该模块，不必等其他无关模块。完整回归仅在所有相关代码冻结后执行。
- 运维：`infra/monitoring/`、`scripts/*monitoring*.ps1`、`docs/platform-expansion-operations.md`。指标契约已确定即可编脚本/看板；真实启动和会停后台的操作统一登记，避免与测试并发。

## 已交接契约

- 产品v1.0的15个EXP已落盘，开发/测试可直接开展，不等Demo。
- 监测大盘管理员入口 `/api/v1/operations/monitoring`；导出 `/api/v1/internal/observability/metrics`，独立Bearer，不使用浏览器cookie。token按实例位于 `PLATFORM_DATA_DIR/monitoring/metrics.token`（默认 `services/api/.local/monitoring/metrics.token`），不回退生产凭证；Prometheus只读挂载，不放argv或输出。
- Docker Desktop探针已证实 `extra_hosts: host.docker.internal:host-gateway` 可访问API8100；无需另开公开exporter端口。
- 指标名为 `owner_agent_service_online{kind}`、`owner_agent_queue_runs{mode,status}`、`owner_agent_queue_oldest_seconds{mode}`、`owner_agent_host_{cpu_percent,memory_available_bytes,memory_total_bytes,disk_free_bytes,disk_total_bytes}`、`owner_agent_model_tokens_sum{direction}`、`owner_agent_model_calls_sum{result}`、`owner_agent_cost_estimate_sum{currency}`、`owner_agent_cost_unknown_calls`、`owner_agent_sample_timestamp_seconds`、`owner_agent_sample_available{signal}`。这些是可回算gauge，不伪装单调counter；label有界，不含账号、任务、地址或模型名。
- 独立媒体入口继续隐藏；真实外部联调继续跳过，用隔离库/本机签名IdP/协议替身验证。所有原数据/密钥、历史和未提交改动保留，不提交推送。

## 代码审查门槛（用户新增要求）

每个模块依次经过：开发完成与自验 → 独立审查 → 修复问题 → 复审通过 → 测试验收。模块作者不能用自验代替独立审查；审查失败时不交QA，保留问题编号、严重性、修复和复审证据。全站集成改变还须审查集成差异。测试可提前独立编写用例和准备样本，只有业务验收等待其上游审查通过；不得为并行绕过门槛。

## 执行门槛

同一文件只由负责人修改。依赖未定时先执行其余独立部分；上下游交接标明实现、测试、未支持边界。不得把Demo、已有历史测试或未接入外部效果当本轮已通过。任何服务启停先通过协调者登记，磁盘/内存不足正确排队，不更改用户其他应用。
