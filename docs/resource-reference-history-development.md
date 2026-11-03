# RDC 配置历史与连接边界实现说明

范围：RDC-05、RDC-07、RDC-11、RDC-12；与运行快照模块共同完成解耦。本文为开发说明，不代表独立审查、验收或上线完成。

## 成功标准与实现

1. 配置提交与历史一致：082 扩展既有 `audit_events`，通过 PostgreSQL 事务触发器记录字段差异。失败回滚、CAS 未匹配不会留下成功记录。模型默认标记的隐式联动在同事务、同服务器生成的 change_id 下记录。删除保留历史，已有数据仅记明确 BASELINE，不重造过去。
2. 脱敏与权限：仅显式字段白名单可显示；URL仅 origin 和摘要；命令、工具结构、仓库自由字段仅摘要；凭据仅设置/更换/清除、configured 和配置修订。历史不保存密钥、密文、请求头、完整通知地址或自由命令。全局模型/MCP/路由仅管理员可看；私有资源仅 owner 可看，管理员不能因此跨 owner 阅读。API 还须执行当前功能查看权限。
3. 操作来源：HTTP middleware 开始/清理 `begin_context/end_context`；`security.identity` 认证授权成功后调用 `set_actor`。连接池在每个事务写入受控 actor/change_id。客户端不能设置 actor。后台可 `begin_context(source='system:具体任务')` 标明来源，默认 system:application。
4. 路由同意：digest 覆盖接收方 ID、完整目标摘要和数据范围。名称、密钥、配置修订、健康阈值、候选排序不使同意失效。新增接收方或目标/范围变化需要新同意。已选运行先读取已冻结模型，再校验选择、路由启用和同意撤销；不会自动改选候选。运行快照保护已冻结原地址；无快照的旧运行缺少目标证据时要求确认恢复。
5. 仓库：恢复/调用按 slug、ref、subdirectory 比较身份，显示名、密钥、通用修订变化可继续。固定 source 中的提交/tree/文件证据不被覆盖。已批准动作仍绑定任务分支、源码提交、成果摘要和具体操作。
6. 通知：新消息冻结目标 identity 与消息字段；投递同目标使用当前密钥。名称/密钥更新不误伤；目标变化进入 PAUSED，不改投。UNKNOWN 不自动重发。显式 retry 创建新的投递并通过 retry_of 指向原记录；改目标重发需 confirm_target，历史缺少冻结消息/目标不自动补造。

MCP 工具历史按工具名排序比较：`name/enabled/read_only` 变化归“启停/权限变化”，否则 `name/schema` 原始结构变化归“兼容性变化”；仅工具 description/title 等顶层展示信息变化归“普通维护”。工具数组顺序不影响上述分类，历史正文仍仅保存摘要。schema 原始差异采用保守分类，表示需要核对，不等于执行不兼容或强制重新发布；实际执行与审批仍使用工具模块的约束规范化检查，SQL 不复制其 schema/ref 解析。

## 接口

- `list_history(database,user,resource_type,resource_id,limit=100)`；`history_detail(database,user,id)`。
- resource_type：model / mcp / route / knowledge / memory / repository / notification。
- 返回 changes 为 `{字段:{before,after,credential_change?}}`，包含 categories、source、actor_id、change_id、baseline、before_revision/after_revision。
- `GET /notifications/deliveries` 增加 retry_of、target_summary、current_target_summary。摘要为 `{kind,host,identity}`，界面可对比 host 和 identity 前12位；无摘要应标“历史未记录”。
- retry 请求 `{acknowledge_duplicate:true,confirm_target?:true}`；返回新投递 ID。原失败/未知/暂停记录保持不变。

## 数据升级与回退

082 给没有通用配置修订的 MCP/知识库增加 configuration_revision，独立于知识库 processing_revision。既有配置只记迁移基线；旧路由选择/旧投递缺少可信接收目标证据时走确认恢复，不能以当前值冒充历史。既有通知通道得到不含原地址的稳定内部目标标识，后续同地址维护保持此标识。

上线前由主任务按既有流程备份并验证迁移。保留新增审计/目标/快照字段，不以删表方式回退；回退旧代码前须停止新投递并保留 PAUSED 记录及恢复关系，不能将 PAUSED 批量改为 PENDING。迁移未提供自动 DROP 回退，以免损失已经发生的证据。

## 开发自验

随机 `owner_agent_rdc_history_<随机>_test` 从空库执行全部迁移，结束强制回收限定名称库；只复用本地数据库连接凭据，不连接生产业务库。使用独立临时 CredentialVault，不使用生产密钥；不访问页面。

新增 `test_rdc_configuration_history.py` 六项：事务回滚/CAS、默认双侧同 ID、删除保留/跨 owner、URL/密钥脱敏、路由维护/接收方/撤销、活动运行冻结原目标、通知冻结正文/current key/目标暂停/UNKNOWN 不重发、仓库身份范围检查。首轮因 Windows 默认 Proactor loop 不支持 psycopg 出现 fixture 错误，调整测试 runner 为 Selector policy 后六项通过。现有路由/通知回归测试同步更新到新语义，真实外部供应商效果不在本次替身验证范围。

2026-11-03 MCP 历史分类补丁作者自验：新增 description/title、read_only、required/schema 三类数据库用例，各自同时重排工具数组，验证分类、真实修订、受控操作者与工具内容摘要脱敏。随机 `owner_agent_rdc_history_class_a9c68a4912_test` 空库执行全部迁移，文件合计 9 passed（0.63s），测试库已回收，Ruff 通过。首轮 runner 在 pytest 前导入迁移依赖触发 anyio assertion-rewrite 警告，未进入测试；改为独立迁移子进程后通过，首轮证据保留。日志位于 `services/api/.local/team-workspace/rdc/history-classification/`。这是作者自验，等待独立审查，不替换冻结 SHA 833a4ac 的既有独立 QA 结果。
