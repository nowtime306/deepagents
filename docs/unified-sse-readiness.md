> 范围已纠正：本轮仅交付智能对话（Ask）SSE。本文中 Work/Workflow 的扩展实现、审查及测试是已撤回方案的历史记录，不代表当前系统交付；以 unified-sse-plan.md 与最终 Ask 验收为准。

# 服务就绪推送与旧行为保留

2026-11-02。测试适配发现：旧Work页面每2秒查询能力，后台从离线变在线会自动启用开始按钮；只删除轮询会使无运行/终态页面永远停在旧状态。主协调追加独立只读能力SSE控制流，已交独立审查通过。

GET /api/v1/work-capabilities/events/stream在响应前鉴权，连接内复用原capabilities投影并再次校验当前身份/权限；每秒采样、只有变化才发无event_id的stream.snapshot，data.capabilities包含enabled等原字段，10秒心跳。没有业务事件/模型/服务控制副作用，没有空闲时持有池连接。身份失效发送固定stream.error后关闭。

WorkPanel使用共享Fetch读取器；该连接不依赖selected run，取消旧能力HTTP查询以免迟到覆盖推送。重连/失败期间禁止开始执行，中文说明独立于运行错误，不清掉其它错误；卸载取消。其它运行详情仍按各自持久事件游标。

root全新测试库中6节点文字+2就绪共8通过11.47秒，TypeScript通过，WorkPanel ESLint0错误/1条同类既有effect警告。独立审查另新库最终22通过21.24秒，12项前端通过12.9秒；不把这些数字合并为一次全量。旧业务断言仍验证开始按钮disabled→自动enabled，原HTTP调用次数按新机制换成能力流状态变化证据。真实页面/代理结果仍由最终QA记录，不把生成器探针当浏览器证据。
