# 统一 SSE 独立 QA 缺陷

> 用户最新要求收窄为Ask-only。Work/Workflow测试停止，保留已产生的原始证据；QA-SSE001与Ask订阅断开相关，修复和复测证据仍适用，最终须按Ask-only源码重新确认。

## QA-SSE-001 / P1：提前关闭 SSE 后独立 API 失去准入并自行退出

状态：已交作者定位，阻塞最终发布；尚未断定根因，不能以重启掩盖。没有生产进程启停或生产数据写入。

最终状态补记：作者修复短SQL取消清理和ServiceFence并发取消边界，经独立审后，原现场重载受审版本，原raw提前断流及24并发断流/同API身份保持通过；用户收窄Ask-only后，又在最终Ask-only生产构建完成真实Ask早断流、首片/答案/用量/历史/切换取消/协议与读取器验收，未发现新阻塞，QA-SSE001关闭。原失效现场与失败日志保留，不将跨域历史实验计为Ask-only新增功能。最终QA结果与精确清理见ask-sse-tests.md。

环境：已独立审源码，新库`owner_agent_sse_qa_ca2ad74b48e0_test`/独立目录，API8149、生产模式Next3114、仅本机模型8148。BUILD_ID `9CsiTXimTrQfPJauMvY1s`。三条真实代理Ask/只读工作流/一般Work在explicit_release屏障下已通过，此缺陷发生于随后独立协议探针。

实际序列：已登录的httpx客户端先直连8149读取已终态Ask SSE首帧后关闭，再经Next3114读取同SSE首帧后关闭；随后workflow SSE返回503。api.log出现psycopg AsyncConnection ACTIVE的rollback `another command is already in progress`、丢弃连接，再出现Shutting down及退出。数据库service_events记录api/fenced/OperationalError，data/services/api.stop不存在。之后登录探针收到非JSON错误，不能误报为密码错误或限流。

首轮协议探针还存在workflow列表首项关联不精确的夹具问题；首轮日志仅记录200断言失败，未捕获404，**不能编造首轮404或将服务退出归因于错ID**。API原日志该请求实际503。关联问题随后改为本轮明确名称/ID，但API已经退出，未把重跑错误算新独立产品缺陷。

证据：`.local/team-workspace/unified-sse/qa/live-protocol.log`、live-protocol-id-fixed.log、live-protocol-diagnostic.log及站点api.log125～175行/关闭堆栈；service_events只读结果为api/fenced/OperationalError。完整原日志保留。

预期：客户端取消/断开只结束该订阅，数据库连接应安全归还；API及守护准入持续正常。真正DB会话失效的既有安全围栏不能被取消保护吞掉。修复需可重复提前close/并发abort回归，独立审查后本站原场景复验，并保留503、会话失效及跨重启规则。

## 测试准备问题（不计业务缺陷）

- 首次旧页面副本config未显式指定outputDir，Playwright默认清理test-results误清自身副本，collection无tests；未执行业务。已改输出为QA独立artifacts并重建副本，原始测试源/生产构建不受影响。之后必须检查两目录互不包含。
- 副本截图路径批量变换误加到本轮凭据路径，导致两个ENOENT和一个skip，均在登录前。只纠正副本路径后Work/repo两项已通过；Node调试15项通过。没有修改密码、模型或业务断言。
- workflow-forms已发布版本的UI等待仍按无query/200，已审新合同为?stream=true/202，因此90秒等待超时，finally日志不能当真正删除失败。新适配保留直接同步API200及全部schema/mobile业务断言，待审复测。
