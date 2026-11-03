# RDC 修改历史与运行追溯独立审查

审查范围：082 配置历史触发器、历史查询与权限、数据库审计上下文、仓库身份与通知投递解耦、模型路由历史与实际开始解析、运行追溯 API，以及 ConfigurationHistory、RunConfigurations、EmbeddingCompatibility 和既有页面挂载。依据 `resource-reference-decoupling-requirements.md`。没有审查本人编写的 recovery/work_engine 实现，也没有修改被审查实现。未访问此前被拒绝的页面或真实服务商。

## 初审发现（主任务已修复，见最终复审）

### [P2] 子工作流详情将父运行已有的实际资源证据显示为“历史未记录”

位置：`services/api/app/resource_trace_api.py:105`，尤其 109～117 行按当前传入的 mode/run_id 查询阶段与快照；`apps/web/src/features/ask/workflow-run-trace.tsx:31` 以子 workflow run ID 调用该接口。

Ask 工具创建的 workflow_runs.snapshot 保存 `parent_ask_run`，实际执行传入父 usage_context，资源快照和模型调用记在父 Ask 根运行。同理，Work 中的流程使用父 Work 根运行。追溯接口虽正确验证了当前工作流拥有者，却没有解析这些已有父运行关系，而直接查询子工作流的 `(workflow, child_id)`。

隔离复现：发布包含 LLM 节点的流程，通过 Ask 工作流工具执行；父 Ask 追溯返回 1 个带 used_at 的模型快照，子工作流追溯返回 HTTP 200 且 items=[]。用户从现有工作流详情查看时被告知“历史未记录”，但实际证据已存在，无法按 RDC-06 定位本次调用配置。

建议：区分详情所代表的业务运行与保存实际资源的根运行；在分别验证拥有权和相关查看权限后沿 parent_ask_run/parent_work_run 读取关联证据，返回清楚的根运行与调用关系。不能将无法读取的父证据伪装为该子流程从未记录。

### [P2] 工具能力修改历史丢失所属模型配置修订与当时名称

位置：`services/api/migrations/082_resource_configuration_history.sql:70`、`:71`、`:99`、`:107`，以及 `services/api/app/resource_configuration_api.py` 以 after_revision 关联运行的新增查询。

模型工具能力保存在 model_capability_declarations；该表只有 model_id/supports_tools，没有 revision、configuration_revision 或 name。通用触发器直接从该表 OLD/NEW 取这些字段，因此独立生成的 supports_tools 记录 before_revision/after_revision 均为 NULL，resource_name 退化为字面值 model。即使同一次 ModelRegistry.save 已推进所属模型修订，能力记录仍没有对应修订。

隔离复现：使用管理员模型创建 API 传入 supports_tools=true，修改记录中的 supports_tools 项两个 revision 都是 NULL，显示名称是 model。新增历史→运行查询使用 `s.revision=a.after_revision::text`，因此这些能力变更永远无法关联实际模型运行。排查 MODEL_TOOLS_UNVERIFIED/UNSUPPORTED 时缺少需求明确要求的配置修订证据，影响 RDC-05/06。

建议：能力变更与所属模型配置变更合并成同一可追溯修订，或为声明记录显式取得准确的所属模型前后修订和名称；不能用当前配置补造旧记录。覆盖创建、后续能力编辑及并发保存。

## 审查中已修复并独立复核的发现

### [P1，已修复并复核] 同一模型先直接调用再首次经路由调用会被普通维护中断

初查时 model_routing.resolve 将候选当前 revision 与 models.get 返回的运行冻结 revision 强行比较。同一个运行先解析 A@1，维护到 A@2，再首次使用选择 A 的路由时会抛 ROUTE_CHANGED，违反 RDC-04/07。

初次隔离复现返回 `('ROUTE_CHANGED', 1)`。主任务作者修改为以实际冻结配置写入选择和接收方摘要后，独立复核返回 `('ok', 1, 1)`，证明该路径复用原运行配置。

### [P1，已修复并复核] 向量兼容确认未绑定管理员所见的新服务目标

初查时 EmbeddingCompatibility 只提交 previous_base_url/confirmed_model/dimensions/evidence。页面核对 A→B 后，另一管理员将同一模型改到 C，提交旧页面仍返回 200，兼容证据被写到未核对的 C。服务端事务内的配置复查只覆盖请求读取后的竞争，无法绑定用户看到的 B。

初次隔离复现返回 200，evidence.target_key 等于 C 的 contract_key。主任务作者增加 expected_revision/expected_base_url 并由页面提交后，独立复核旧页面提交返回 409，且没有写入对应兼容证据。

## 验证证据与边界

- 使用随机独立 `owner_agent_rdc_runtime_*_test` PostgreSQL 数据库和受控模型；数据库已删除。
- 最终独立复现/复核脚本 4 项通过；这些测试分别证明上述缺陷或修复结果，并不等于所有需求通过。
- 日志：`services/api/.local/team-workspace/rdc/runtime/owner_agent_rdc_runtime_a89998e3_test/result.log`。
- 新增关联运行查询已检查 owner_id 限定与原地展开；它不会绕过运行详情 API 的拥有权检查。工具能力记录 after_revision=NULL 导致关联缺失的问题已包含在第二项。
- 未发现本轮范围内可确认的凭据明文输出或跨账号读取路径。历史只读组件的错误态会隐藏旧列表；运行配置恢复动作仍依靠后端执行权限检查，因此没有把只读界面可见的“查看恢复差异”按钮单独列为越权缺陷。
- 没有执行浏览器验证、真实供应商调用、部署或 Git 提交。初次子流程复现遗漏替身注入，尝试了测试配置的无效地址并失败；补齐替身后重新复现，报告采用最终受控结果。


## 最终复审

本节为当前状态，前文保留初审与修复依据。主任务作者修复后再次独立阅读并在新的隔离数据库复核，本报告列出的 2 项 P1、2 项 P2 均已修复；目前没有本审查范围内仍未解决的已确认发现。

- 路由同运行混用直接调用与首次路由：继续复用 revision 1，选择记录亦使用实际 revision 1。
- 向量兼容过时确认：按页面所见 expected_revision/expected_base_url 返回 409，不写入错误目标的兼容证据。
- Ask 子流程追溯：父运行与子详情各读取到同一模型证据，configuration_scope 明确指出 Ask 根运行。
- 追加检查 Work 工具/API 子调用：作者补充 external_workflow_jobs → api_credentials.parent_work_run 关系，并对凭据与父运行 owner_id、父 Work 查看权限作校验。隔离关系复现中父/子各返回 1 条相同配置，configuration_scope 正确为 Work 父运行。不是只覆盖 work_run_workflows 绑定入口。
- 模型能力历史：创建时显示所属模型名称与修订；再次仅修改 supports_tools、其他模型字段完全不变时，实际记录已从错误的 2→2 修复为 1→2；after_revision=2 的能力历史成功关联实际使用该修订的本人运行。
- UI 的共享父运行提示与 can_recover/can_view_global_history 守卫已作静态复核。没有浏览器执行证据。

最新证据：`services/api/.local/team-workspace/rdc/runtime/owner_agent_rdc_runtime_b8b3b050_test/result.log`，对应 5 项独立复现/复核检查通过；同次命令还包含作者自行编写的 ModelRegistry 原子读取回归 1 项，合计输出 6 passed。该作者测试不作为本报告的独立审查证据，原子读取修复仍须由原审查者另行复审。数据库已删除。
