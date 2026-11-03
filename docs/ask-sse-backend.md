# 智能对话 SSE 后端范围与验证

用户最终明确：只有智能对话使用本次 SSE 改动，任务工作台和工作流保持本轮前的交互及执行方式。以下为当前范围；原统一三通道的记录仅作研发历史。

## 基线与精确撤回

基线为 `services/api/.local/backups/20261102T114245Z-875e1c0c/source.zip`。目标源码与 `20261102T113819Z-9373feae/source.zip` 逐字节一致，也符合上一轮验收清单。

- `workflow_api.py`、`workflow_runtime.py`、`workflow_work.py`、`work_run_api.py` 完全恢复基线，撤回本次 Work/Workflow/就绪 SSE 路由、只读流式准入202、节点文字增量及其写入辅助。
- `test_workflow_nodes.py`、`test_workflow_composition.py` 完全恢复对应基线夹具。
- `main.py` 只撤回只读准入任务生命周期，保留原有 `event_stream(request, run_id, cursor)` 入口和 Ask SSE。初始授权统一在有界读取保护中执行，去掉重复的未保护授权读取。
- 新增 `readonly_runs.py`、`workflow_events.py`、三个 Work/Workflow 专项测试已退出运行源码或测试树；本次之前的文件逐项存入 `.local/team-workspace/unified-sse/backend/scope-before-withdrawal`。历史日志与证据保留。

撤回只涉及源码，没有还原任何数据库、配置、密钥或用户状态。基线比对与SHA见 `ask-scope-restoration.json`。

## 当前 Ask 行为

仅 `/api/v1/runs/{id}/events` 提供本轮事件、诊断、用量、团队与运行状态快照。保留原持久事件序号和历史接口；控制 snapshot/end/error 不占持久序号。每帧检查当前身份及归属，撤销后不继续发送已缓存的剩余事件；终态排空所有可见事件后结束。BIGINT游标校验、Decimal精确编码与禁代理转换保留。

只读阶段最多5秒，超时先完成驱动和事务清理，然后传播客户端已发生的取消；仍连接的初始请求返回 JSON503，已有 SSE 返回固定 `STREAM_READ_TIMEOUT` 错误并关闭。发送网络帧和等待下一轮不屏蔽取消。服务锁探测与心跳的取消清理修复保留：实际数据库会话丢失仍永久停止旧实例，不能靠恢复连接池继续执行。

## 作者验证

使用独立随机测试数据库与唯一目录，结束后精确删库，不使用用户模型。九个后端模块联合 `56 passed, 2 skipped`，95.80秒，日志 `selftest-7be29f9c.log`。两项跳过均为既有 `test_workflow_node_debug.py::test_durable_node_debug_approval_resume_and_publish_boundary[code/approval]`，需要实际 Coding 环境；本次未开启该环境。

Ask 生产 Next 测试实际执行：受控模型首段到达浏览器代理时运行仍为 RUNNING、末段屏障尚未释放；释放后两段完整，成功结束。再进行18次顺序和6次并发的仅 Ask 首帧关闭，直连及 Next 均可完整重放，原服务锁连接与 API 健康。真实长查询5秒期限、客户端取消、初始JSON503、已开流错误帧、缓存批次撤权及205事件分页均通过。恢复基线的普通工作流、节点测试、组合及 Work 调用已随同回归。Ruff 与差异检查通过。

当前为作者冻结结果；由协调者独立审查后再进行真实 Ask 页面 QA，尚未据此更新生产服务。
