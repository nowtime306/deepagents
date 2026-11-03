> 范围已纠正：本轮仅交付智能对话（Ask）SSE。本文中 Work/Workflow 的扩展实现、审查及测试是已撤回方案的历史记录，不代表当前系统交付；以 unified-sse-plan.md 与最终 Ask 验收为准。

# 工作流节点真实文字增量

2026-11-02，主协调开发。仅修改workflow_runtime.run/execute的模型流及新可选关键字on_delta，保留另一作者workflow_events串行写入改动；不改节点执行结果、模型选择、资源固定、用量回调或公开HTTP旧同步语义。

只读run把观察器传给execute：LLM显式调用model.astream，Agent使用graph.astream(messages+values)，中途只转发用户可见AI文字，忽略platform_role内部输出。node.delta保留node_id和message_id，前缀由既有observe统一添加；最终值取LLM分片拼接/Agent最后values，不把过程文字当最终结果。aclosing确保失败/撤权/取消退出时关闭生成器；文本产生时复核原执行guard。UsageRecorder继续由模型SDK原调用链统计，没有额外invoke或二次计费。

Work调用方可选传on_delta，复用同节点事件；未提供callback的既有内部调用仍保留原ainvoke路径。新增参数为可选keyword-only，不改变已有位置参数。

自验使用新随机_test数据库和唯一TEST_DATA_DIR，fake密钥与受控模型；16项通过17.04秒，Ruff通过。6条新增案例覆盖LLM/Agent各自成功、首片后失败、首片后停用账号：明确释放前已持久化首片、SQL仍RUNNING；成功最终准确拼接，失败输出为空，撤权不写第二片，模型流finally关闭且model_usage只一调用。其余原工作流/节点/组合回归保留，包括实际本机并行模型预算和7/3 Token计量。

两个旧测试夹具同步适配合法stream请求：InspectModel增加_astream捕捉原提示词、保留原保修/条件/发布断言；本机Provider按stream字段返回SSE或原JSON，保留原请求数/原子预算/用量断言。不是放宽业务结果。证据.local/team-workspace/unified-sse/development/workflow-text-selftest.log与执行器run_root_stream_tests.py，精确删除本次随机库。作者测试不代替独立审或真实Next代理浏览器验收，仍须按审查门槛交接。

最终补充：后来新增独立服务就绪SSE，root最新6条节点+2条就绪定向8通过11.47秒，原始日志readiness-node-final-selftest.log；较早16条联合自验汇总保留root-selftest-history.json，不将被后续运行覆盖的旧路径冒称16条原始日志。独立审查者另在新库完整核心40条及最终增量22条分别通过，详见审查报告；最终QA另记录，数字不相加为一次全量。
