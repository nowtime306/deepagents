# RDC-01～04 运行资源解析开发说明

范围：智能体发布版、自动任务、团队、工作流各入口及公共模型记账。业务发布版本仍固定逻辑；model_revision/resource_pins 保留为历史证据，不再代表外部配置当前不可用。

## 实现与边界

- ModelRegistry.get 仍读取实时存在/启用状态，并检查当前用途和协议兼容；运行上下文内返回首次解析的配置。排队、创建规则及保存草稿不创建最终运行快照。
- resource_run 以 owner、mode、run、确认阶段隔离；团队和嵌套流程继承根上下文，同一资源 ID 复用首次配置。运行中普通维护不会替换地址、参数或密钥。
- run_resource_snapshots 保存脱敏摘要及加密的非凭据执行字段；不保存密钥历史。模型密钥仅在进程内按运行阶段缓存。used_at 与 model_usage.resource_snapshot_id 区分已解析和已发起调用。
- 暂停恢复先读已封存配置；进程重启后仅当协议、供应商和服务目标相同才可使用当前凭据，并标记 credential_changed。目标变化报 RESOURCE_RECOVERY_CONFIRMATION_REQUIRED，需确认新阶段；不得将新目标凭据发往旧目标。
- run_resource_stages 中 phase>1 必须有 confirmed_by；只允许服务端验证拥有者及恢复条件后写入。运行不接受客户端任意阶段跳转。
- 工作流 Agent ID 节点首次解析当前配置并封存；显式子工作流版本 ID 保持原值。全量 pins 相等校验移除，节点级资源存在、启用、能力、授权和业务版本 active 检查继续执行。
- 旧工作流直接设为当前时校验其定义及当前依赖；草稿自身 tested_revision 门槛及 release_revision 并发控制保留。

## 验证目标

1. 发布版原 model_revision=1、当前模型 revision=2：选取发布版和创建自动规则成功；用途改成 embedding 或停用仍拒绝。
2. 运行首次用 revision=2 后更新配置：当前成员、嵌套流程和后续调用仍用 revision=2；新运行取 revision=3。
3. 冷恢复同目标换密钥可继续，异目标必须确认；记录中没有测试秘密值。
4. v1/v2 工作流维护模型后可切回 v1；显式 v2 执行逻辑不变。
5. 团队成员版本下线、授权撤销、取消、租约失效及预算仍拦截。

自动测试使用隔离的 *_test 数据库和受控模型，不证明真实供应商效果。独立代码审查、QA 和部署由主任务继续执行；本说明不声明全部需求已验收。

## 显式冷恢复 API

GET `/api/v1/resource-runs/work/{run_id}/configuration-recovery` 返回安全差异、当前确认阶段、confirmation_digest、can_confirm、manual_review、checkpoint_recorded。POST 相同路径提交 `{expected_phase, confirmation_digest}`，成功后原子插入已确认阶段、封存确认当时模型配置、将原运行排队继续。确认与执行之间再次更新配置不会替换已确认的配置；若无法安全使用凭据则再次等待确认。

接口仅供运行拥有者且具有 work.execute 权限。仅接受 error_code 为 RESOURCE_RECOVERY_CONFIRMATION_REQUIRED 的 PAUSED 运行，取消或仍有租约的运行拒绝。原普通继续入口不能跳过配置确认。确认摘要包括当前模型实际配置/凭据修订标识、历史快照、业务定义和检查点，过时确认返回 RESOURCE_RECOVERY_CONFLICT。

本期自动确认恢复限定为直接聊天模型配置。没有完整原检查点、出现旧审批或任何操作执行凭据、STARTED/UNKNOWN/未知调用、人工浏览器控制、已解析非模型依赖或路由接收方未重新核对时，返回明确 manual_review 原因，不假装恢复成功。检索/多模态模型、MCP/Skill脚本以及复杂审批运行需人工核对；相同接收范围的纯凭据轮换仍优先沿用原阶段，不走新阶段 API。

恢复成功只继续原 thread 检查点，不重新发送任务初始输入。resource_resume_pending 标记选择 graph.astream(None) 或原有全 True 的安全暂停恢复参数；旧写操作批准不得迁移到新阶段。确认服务端事务失败时不留下可使用的新阶段凭据缓存。已完成成果、未知副作用以及既有审批不会被自动重放。


## 模型快照原子读取修复

独立审查指出 ModelRegistry.get 分两次短事务读取模型主体和 supports_tools，可在并发保存时组合出不存在的配置。现改为单条 LEFT JOIN 读取。`test_model_snapshot_atomicity.py` 在首次读取之后通过另一数据库事务同时提交模型 revision/temperature 和工具声明变更；旧逻辑会返回 revision 1 + tools=False，修复后返回完整旧配置 revision 1 + tools=True，并证实落库运行快照保持该组合，下一次读取才返回完整 revision 2 + tools=False。作者隔离回归通过；这不是独立复审结论。
