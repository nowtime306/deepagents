# RDC 配置历史与运维独立 QA

日期：2026-11-03。验收角色未编写本报告范围内的配置历史、通知、仓库、追溯关联、083 隔离迁移及 backup.py 修改。本报告不验收该角色编写的 MCP Schema 规范化和索引实现；这些模块由其他角色独立验收。此前代码审查和作者自验不计作本次执行结果。

## 冻结与结论

正式最终基线为 `fcdc1dd1ef8022dfe80a4d20f4ebd972578b0a0b`。执行前核对 HEAD 完全一致且工作区干净；执行后 HEAD 未变，services/apps/scripts/infra/libs 无源码差异。测试期间未修改产品或测试源码。

最终结果：**66 passed，2 skipped，0 failed，0 error，0 deselected；40.31 秒**。本报告所列离线及受控协议范围通过，未发现该范围的未关闭 QA 缺陷；不代表真实供应商、生产界面、实际 Coding 工作区恢复或真实完整备份恢复已通过。

使用全新随机数据库 `owner_agent_history_ops_qa_f21dff4c7e_test`，先执行现有迁移再测试；测试数据目录独立。Windows pytest 启动显式使用 SelectorEventLoopPolicy。主 QA 库及升级测试自行创建的隔离库按各自保护条件清理；主库删除结果已确认。未读取或修改生产业务数据，未部署、构建 Web 或访问被拒绝的 3100/3131 页面。

## 实际执行范围

以下数量来自本轮 JUnit 的实际 testcase，不按作者报告或原测试数量推算：

- `test_rdc_configuration_history.py`：9 项通过。覆盖成功修改历史、CAS 冲突不产生成功记录、默认切换关联、删除后保留、秘密值脱敏、资源可见性及路由维护/接收方授权边界。新增三项工具历史分类全部执行：说明/title/发现顺序变化为普通维护，read_only 变化为启停/权限变化，required 约束变化为兼容性变化。
- `test_rdc_history_http.py`：2 项通过。通过实际 TestClient HTTP 验证认证操作者不可由请求头伪造、关联变更标识、列表/详情一致、只读权限和跨账号隔离；通知目标确认绑定用户查看的目标，期间再次变化时拒绝，新投递保留 retry_of，原记录保持待核对。
- `test_resource_trace.py`：6 项通过。执行受控模型运行后读取实际资源修订/调用关联；当前差异不改写旧快照；旧运行缺信息不补造；排队路由在执行开始解析；模型能力变更历史可关联实际运行；嵌套子工作流可定位共享父范围与业务版本。模型输出是替身，子工作流关联使用受控已保存记录，不证明真实模型效果或完整嵌套流程业务体验。
- `test_automation_notifications.py`：6 项通过。实际随机本机 HTTP 接收器验证签名 webhook、飞书/企业微信/Slack 请求结构、凭据保护、通道维护、结果未知不自动重发、通知去重/已读及签名事件篡改/重放/暂停边界；不是第三方供应商联调。
- `test_repositories.py`：17 项通过；`test_repository_actions.py`：12 项通过；`test_repository_git_protocol.py`：1 项通过。覆盖仓库范围、加密凭据、固定提交与 Git 对象校验、子目录/删除/文件模式、审批摘要与实际字节/目标、权限变化、未知远端响应不自动重试、只读核对和 PR/CI/issue 证据。协议验证使用受控 GitHub 端点替身及新建临时原生 Git 仓库，没有对项目仓库或真实 GitHub 推送/创建 PR。
- `test_rdc_migration_upgrade.py`：9 项通过。实际建立 001～081 旧数据、加密凭据、原业务发布、审批/操作凭据及 LangGraph 检查点，再升级 082～084；验证模型调用/检查点旧队列隔离、干净排队和终态保留、沙箱 retention、旧审批拒恢复、旧向量未知、旧通知无外发、新证据表计数、强制 SQL 失败回滚以及无检查点表升级。
- `test_workspace_backup.py`：4 项通过。验证旧版可选表与 manifest 指定表的区分、扩展计数、恢复副本保留凭据资料且不重放媒体/登录、旧 Ask/邀请隔离。升级测试中的 restore_check 由真实 PostgreSQL template 克隆替代 pg_restore 工具，验证原 manifest 在 baseline 迁移前比较；没有执行真实 Docker dump/restore。

这些结果支持 RDC-05/06/11/12/13，以及 RDC-07 接收方维护和升级保留边界的对应条目。RDC-14 本轮只验 HTTP 数据、权限与定位信息，未据此宣称实际界面通过；不以总数量代替需求覆盖。

## 跳过、阻断与未验证

本轮显式 `RUN_LOCAL_SANDBOX=0`，以下两项保持 skipped，未计通过：

- `test_repository_workspace_real.py::test_native_git_commit_dirty_delete_ignore_bundle_and_process_quiesce`。
- `test_repository_workspace_real.py::test_frozen_repository_fixed_workflow_code_and_approval_restore`。

实际 Coding 沙箱验收由主任务另行执行；镜像构建成功不能替代以上实测。本报告不借用并行测试结果。

真实 GitHub、第三方通知和外部模型未执行；协议替身结果不计作真实供应商通过。生产页面仍受既有权限拒绝约束，本轮没有重试或改入口绕过；真实界面体验、生产部署、完整离线 dump/restore 和故障切换没有本报告的通过证据。

## 原失败与修复重验

首次基线 `833a4ac212b2103d05e3a73e352d27af74d52e07` 已实际核对为干净源码：

1. 初次测试启动使用 Windows 默认 Proactor，异步 psycopg fixture 连接超时，业务断言尚未执行。仅在本地 QA 启动器显式选用 Selector 后解决，未改产品或冻结测试。
2. 随后实际发现历史列表行有 related_runs，而变更详情缺该字段；`test_http_actor_default_link_cas_redaction_deleted_history_and_cross_owner` 失败。按 -x 停止并立即报告，未放宽断言。
3. 根任务授权在旧基线排除这一个已知失败用例继续其余范围，结果 62 passed、2 skipped、1 deselected；该排除用例没有被算为通过。
4. 根角色修复列表/详情共用关联查询并重新冻结后，本轮完整重跑恢复该用例，并纳入新增三项历史分类。最终 66 passed、2 skipped、0 deselected，该接口缺陷本轮重验关闭。

## 可追溯证据

最终执行命令、冻结检查、耗时与按模块结果：`services/api/.local/team-workspace/rdc/history-operations-qa/owner_agent_history_ops_qa_f21dff4c7e_test/evidence.json`；同目录 `results.xml`、`result.log` 保存完整结果。没有数据库连接串或生产凭据写入本报告。

原环境错误、接口失败和排除续跑日志分别保留在同一父目录的 `owner_agent_history_ops_qa_0bd72d20ec_test`、`owner_agent_history_ops_qa_ea4bdf970f_test`、`owner_agent_history_ops_qa_0e7830c986_test`。这些目录只是日志/测试材料，相关数据库已经删除。

源码冻结后若再改变相关产品或断言，本结论需按受影响范围重新执行。主任务继续汇总其他非作者 QA、真实执行环境和界面限制，并独立决定发布门禁；本报告不是部署批准。
