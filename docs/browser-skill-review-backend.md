# BrowserSkill 后端独立代码审查

审查日期：2026-11-03。审查对象为 `onwer_agent_browser_skill` 的 `backend-freeze.json` 19 项；不包含并行编写的前端与运维控制脚本。不修改业务实现，不提交代码，不访问任何应用页面或真实用户浏览器。

结论：**修复后复审通过，可交独立测试。** 初审发现的 BSK-BE-01 已关闭；下面保留问题及初始证据，末节记录复审结果。开发者真实扩展组合证据和本审查均不替代后续独立 QA、前端审查及部署门禁。

## BSK-BE-01：取消和异常清理期间过早释放专用浏览器连接（P1）

位置：`services/api/app/browser_skill_api.py:134`，`services/api/app/browser_skill_runtime.py:246`，以及 `:250` 开始的异步清理。

`finish(cancel=True)` 在调用并等待 `runtime.stop()` 之前，将记录提交为 `cancelled`。但录制进程、扩展会话、共享 BSK_HOME 的停止和清理尚未完成。唯一忙索引、`runtime.status()` 和 Work 工具都仅以 `starting/recording/stopping` 判忙。因此，同期另一个管理员的新录制或已经批准的 Work 可以占用同一连接。旧取消操作随后执行无录制 ID 参数的 `record stop`，或删除共享 `record-recovery.*`，可能干扰新录制。权限撤销/其它异常路径同样先写 `failed`，然后才在 finally 中异步停止存活进程，存在相同窗口。

最小反例调用真实 `browser_skill_api.finish` 和 `BrowserSkillRuntime.status`，只替换身份、DB 和停止进程为受控替身，不运行浏览器或 CLI。让 stop 挂起后检查连接状态，实际输出：

```json
{"cancel_not_finished": true, "persisted_state": "cancelled", "connection_ready_for_new_work": true}
```

复现文件：`.local/team-workspace/browser-skill/review/reproduce_cancel.py`。使用现有 Python 环境、`PYTHONPATH=services/api` 执行，退出码 0。该反例验证的是错误行为存在；修复后应转为验收断言：停止及清理未确认时仍保持 busy，后续录制和 Work 均拒绝，确认进程退出且清理完成才允许复用。

建议最小修复：所有取消/失败清理期间保持数据库 `stopping`；终态只在清理结束后提交。清理无法确认时设置持久恢复隔离标记，拒绝连接复用。重复取消要幂等，不能因第二次请求改变原停止意图。不要仅修改 status 的前端显示，DB 唯一索引及 Work 跨进程判断也需继续有效。

## 已核对的行为

- 录制详情/列表/变更均受 owner 约束；本机 live status/start 明确要求管理员。普通用户不获得连接信息。
- 新运行的 `allow_local_browser` 默认 false，只对管理员 browser 运行允许 true；自动运行显式清除快照授权。Work 注册工具和逐步 guard 均检查运行列与冻结快照双授权、当前管理员、权限、租约和取消。
- 执行使用现有 OperationJournal，审批 digest 包含版本、完整参数、边界、名称和步数；先消费单次凭据，再创建会话。失效/不匹配凭据不调用浏览器。
- 每个交互动作经过新的 strict sync；按 role/name/ctx 唯一匹配当前 ref；观察截断、人工控制和歧义失败即停止，不复用旧录制 ref。
- 导入仅保留四类安全结构，fill/select 原值转为必填参数；密码等阻断发布；发布从 DB 安全草稿生成私有 Skill，不接受客户端篡改操作数组。
- 顶层 HTTPS 域约束在工具说明、审批 metadata 和合同中明确不等于网络防火墙。没有把它宣称为原隔离浏览器的网络边界。
- CLI 使用无 shell 参数数组；Windows Selector 下在线程运行 Popen，launch 被取消时回收进程；command 超时/取消杀死该进程。没有因审查启动实际 CLI。

## 验证边界

本审查完成静态调用链核对和上述异步取消反例。未重新执行作者真实扩展专项，未运行新应用 UI、全系统 QA 或部署；这些应分别进行。未对无证据的潜在情形提出阻断问题。

## 修复复审

复审冻结清单：`.local/team-workspace/browser-skill/backend/backend-freeze.json`，SHA256 `d050a77963fa61e65a5b2627b5d8104f0f931bd8b6660c9dde3c69e43c9c635f`。20 项文件在验证前后均与冻结摘要一致；独立留档为 `.local/team-workspace/browser-skill/review/backend-reviewed-source.json`。

- **BSK-BE-01 关闭**：取消请求保持 DB `stopping`，仅向唯一录制 job 发停止/取消信号；job 完成进程退出、session-state 释放和原始文件清理后，才更新终态。异常、取消、自然退出共用 finally。重复取消不启动第二条停止命令。无法确认进程/会话清理时保留 `stopping` 并落持久隔离标记；重启 recover 同样不提前释放 busy。
- 原取消反例保留相同真实 finish/status 调用与相同挂起点，仅将错误行为断言改为预期安全行为，实际输出 `cancel_not_finished=true,persisted_state=stopping,connection_ready_for_new_work=false`，退出码 0。
- 独立重跑 lifecycle、API、Work guard 和 trace 四个相关模块：**29 passed in 8.07s**，后台和测试源码摘要不变。lifecycle 使用隔离真实 PostgreSQL，停止进程为受控替身；覆盖正常取消、撤权异常、清理未知，忙窗口新 start/Work 均拒绝、重复取消单次 stop、重启仍隔离。没有启动实际浏览器或读取生产用户数据。
- 发布元数据修复复核：用户 title/description 通过 `json.dumps` 编入 YAML quoted scalar，用户参数说明以 JSON 编入正文；固定 `browser-path-<id>` slug 和 `run_browser_skill` 名称/边界不受用户文字替换。恶意换行、引号和 frontmatter 分隔符反例通过 `parse_skill` 往返校验，标题、用途、参数说明可保留而不注入 name 字段。

这次复审没有新发现的可证实阻断问题。批准范围为该 20 项后端冻结代码交 QA；不代表并行前端/ops 脚本已审查，也不是应用完整验收或上线批准。

### 纯文档一致性整理

后续静态复核将合同开头 stop/cancel 概述的旧终态措辞与最终 BSK-BE-01 合同对齐：重启未确认时保持 `stopping/BROWSER_INTERRUPTED` 并隔离连接，不提前写 failed 释放连接。该整理没有改变 API、运行时、迁移或测试代码；其余 19 项冻结摘要一致，后端审查通过结论不变，无需重新运行测试。

合同当前 SHA256 为 `319568b5b079c85616fcc87f4164ffd6aedd44db929702f710a4409c6bc9870d`。独立留档仅更新此文档的摘要/字节数，追加原测试时文档摘要与本次整理说明；前述原测试冻结清单摘要和 29 项测试证据保留。后续开发提交可标注“待页面验收”，本说明不批准正式验收 tag 或部署。
