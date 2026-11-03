# RDC 升级迁移独立审查

日期：2026-11-03。角色：独立审查者，未编写本报告涉及的 083 隔离 SQL、backup.py 改动、升级专项测试或运维文档。本报告是代码审查和定向隔离探针记录，不是全量 QA、部署批准或真实 dump/restore 验收。

## 当前结论

M-01（P1）已在作者重新冻结后复审关闭。本次限定范围未发现仍需修复的阻断缺陷；四项最终文件摘要全部吻合，独立十项定向 PostgreSQL 探针通过。此结论不替代完整 QA、真实备份恢复演练或生产发布门禁。

## 审查基线

已独立重算并核对作者 `migration-author-evidence.json` 中四项 SHA256，首次冻结及修复后再次核对均吻合。以下为最终审查基线：

- `services/api/migrations/083_run_resource_snapshots.sql`：`744ebac2f959052a9b6226864ba29be952d8ad5baea56d9b3a150262c154e8b9`。
- `services/api/app/backup.py`：`5a3bbc521461adf9ac67bb4561367773d6d5a1216401a1ba29b0831fd226f30e`。
- `services/api/tests/integration_tests/test_rdc_migration_upgrade.py`：`c6b9fcef28395524275461b071f165916b5c24795ea3701116c9310b29815013`。
- `docs/resource-reference-operations.md`：`cebbe5148847b2d2037a6027e796b315e22da25fa4ebb464747aeacdbe9eb6d3`。

另只读核对现有 `deploy-platform.ps1`、`deployment_gate.py`、`app.manage.migrate`、Work 暂停/恢复与工作区救援接口，确认操作说明与实际接口的边界。作者最终 13 项自验结果仅作为审查输入，未计为独立验证结果。

## M-01：历史已执行排队运行漏隔离（P1，已关闭）

原 083 的 PENDING 条件仅检查 work_approvals 或 operation_executions。独立创建 001～081 旧库，加入一条无审批、无操作凭据、无资源快照，但已有 KNOWN model_usage 的 PENDING Work 运行，再执行 082～084。实际结果仍为 PENDING。该记录已经具有过去执行证据，不能等同于尚未开始的干净排队任务。

这会允许旧模型/读取链在缺少历史配置的情况下自动冷恢复，并从当前配置建立首次阶段，无法证明与旧输出使用同一配置。根据根任务在本次审查中明确的范围，应对已有 model_usage 或持久检查点的旧 PENDING 同样隔离；没有任何执行证据的新 PENDING 保持原状。

作者已补充同 owner、mode=work 的 model_usage 和 work:<run_id> 持久检查点条件，并通过检查点表存在性判断兼容 SQL 提交后才 setup 检查点表的全新安装。临时函数在迁移完成后删除。独立重新执行 KNOWN/PENDING/UNKNOWN 模型记录和仅持久检查点四种反例，均准确进入 NEEDS_REVIEW；无执行证据的 PENDING 保持排队，原模型记录与检查点未被补造或替换。

## 独立定向探针

在独立随机 `_test` 数据库执行十项自写探针，用完只删除本次创建的测试库和恢复副本；没有操作生产库、生产备份、真实模型、浏览器或解码器。修复后最终输出为 `10 passed in 6.40s`。首次六项探针中的历史模型调用边界用于发现 M-01，修复后已改为验证应隔离结果，并增加持久检查点与关联工作流的独立反例。

探针源码位于本地证据目录 `services/api/.local/team-workspace/rdc/migration-review/test_independent_upgrade.py`。覆盖结果：

- WAITING_APPROVAL/PAUSED 无审批记录仍被隔离；PENDING 有已拒绝且过期的旧审批仍隔离；干净 PENDING、COMPLETED、CANCELLED、FAILED 和既有 NEEDS_REVIEW 未误变更。
- 原审批记录逐列保留；READY/UNKNOWN 沙箱只增加 retention_required，sandbox_id、状态及其他字段不变；RELEASED 未被伪装为可恢复。迁移不补造资源快照。
- KNOWN/PENDING/UNKNOWN 模型调用记录和仅持久检查点的旧 PENDING 均隔离；没有检查点表时仍可升级，临时检查函数不残留。
- 两种父子关联（work_run_workflows、external_workflow_jobs → api_credentials.parent_work_run）仅改变关联 RUNNING 工作流的状态和结束时间；已成功、已取消及无关运行不变，发布版本、输入输出和快照保持原值。
- 082～084 后强制 SQL 错误：新表、迁移版本记录及隔离状态一起回滚，旧 PAUSED 和原审批仍在。
- 旧备份 manifest 计数在新 baseline 迁移前比较，真实 PostgreSQL fixture 克隆配合原 restore_check 编排成功，恢复副本自动删除。
- 非法 execution_config 密文与可解密但非法 JSON 都拒绝恢复，并只清理本次恢复副本；原测试源数据不变。

恢复探针以受控 PostgreSQL template 克隆替代 pg_restore 子进程。它验证恢复编排、数据和失败清理，不证明 Docker pg_dump/pg_restore 工具链已演练。

## 运维与回退边界

现有发布准入仍拒绝活动 RUNNING/STARTED/SENDING 及特定未知外部执行；必须先走已有停止、核对和保留证据流程。隔离 SQL 对残留关联 RUNNING 工作流的覆盖不等于发布脚本可以跳过准入或强制改状态。

SQL pending migrations 使用同一连接事务，SQL 异常可一起回滚；后续检查点 setup 是独立步骤，其失败不能证明前面 SQL 已撤销。运维文档正确区分这两种故障。

SQL 已提交或产生新数据后，不能只恢复旧 Web 产物、删新列或直接让旧后台消费队列。应保全新执行阶段、配置历史、快照、通知目标证据以及旧审批和保留沙箱，在独立恢复库核对匹配的数据库、主密钥、文件与代码，再决定修复前进或经授权完整切换。文档没有承诺无损逆向 SQL，也没有把 restore 命令描述为覆盖生产。

上述是定向代码审查结论；真实离线完整备份恢复、生产发布和故障切换仍未验证。M-01 及当前限定范围审查已结束；后续实现变更需重新核对审查基线并执行适用验证，完整 QA 与发布由主任务单独推进。
