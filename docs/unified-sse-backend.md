# 统一 SSE 后端实现记录

> 范围更正：用户最终要求只改智能对话 SSE。本文保留此前实验与故障证据；Work/Workflow 的本轮改动已精确撤回。当前实现及验收范围见 [智能对话 SSE 后端范围与验证](ask-sse-backend.md)。

2026-11-02，onwer_agent。只改运行事件传输和只读测试的确认生命周期，保留全部JSON历史接口、原数据、密钥及执行授权，不提交推送。

## 协议与投影

Ask沿用`/runs/{id}/events`；Work新增`/work-runs/{id}/events/stream`；Workflow新增`/workflows/{wf}/runs/{run}/events/stream`。均使用Last-Event-ID或after_event_id非负BIGINT游标，先授权再响应，连接中持续复核会话、角色、归属。持久事件有event_id；stream.snapshot/end/error无event_id，不改变游标；error含status_code/code/message。错误与取消不能假作完成。

- Ask snapshot data={run,diagnostics,usage,team}。run只含id/conversation_id/status/created_at/claimed_at/finished_at/error_code；usage为原conversation_usage {runs,total}，team为原{items}。
- Work data={run,approvals,artifacts,operations,recovery,diagnostics,usage,team}。列表字段直接数组，recovery为原{job,sessions}，usage为{total,routed}，其它沿既有鉴权投影。
- Workflow data={run}，含work_status/routing_run_id/routed；去除lease字段，snapshot只保留修订/发布/单测节点标识和安全不可变画布。图保留execution/node_budget/edges和节点id/name/kind/input_from，parameters为空；不传HTTP headers、资源pins、debug变量、指令或完整执行快照。此图只用于只读轨迹，不参与执行或编辑校验。
- 诊断不传execution.lease_until；所有Decimal经显式字符串编码，不损失金额精度。事件表只取原业务数据。0.2秒读新事件、最多100条一页排空；最多每秒获取一次变更快照。终态前强制快照，等待审批/暂停持续订阅。Work终态仍有恢复PENDING/RUNNING或未释放session时保持快照到资源状态明确。
- 每个持久frame发送前及快照取完后重新复核授权，避免慢客户端仍接收已缓存batch的剩余事件；终态关闭前查未读尾事件。无连接跨sleep持有；断连只结束订阅，不执行或重放模型/工具。SSE no-transform在全局响应中保留，JSON仍no-store；默认静态资源压缩未关闭。

## 并发与测试确认

workflow_node_events原全局BIGSERIAL不能用序号推断提交顺序。全部三个生产writer统一workflow_events.append：在同一事务先锁workflow_runs父行，再分配序号/插入，提交后释放；不同运行不互锁。不改旧ID或迁移历史。

只读test/node/published POST新增opt-in `?stream=true`，校验/落库后202返回id；旧无参同步200不变。应用在任何持久写入前持有admission task，handler通过shield等待，入库后持有执行task；不会依赖Starlette发送ACK后才调度BackgroundTasks。客户端ACK发送失败时继续已授权执行一次。main关闭先取消并等待这些task，再将本进程已落库但未完成的运行标FAILED，原startup reconcile继续处理重启遗留，不自动重放。RUNNING落库覆盖ACK到执行间隙，维护idle会正确拒绝备份。

## 作者验证

使用每次全新随机owner_agent_sse_<8hex>_test数据库及独立TEST_DATA_DIR，PYTHONNOUSERSITE=1，finally精确删库；不访问生产控制/用户模型。tests/integration_tests/test_run_streams.py覆盖非法游标、重放、安全投影、205条多页Work事件、持续撤权、审批/暂停/终态恢复、Decimal精度、实际双连接延迟提交、ACK body前ASGI send失败且只执行一次。真实本机HTTP+Uvicorn+受控模型屏障验证202及node.started/首node.delta在完成前可见，释放屏障后最终文字完整，未拿整段fulfill冒充流式。

原test_api SSE历史用例只过滤无event_id控制帧，全部原持久事件顺序/数量/文本/重放断言保留。先后发现响应middleware覆写no-transform已修；网络测试首次cookiejar跨httpx包类型不兼容改为字典；中途可见测试曾错误要求模型释放后末节点历史仍RUNNING，改为首节点与首delta屏障断言，不放宽真正流式目标。首批关键链26项通过22秒；联合六模块39项通过32.32秒（selftest-315d8983.log）。随后逐帧撤权强化与应用关闭held模型/FAILED收尾补充，同冻结14项通过14.89秒（selftest-f1a721ad.log），不把二者相加为一次全量；root负责真实node文字与对应测试，见unified-sse-node-text.md。历史视图由前端一次REST载入不可变完整快照，SSE presentation subset不覆盖该定义，也不参与编辑/输入Schema校验。

当前作者实现与自验完成，生产代码冻结，待独立审查与QA，不能据此声明生产已更新。

## Work 固定流程文字增量补充验证

最终关联检查确认 WorkWorkflow.execute 的 delta 原本已含 kind，没有复现字段缺失异常。实际发现是 observe 对 node.delta 除写流程事件外还额外发送 workflow.node 状态事件；现仅在 node.delta 持久化后直接返回，保留 started/completed/failed 等原状态通知，Work 外层模型文字通道不变。

新增 test_work_workflow_text_stream.py 两个 LLM/Agent 用例，经真实 HTTP Work admission、队列 claim、活租约、资源 pins、WorkWorkflow.graph/execute、模型流、WorkWorker.complete 和数据库账本运行。受控模型在首片后暂停，断言完成前文字已持久化且父任务仍 RUNNING；释放后两片完整、流程 SUCCEEDED、一次 KNOWN 用量（输入7/输出3），legacy workflow.node 仅含六个节点起止状态，无文字片伪状态。仅模型和未使用的环境对象为替身；不覆盖 WorkEngine 外层界面消息，应由 QA 端到端验证。

修复前 Agent 用例在多出 node.delta 状态事件上失败；LLM 首次被复用夹具的 transform 参数挡住，已修正该测试数据，没有放宽业务参数校验。新两项连同只读文字、Work 版本调用、固定任务和 SSE 五模块最终 28 passed、1 skipped，22.18秒（selftest-f16bcc2c.log）；跳过项是既有实际 Coding 环境三审批测试，本轮未申请原生环境。Ruff 和 diff 检查通过。增量仍需独立审查及 QA，未部署、未动用户数据。

## 首帧断连导致服务围栏故障

QA真实代理首轮页面通过后，已完成Ask订阅读到首帧就关闭，出现池连接ACTIVE回滚失败、随后API的专用服务连接OperationalError并安全退出。原始API日志和service_events已保留；不能归因为测试夹具或声称已捕获404。独立随机数据库精确复现：在专用连接真实SQL执行中触发AnyIO取消，调用者已收到CancelledError且mutex已释放，连接却仍ACTIVE（selftest-e3ebb0c0.log）。ASGI重复取消会再次中断psycopg取消查询后的清理等待，下一探测无法复用该连接。

ServiceFence.check/heartbeat现只在持mutex的完整SQL操作及原有3秒自身超时内使用AnyIO shield；退出锁后checkpoint传播调用者待处理取消，不创建脱离锁的后台探测。真实断连、丢锁、超时、OperationalError仍永久fence，健康连接池不能让旧实例重新获准执行。SSE新增reading上下文，只保护每组短数据库读取及事务退出，结束后checkpoint；发送frame、网络背压、sleep均不在保护内。这样断流结束订阅前可完整归还连接，不取消、重放或修改业务执行。

新增test_sse_disconnect.py分别验证专用probe、heartbeat在真实SQL ACTIVE时取消后的连接IDLE及下一次探测正常；同样验证SSE读取将干净连接归还池。真实HTTP用自有API与独立生产Next构建，三种已完成运行执行18次顺序首帧关闭、6次并发首帧关闭，再经直连和Next完整读至stream.end；结束后原专用连接和API仍健康，无连接丢弃或another command日志。期间小延迟均为真实数据库pg_sleep，不替换连接协议。25项联合取消/服务围栏/SSE回归通过50.49秒（selftest-3de56a5e.log），无skip；首次故障QA站和生产均未重启。此补充修复仍待独立审查、QA原真实代理站重新验收。

最终测试只强化活monitor状态需在mutex内读取、Next退出的精确finally清理；最终冻结四项重新通过39.52秒（selftest-ee385227.log），无skip。API包内Ruff与diff检查通过。

独立审查确认 reading 的取消保护还需要查询期限：现局部读取固定5秒，超时取消真实查询并在保护内完成驱动/事务清理；保护结束先传播客户端已发生的取消，再对仍连接的调用者返回固定503 STREAM_READ_TIMEOUT。初始请求走JSON503，已开SSE走stream.error后关闭，不能发送成功结束。没有修改全局数据库超时。真实pg_sleep(20)覆盖正常订阅与已断连订阅在5秒截止、连接干净归还、取消优先于超时、服务围栏健康及后续订阅正常；另覆盖初始响应与已开流的具体错误形式。联合最终29项通过73.01秒（selftest-da59313f.log），无skip。

生产Next测试的finally也只清理本次output.name的两个tsconfig include；next-env仍引用本次目录才改回现有默认.next/types，保留其它builder的改动。该清理末尾小改由静态审查和Ruff验证，不为此重跑整组。生产修复与超时测试已冻结，等待独立增量审查再交QA，未动原QA故障现场或生产服务。
