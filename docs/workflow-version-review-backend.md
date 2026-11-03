# 工作流版本后端独立代码审查

2026-11-03。审查者未编写此后端实现，范围为独立workflow_versions路由、原publish/state增量、080迁移和主路由注册。基线为2818e6f及backend目录原副本；不替代独立QA。

## 检查结果

三种状态分开：draft revision、不可变published version/active、独立current指针/release_revision。080不猜旧current，复合FK不允许跨流程指向；删除整流程的兼容有作者API反例。新publish和下线当前版在原流程行锁内更新指针，幂等publish和普通重新上线不抢current。显式Key/Agent/subworkflow版本执行路径未重定向。

set-current先本人/独立CAS再查active、编辑器可支持结构和实时pins，管理操作不调用模型或工具。restore在同一事务先CAS，创建独立未测未发布备份，再恢复历史，清空tested资格；名称冲突或故障完整回滚。copy只拷配置，不拷运行/Key/权限/测试状态。资源失效允许留待修配置，不偷偷换ID；不支持的结构明确拒绝。GET和新router顺序保留原/options等静态入口，读写权限沿workflows.read/manage原策略。

## WVR-BE01/P2：025裸历史的编辑器投影

真实025默认definition可缺parameters等默认字段，核心Definition可支持，但返回raw直接渲染会碰编辑器直接属性访问。作者先新增API反例并由根在独立库执行，1项失败7.13秒（缺canvas_definition）；修复后GET草稿规范化，历史原raw definition保留、canvas_definition提供受支持的默认补全，unsupported仅原文预览。restore返回完整规范化workflow，便于UI可靠落成功状态，追加字段不改变原id/revision/backup响应。

逐行复查projection、draft_response与RETURNING*，未改DB历史或旧执行语义；后端原作者实现修复，审查者只读核对。修后13项作者自验通过17.85秒，全应用源码摘要一致，Ruff通过。原18模块169项通过228.49秒属于修前源，其中UI并行导致全源frozen=false，后端app逐项hash一致；不冒充最终修后全量或相加。

当前后端增量复审通过，可交独立非界面QA。前端及发布脚本各有独立审查；浏览器实际验收因本地页面授权拒绝尚待用户允许，不能据此上线。后端最新5项SHA见review/backend-reviewed-source.json，交QA后如业务源再变须定向复审。
