> 范围已纠正：本轮仅交付智能对话（Ask）SSE。本文中 Work/Workflow 的扩展实现、审查及测试是已撤回方案的历史记录，不代表当前系统交付；以 unified-sse-plan.md 与最终 Ask 验收为准。

# 统一 SSE 前端独立审查

2026-11-02。审查者sse_review，未编写本批业务源。作者sse_frontend；后端尚在开发，本文不放行后端，也不替代真实Next代理和浏览器验收。

## 首轮结论：不通过，修复后复审

审查范围为共享platform.ts、run-snapshot及Ask/Work/Workflow运行页、诊断/用量/team、节点增量聚合和sse-transport测试。保留既有数据及所有未提交改动，没有启停生产、访问外部模型或修改业务源码。

独立实际执行原7项transport/节点聚合测试，7 passed in1.8s。它们使用Node ReadableStream/Fetch替身，不需要浏览器或生产接口；通过不证明真实代理流式，也未覆盖下述缺陷。

- **R-SSE-FE001 / P2，有界重连失效**：followEvents对每个stream.snapshot也清零failures。连接持续“初始snapshot→EOF”且没有新持久事件时，每次只累加到1，永久1秒重连，无法进入耗尽错误。需按真实业务游标进展或明确稳定恢复判断重置，新增snapshot-only EOF连续失败回归。
- **R-SSE-FE002 / P2，慢历史覆盖推送状态**：Work初始history响应把同ID旧状态放在previous前；Workflow opened effect直接setRuns(history.items)。如果SSE已经交付新run/终态，迟到的初始HTTP会回退或移除它，去除轮询后不再自动纠正。需保留已推送的同run状态及旧页未包含的新run；新增先收终态、再释放旧history的页面回归。打开/关闭切换的取消保护不能代替同一页面的响应先后保护。
- **R-SSE-FE003 / P2，新协议被旧EOF兼容提前结束**：Ask legacyDone+EOF也适用于已经收到stream.snapshot的新协议。若网络在run.completed后、最终snapshot/end前断开，读取器误判完成、不补最终快照；pushed usage又优先于后取的统计，可能停留在旧值。应仅为没有新控制帧的旧协议保留legacy EOF；识别新协议后需要stream.end，断线续连补齐最终snapshot。补两种兼容路径测试，不删除原旧协议回归。

上述问题已交作者和root，未提前交QA。其余已核范围：控制帧不推进cursor、权限错误不重连、Abort取消退避、RunSnapshot按run/mode绑定；活动诊断/用量/team去定时HTTP，仅本地计时器保留；Work/Workflow rAF批次不会每token整树同步更新；节点partial聚合及权威completed覆盖通过既有定向测试。最终放行仍须修复复审及真实页面/代理测试。

## 后端设计审查准备

身份函数每次重新查询会话/账号/路由权限；维护request_slot仅写请求，SSE GET不会长期持有该锁。只读工作流落库RUNNING可覆盖202到执行开始的维护空闲间隙；既有reconcile_startup对交互孤儿标FAILED，不自动重放。

另已提醒作者核对“落库之后、202完整响应发送之前断连/发送失败”的BackgroundTasks生命周期风险。作者随后改为应用持有tracked task并准备真实发送失败回归，目前未冻结，尚未正式审查或放行。

## 第二轮独立复审（仍待修复）

前端R-SSE-FE001/002/003修复已核对：只有持久游标进展重置重连预算；识别控制帧后必须等待stream.end，纯旧协议仍兼容；历史页经合并保留晚于历史的推送。独立执行扩展transport测试，11 passed in 12.8s。包括真实ReadableStream分块替身和迟到历史、原始debug_variables/参数保留；此层不是实际页面/Next代理证据。

复审另发现 **R-SSE-FE004 / P2，早期推送缓存压过后来历史**：pushedRuns长期保存所有运行，mergeRunHistory无条件让缓存胜REST。A收到RUNNING后切换B或关闭工作流，A后台完成；之后新发出的历史读取返回SUCCEEDED，却被旧RUNNING盖住。Workflow open不清map，Work仅切任务才清；非活动A已无SSE修正。独立直接导入当前TypeScript函数复现，期望SUCCEEDED，实际RUNNING，退出码1。已交作者按请求开始的推送版本保护并补回归；不能以永久缓存解决原迟到HTTP竞争。此项修复前不放行前端。

后端及root节点文字冻结版本已独立静态审查：安全投影、循环/每持久帧鉴权、终态排空与Work资源清理、三生产writer父行锁、tracked admission+shield/取消关闭、LLM与Agent真实astream和guard/aclosing。独立随机owner_agent_sse_review_*_test数据库与唯一数据目录运行六模块（test_run_streams、test_workflow_text_stream、test_workflow_nodes、test_workflow_composition、test_workflows、test_api），40 passed in 33.52s。自己的执行器及动态脱敏日志在.local/team-workspace/unified-sse/review/run_review_stream_tests.py和review-fresh-backend.log；finally已精确删除本次随机数据库。初次沙箱网络权限阻止本机连接，获执行权限后完成；无生产数据、外部模型或生产启停。

后端测试覆盖了ASGI ACK发送失败后仍只执行一次、应用关闭取消held模型并FAILED、同一缓存batch中撤权停止后续持久帧、实际双连接延迟提交、真实本机HTTP首delta早于模型完成，以及LLM/Agent各自成功/失败/撤权。旧同步200及原流程提示/Token计量断言保留。此批未发现新增后端阻断，但root正新增Work服务就绪控制订阅，需该局部冻结复审后整体放行；真实Next代理和浏览器仍待QA，不能声明部署完成。

## 最终复审：通过，可交QA（尚未部署）

R-SSE-FE004已按HTTP开始的pushVersion修复：只有请求开始之后到达的推送能盖过该响应；请求前的旧推送不能盖过新REST。已核Work历史、Workflow历史/结束读取/单次完整定义读取均捕获请求版本，完整不可变snapshot与运行状态合并仍分开处理。独立重跑原失败顺序，期望SUCCEEDED、实际SUCCEEDED，退出码0；扩展transport独立 **12 passed in 12.9s**，原迟到历史保护和新鲜历史刷新同时通过。前四个P2发现现均闭环。

新增Work服务就绪订阅已审：独立于activeRun、复用共享followEvents；只发无id的stream.snapshot(capabilities)，变化才发，10秒心跳；采样前身份/角色校验且读取后二次复核，无跨sleep持有连接。页面去除旧初始capabilities GET，防止晚HTTP回退；断线期间和最终错误均关闭发起入口并说明原因，切出时Abort。不会因没有run或旧run终态而失去就绪恢复。仅运行服务就绪使用此只读控制流，不重放控制/模型调用。

最新增量以新的随机_test库和独立数据目录，实际执行test_work_readiness_stream、test_run_streams、test_workflow_text_stream，独立 **22 passed in 21.24s**。两个新增用例验证无run时离线→在线→离线、同连接撤权后错误关闭，以及未登录在SSE响应头前拒绝。原核心六模块上一轮40项与本轮22项是两次运行，不相加为单次全量。review-fresh-backend.log已由本轮执行器更新为22项，并另存review-final-increment.log；上一轮40项保留在本聊天实际工具结果，不能把最新日志误称为40项原始记录。

本次审查通过的关键业务源SHA256清单：.local/team-workspace/unified-sse/review/final-reviewed-source.json。没有修改业务源码、生产数据或生产进程，无提交推送。此结论允许进入QA；真实生产模式Next代理的早片段到达、浏览器交互、完整回归、构建与部署由QA/运维继续，尚未验证的部分不宣称通过。

## 补充门禁：Work模型节点链待增量审查

root指出WorkWorkflow.execute→observe的真实持久Work模型节点链未被上述readonly模型/SSE生成器案例覆盖，已暂撤这条链的最终放行，其余已验证范围保留。独立读取当前源确认delta带kind=node.kind，因此不能把首片KeyError记为当前版本已复现缺陷；可确认observe仍对每个node.delta额外emit legacy workflow.node，需作者使持久化后delta直接返回并以实际WorkWorkflow.execute覆盖LLM/Agent流式链。等待作者冻结修复与新增案例后独立验证，再恢复此链门禁；QA不得把原40/22通过视为该链已测试。

## Work链增量复审：通过，恢复交QA端到端

冻结增量已独立核对SHA：workflow_work.observe在node.delta持久化后直接返回，不额外发送legacy workflow.node；原起止/失败等状态通知和authority保持。回调原有kind保留，没有字段缺失错误的复现结论。新测试实际经HTTP admission、队列claim、租约/权限/pins、WorkWorkflow.graph/execute、LLM/Agent受控流、完成落库与账本，首片屏障和最终输入7/输出3一次KNOWN断言完整，没有用替身直接跳过目标调用链。

使用新的随机owner_agent_sse_review_*_test库及独立目录，独立执行test_work_workflow_text_stream、test_workflow_tasks、test_work_workflows、test_run_streams、test_workflow_text_stream，**28 passed, 1 skipped in 23.92s**。新增两条LLM/Agent均通过；跳过项是既有要求实际Coding环境的三审批测试，未把skip记为通过。精确删除测试库、未调用外部模型或生产；证据为.local/team-workspace/unified-sse/review/run_review_work_chain.py及review-work-chain.log。此链审查恢复通过，可由QA使用自己的服务验真实WorkEngine外层文字通道与页面，不代替该端到端验收。

另独立审查.gitignore单行!apps/web/src/lib/例外：git ls-files已纳入SSE客户端platform.ts，实际.env/.local密钥仍被忽略；source_archive.allowed对客户端允许、.env/.key拒绝验证通过。这修复源码归档遗漏，不改运行行为。其他既有.gitignore改动未触碰。

## QA-SSE001取消修复增量复审（等待读事务上限确认）

作者disconnect-freeze.json四文件SHA与本轮独立核对一致。ServiceFence.check/heartbeat在mutex内用AnyIO shield保护专用SQL与清理，保留内部asyncio.timeout(3)，退出mutex后checkpoint传播pending cancellation；不吞真正session失效。run_streams的reading仅包读取/清理，不跨SSE yield或sleep。独立随机_test库、唯一数据目录/端口/Next输出目录运行test_sse_disconnect、test_service_fence、test_run_streams，**25 passed in 50.82s**，无skip；证据review/run_review_cancel_chain.py与review-cancel-chain.log。包括实际生产Next代理24次首帧断连（六并发）、三通道随后完整end复读、专用guard保持IDLE、pool清理，以及原七项真实会话失效保护。未操作原QA现场或生产进程。

仍待确认的边界：reading shield自身没有超时，storage.Database.fetch/execute也没有statement_timeout；若读取被锁住或执行异常长，已断连任务会一直屏蔽AnyIO取消直到SQL自行结束。不能只凭正常短查询的取消回归宣称有界清理。已交作者/root确认是否有其他上限或增加局部期限，此项明确前暂不最终放行本次取消增量，其他已通过范围保留。

## QA-SSE001最终增量：通过，可交QA复验

上述边界已修复并独立复审：reading在AnyIO shield内使用固定5秒asyncio.timeout，让SQL取消和事务清理完成后退出保护；锁外checkpoint优先传播原客户端取消，只有未取消的调用才得到固定503 STREAM_READ_TIMEOUT。未改全局数据库超时、权限或真正session失效的failclosed含义；不跨持久frame/yield/sleep保护。最新disconnect-freeze.json四文件SHA独立核对一致。

仅对本次期限增量再用fresh随机_test库实际执行四个新增用例，**4 passed in 27.04s**：真实pg_sleep(20)约5秒中断后pool连接仍IDLE可用、已经断连时取消优先于普通超时、响应头前固定JSON503、已开流固定stream.error后关闭；随后readiness和订阅恢复。证据review/run_review_deadline.py与review-deadline.log。此为本轮4项，上一冻结的25项真实Next断流与原session失效回归保留，两次运行不混称一次29项。没有新风险要求重复生产Next构建。

测试夹具finally仅移除本次随机输出对应的两个tsconfig include；next-env仅仍指向本次目录时改回默认路径，不触其他构建目录，静态审查通过。关键源manifest已更新run_streams/service_fence及本次测试哈希。QA-SSE001取消增量审查恢复通过，允许QA重载自己的服务后执行原失败场景、原页面与真实Work绑定端到端复验；尚未生产部署，不能代替最终验收。
