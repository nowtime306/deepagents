# 智能对话 SSE 独立验收

2026-11-02用户最终范围：**只增强智能对话Ask**。不继续Work/Workflow流式改造，不执行90秒循环脚本。原统一34条设计和实验是历史，不当当前交付要求。

## 放行与环境

依据ask-sse-review.md及review/ask-only-source.json，构建前后核对source和restored_exact摘要。仅重载QA自己的四后台，独立API8149、Next3114、本机受控模型8148及原随机_test数据库/数据目录；禁止生产端口/用户配置/真实外部模型。最终Ask-only用新生产构建目录，不复用旧全域BUILD_ID。普通Work/Workflow只原功能smoke与撤回路由检查，旧同步接口必须200。

## 当前用例

- ASK-QA01：真实Next生产代理首片和第二片在模型release前可见，最后一片/最终答案正确；模型explicit_release=true、auto_release=false，一轮只执行一次。
- ASK-QA02：本轮用量更新，最终snapshot在stream.end前到达，不用量未知伪0；刷新历史保留同答案和用量。
- ASK-QA03：分包UTF-8/CRLF、心跳、去重、断线携游标续传；控制帧不占业务序号。
- ASK-QA04：切换对话/退出/取消及退避abort不串旧数据、不重放工具/模型；已显示文字安全保存。
- ASK-QA05：旧终态EOF兼容；新协议业务终态之后必须继续收最终snapshot/end，早EOF重连，有界失败。
- ASK-QA06：401/403控制或握手错误不无限重试，服务中途撤销会话不再发送后续片段。
- ASK-QA07：真实API/Next在Accept-Encoding gzip条件下仍text/event-stream、no-cache/no-transform且多片分时到达；不全局禁静态压缩。
- ASK-QA08：Ask首帧早断连/并发取消后API同实例仍健康；真正丢会话的fail-closed仍按已审backend证据。
- ASK-QA09：活动状态/诊断/用量/team由Ask快照更新；无旧周期HTTP轮询。时钟、管理页查询、Work/Workflow原轮询不误报。
- ASK-QA10：Work原任务详情/审批/成果查询依旧，Workflow画布单节点/测试/发布原业务保留；原同步POST200，无新stream参数准入。
- ASK-QA11：撤回的Work/Workflow/readiness SSE路由不存在，不因通配route返回伪200；16个非Ask文件恢复摘要不变。
- ASK-QA12：最终生产构建、source冻结、仅own资源清理计划，测试结果按层级分别报告。生产部署由root执行，不恢复用户已确认删除的工作流/知识配置。

状态：测试计划已准备；已获Ask-only独立审查放行，开始最终Ask-only构建及隔离验证。旧跨域实验不移花接木成当前通过。实际结果另补，未执行项目不能提前标通过。

## Ask-only 最终实际结果：通过

按review/ask-only-source.json合并source及restored_exact核验，重载仅QA own四后台，并停止已核PID/端口的3114旧QA Web后，全新生产构建`.next-ask-only-qa-production`，BUILD_ID=HppIlRqTVTjE9ht9BVAF0。构建和最后探针前后摘要一致；没有复用旧全域构建。站点及证据保持待root清理指令。

- **真实Ask浏览器通过**：首/第二片在模型显式release前可见，保留6.2秒观察，最后答案完成；source explicit_release=true、auto_release=false。最终用量1次调用、输入10/输出12，刷新后同一run仍有完整答案。另一活动对话切换到新对话后不残留旧流，重进旧对话可续读，再按停止生成，后台CANCELLED；没有将未知用量伪零。证据qa/ask-only-browser.log、ask-only-browser-result.json及ask-only-ask-live.png。
- **读取器8项unit通过12.7秒**：UTF-8/CRLF拆包、续传去重/控制快照、401/403停止重试、退避abort、旧终态兼容、新终态EOF须重连到最终snapshot/end、仅snapshot空转的重试上限。证据qa/ask-only-transport.log。Unit不是实际网络替身效果，层级分别保留。
- **真实协议通过**：API8149/Next3114在gzip请求条件下均text/event-stream、no-store/no-transform、无Content-Encoding缓冲；实际最终snapshot(SUCCEEDED)紧接stream.end之前，持久ID严格升序唯一。Last-Event-ID只补下一条，三个非法游标422；未登录握手401；首帧提前关闭3次后API仍ready200。证据qa/ask-only-protocol-result.json/log。
- **原功能/撤回确认通过**：Work原JSON events200；新建本次transform只读工作流，原无stream参数测试同步200且输出HELLO，原JSON事件200。新增的Work events/stream、workflow events/stream、work-capabilities events/stream三条路由均404。16个非Ask源/原测试文件restored_exact摘要保持；不把旧跨域SSE测试作为本轮结果。

当前Ask-only QA未发现新阻塞缺陷，可交root已审部署。真正外部模型仍未调用，不能用本机模型通过证明供应商效果；生产发布/数据核对由root另验。QA-SSE001修复证据保留，当前Ask源码也有真实早断流正向确认。清理前只停止明确属于本轮profile的进程、数据库/数据目录/生成凭据，日志、截图、freeze和用户当前状态保留。

## 已授权精确清理完成

root批准后，按唯一随机库service_instances process_id写own四类.stop并正常等待退出；验证manifest的3114 Next和8148 delayed_model进程后精确停止。命名空间remaining=0，无额外环境误删。确切owner_agent_sse_qa_ca2ad74b48e0_test已drop，own数据目录及生成管理员凭据删除；证据目录保留，生产3100/8100/3300未启停。3114/8148/8149三个QA端口无监听，最后306项受审source/restored_exact摘要不变。见qa/ask-only-cleanup-result.json及ask-only-cleanup.log。临时Next构建目录保留原位，生成TS路径由root统一恢复，QA未额外修改源码。
