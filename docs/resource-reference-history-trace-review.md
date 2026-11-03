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

## 第二轮：历史列表/详情契约最小改动复审

冻结文件：`services/api/app/resource_configuration_api.py`，SHA256 `417fec7e6173c8a6bac831b2686904192a039778ec7b8e164a56eb0d27128587`。本轮没有修改产品实现。

原 833 问题（列表返回 related_runs、详情缺失）在普通数据量下已修复。独立复现还验证，同一管理员可见全局模型历史时，related_runs 仅返回自己的运行：1 条本人证据加 2 条其他账号证据时，列表和详情均只返回本人 1 条。另两项既有 HTTP 鉴权/历史契约回归通过。

### [P2，待修复] 全局关联行数截断使列表和详情仍返回不同证据

位置：新 `attach_runs` 中 `ORDER BY a.id DESC,s.phase DESC LIMIT 500`，随后才对每个历史项执行 `[:10]`。

同一资源的较新修改记录关联超过 500 个本人运行时，全局 LIMIT 会在查询较旧修改记录的关联行之前耗尽。列表包含这条旧历史，但 related_runs=[]；详情只查询该历史 ID，因此可以返回同一条已存在的旧运行。仅抽取公共 helper 没有修复这个随列表上下文改变结果的契约问题。

真实隔离 PostgreSQL 复现：模型 revision 1 保存 1 条本人的已解析运行证据；更新至 revision 2 后保存 501 条本人的已解析运行证据。HTTP 列表中的 revision 1 记录返回 0 条 related_runs，详情返回 1 条，其余字段一致。没有真实模型请求，测试运行以“配置解析后、供应商请求前失败”标记，不虚构已调用状态。

建议：按 audit_id 分组取每项最多 10 条后再合并，使用稳定排序；不能先对所有历史项统一截断，再在 Python 中分别切片。提高全局上限只能推迟同一问题。

本轮结果 3 通过、1 失败；日志 `services/api/.local/team-workspace/rdc/runtime/owner_agent_rdc_runtime_a72ac4ed_test/result.log`。该失败为上面已确认的 P2，不是测试环境故障。数据库已删除，无生产、浏览器或真实供应商操作。

### 第二轮复审结论：P2 已修复并独立关闭

主任务提交新的冻结文件 SHA256 `02b8cb597a8acc012189452dc5b27a46e098154db5df135df2bd5278c3277469`，已独立核对吻合。attach_runs 改为按每个 audit 进行 owner 限定的 LATERAL 查询，按 resolved_at/snapshot_id 稳定排序各取最多 10 条，取消全局 500 条截断和 Python 后置切片。

重放相同的 501 条新修订关联边界后，旧历史项在列表和详情均返回 1 条证据；普通列表/详情完全一致、本人关联不混入其他账号、既有私有历史/权限 HTTP 回归均通过。本轮 4 passed（6.54 秒），日志 `services/api/.local/team-workspace/rdc/runtime/owner_agent_rdc_runtime_f7d9a144_test/result.log`，独立数据库已删除。本报告当前没有未解决的已确认发现。

## 第三轮：MCP 工具历史分类最小补丁独立复审

冻结清单为 `services/api/.local/team-workspace/rdc/history-classification/author-evidence.json`，清单 SHA256 `e0cdfb0a8a5eef376d6a741ea3db26345f5a35292d20aefde352fac59809531c`。三项文件 SHA 全部独立吻合；082 实现 SHA256 `11276d85d10b6fd04202b648fa44c5529389a62913e1887cbc5647d95904f5c7`。

独立阅读确认：比较只作用于 tools 历史分类，不改变执行、审批、真实接口校验或权限决策。工具列表按稳定工具名排序后比较权限投影和原始 schema 投影；顶层 description/title 和发现顺序变化归普通维护，name/enabled/read_only 变化优先归启停/权限，真实参数名和 schema 原始约束变化归兼容性待核对。后者是保守历史提示，不表示执行必定不兼容；没有在 SQL 中复制工具 Schema/ref 解释器。

独立真实隔离 PostgreSQL 探针验证 8 项：纯顺序、顶层展示文字、enabled、read_only、工具名、实际名为 description/title 的参数名变更、minLength 约束变更，以及事务主动异常后配置修订/历史一起回滚。验证了准确 1→2 修订、服务端操作者及 tools 内容只有摘要、私密标记和 schema 明文不出现在变化内容。8 passed（6.83 秒），日志 `services/api/.local/team-workspace/rdc/runtime/owner_agent_rdc_runtime_a649c66d_test/result.log`，数据库已删除。

本轮未发现新的已确认缺陷，分类补丁复审关闭。没有修改产品，也没有访问浏览器、真实 MCP 服务或供应商；本结论不是完整 QA、部署或真实 dump 恢复验收。
