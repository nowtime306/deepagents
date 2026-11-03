# Dify参考工作流后端实施

依据 workflow-dify-requirements.md WF-D03/04/05/08，保留全部旧定义、同步HTTP及Ask-only SSE范围。不新增依赖、数据库迁移或schema默认字段。

1. 共享定义检查：保留严格validate权威入口，独立节点参数与支配引用收集为code/node_id/field/message。严重图结构错误先返回一条结构问题。新增POST /api/v1/workflows/validate，body {definition}；返回 {valid,scope:"configuration",issues}，仅配置检查，不pin资源、不执行/创建run/usage。沿workflows.write鉴权，格式无效422。
2. 转换新增operation template与template参数，精确{{input}}/{{node_id}}，长度12000、占位最多100、结果最多40000字符。有限正则替换，无表达式；引用加入与主输入/MCP一致的必经检查。旧合法开始节点引用继续兼容。
3. 单节点隔离：主输入text作为所选节点input测试值，其他引用由variables提供；input原本来自上游时，{{input}}必须显式variables.input。先用同一模板渲染，再投影为既有replace(old=整个text,new=结果)，仅替换一次，不二次解析变量内括号，原配置不变。缺值422，不能自动执行上游。
4. runtime使用现有execute values输入处理真实模板；转换错误仍走原确定性错误分支。只读、发布、Work共享实现。
5. 作者测试：无副作用配置校验、多错误、越权、非法/未来/互斥引用、合法中文/多变量/原input、上限、单节点/发布/Work与原流程回归。新隔离库/本机受控模型，作者自验后冻结独立审查再QA，不调用真实外部模型。

## 作者结果与冻结

实际实现仅 workflow_definition.py、workflow_runtime.py、workflow_api.py 三个生产文件；原有字段、Compiler、Work适配器、SSE及同步入口未改。新增模板与配置接口、JSON字段选择继续用原json_get。冻结摘要及目标文件原副本位于后端工作区。

- 首轮新13项通过9.64秒；补充跨分支反例后，13个相关模块80项通过、无跳过，174.36秒。包含原有实际本机Coding/审批用例。通用runner冻结包含并行前端开发，故全域frozen=false；逐项核对该批次所有后端应用文件未改变，不把该false隐瞒为整体冻结通过。
- 新增真实Work模板链后重跑新15项通过22.46秒，全域source冻结不变。真实任务经过持久入队、WorkEngine.events.tick、实际本机环境及共享模板runtime，根COMPLETED/子流程SUCCEEDED，最终报告正确且模型调用0。finally精确释放本轮session并关闭管理客户端。
- 真实Work新夹具首轮错误期望根状态SUCCEEDED，实际原接口COMPLETED，出现14pass/1断言失败；修正测试期望后复测通过，未修改生产状态语义。失败日志保留。
- 定向Ruff通过。测试均新建随机隔离数据库，结束精确删库；未改用户库、外部模型、模型密钥或当前运行服务，未提交推送。

单节点预览沿用原64K定义预算：显式变量展开后构成只执行当前节点的临时定义，大量中文/转义文本可能在40K结果上限之前触发定义预算422；错误可见且不新增运行。普通完整模板执行仍按最终40K字符判断。没有为调试放宽全局定义预算。严格占位语法为精确{{input}}/{{node_id}}，不支持空白，普通单括号文本保留。

本页为作者自验，不替代独立代码审查及QA验收；等待root审核放行。

## 独立审查 P2 修复

根审查指出普通断线/缺出口无法选中节点。新增保持ValueError契约的NodeStructureError，在已知节点的断线、出口、重复/悬空/自环错误携node_id；配置接口返回WORKFLOW_STRUCTURE/edges。两端皆不存在或严重全局图问题仍为全局问题。同步保存/测试/发布仍通过严格validate拒绝，不将定位元数据当可执行许可。

只增加HTTP.url、MCP.tool非空白检查，未增加DNS/资源访问。补充4种定位且无run/usage写入反例与2种空参数反例。最终受影响3模块39项通过、无跳过，89.29秒；含新21项、原节点/单节点及实际Coding审批。源码前后冻结完全相同，定向Ruff通过。最新freeze.json、changes.diff与510f66fdd8日志已更新，等根审查复审放行再交QA。
