> 范围已纠正：本轮仅交付智能对话（Ask）SSE。本文中 Work/Workflow 的扩展实现、审查及测试是已撤回方案的历史记录，不代表当前系统交付；以 unified-sse-plan.md 与最终 Ask 验收为准。

# 旧页面夹具适配独立审查

2026-11-02。作者sse_qa，独立审查主协调。以最近已验源码归档只读提取的四文件为before，逐段核对qa/legacy-fixture-adaptation.diff及新增apps/web/tests/sse-fixture.ts；未恢复该归档或运行生产测试。

结论：测试夹具适配通过，可在业务源码独立审查通过后用于新的隔离站点。不能据此提前认定真实代理流式已验。

- platform Work保留模型选择、开始按钮初始disabled及自动恢复enabled、计划、审批参数/决定、最终成果、下载名称全部业务断言。能力HTTP轮询次数断言按本批机制改为fixture发出离线/在线两种推送、页面分别观察disabled/enabled；真正更新仍必须由同一SSE连接驱动。审批后同连接发送完成快照与end，没有模拟EOF反复请求来冒充推送。
- repositories只补合法终态snapshot/end和独立能力流，原仓库审阅/批准/撤销/证据/手机版断言保持。
- node-debug的UI只读调用202和WAITING_APPROVAL持续连接符合新合同，节点输入/输出/耗时/错误/变量及14种节点逻辑断言保持；发布/未运行节点不伪造成功。
- workflow-forms仅UI的?stream=true按pathname识别并验202，再由测试只读查询终态保留原输出JSON比较。测试自己的assert.poll是验收等待，不是产品轮询；直接调用旧同步接口的200断言未改。
- 新HTTP夹具仅绑定127.0.0.1动态端口、固定/events路径及localhost Origin，显式flush headers/持续response/publish，finally关闭本次连接和服务器；不执行模型、数据库、进程控制或外连。repo整串fulfill仅为终态组件证据，不算分时流式；真正Next代理证明必须用独立慢模型屏障。

原测试若直接运行会访问3100/旧管理员路径，禁止为方便直接对用户生产跑整组。QA必须使用新test数据库/数据目录/账号和URL/metadata精确适配副本，保持断言；业务审查门槛和真实网络证据另行记录。
