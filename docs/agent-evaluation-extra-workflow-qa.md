# DeepEval Workflow 专项测试启动与交接

日期：2026-11-04。角色：`expert-evaluation-workflow-tester`。本轮固定读取 HEAD：`71780807df30f95a5c6eb7f6dc6ad5b7b60e5018`，分支 `onwer_agent`。

本次仅完成启动、资料与源码只读核对、验收条件整理。未启动数据库、服务、沙箱、模型或业务任务，未执行 pytest，未修改实现和公共验收计数。原会话正在运行的 suite 由原执行者负责，本专项不重复派发、不抢占端口或沙箱、不修改其冻结原件。

## 已读范围

- `D:/project/my_agent/.cursor/agents/expert-evaluation-workflow-tester.md` 及 `expert-tester.md`。
- DeepEval 共享知识入口和 `docs/deepeval-learning/qa.md`。采用其事实优先、评分异常单列、缓存不当独立执行和缺证据不通过的要求。
- `agent-evaluation-checkpoint.md`、`agent-evaluation-remaining-requirements.md`。
- `agent-evaluation-workflow-review.md`、`agent-evaluation-approval-review.md`、`agent-evaluation-approval-cases.md`。
- `services/evaluation/src/evaluation_runner/fixed_run.py` 的 IMPLEMENTED、分派和 readiness；`fixed_approval_evidence.py` 的固定审批及原件封存逻辑。
- `services/evaluation/datasets/a20-fixed.json` 的 F05/F07 定义。
- 私有 `services/evaluation/.local/workflow-effects-review/target-expiry-v2/review.md` 及 manifest 头部；其内容是冻结候选及历史作者工程范围，不把它直接当作正式放行收据。

以上是阅读和静态核对，不将历史报告中的运行结果写成本角色亲自执行。

## 当前可确认状态

1. 当前 `fixed_run.py` 的固定 IMPLEMENTED 集合包含 F01/F02/F03/F04/F06，另加入团队用例；没有 F05/F07。readiness 对未实现用例列出处理器仍在实现的原因。因此不能从题集存在 F05/F07 推导其实际可执行。
2. 检查点记录 F05 的 body deadline、父期限记录、目标效果 hook 等私有增量已有工程审查进展；仍未合 active，默认 OFF/NOT_READY。后续 W 兼容重基 `31c48be9…` 的最终统一消费候选与真实 PG/target 合同仍待确认。
3. F07 当前题集包含 `not-json` 解析错误分支，同时声明超时 companion。原需求明确要求受控超时恢复/转支，不能以解析错误通过替代；检查点仍记 receiver 未完整接线。
4. 当前审批审查文档主要针对 W03/W04。可以参考其未知提交不重放、先封存再审批和 CLEAN 条件，但其 53/67 等工程通过数不是 F05 的审批验收。
5. 当前公共覆盖仍为 13/20，各一次；本次启动不增加业务案例数，也不改变公开执行入口 BLOCKED 状态。

## 首批优先验收

### 第一优先：F05 固定代码与审批

取得独审后的统一候选后，先核只读源码/定义/规则及预算身份，再安排独立工程验证，最后才在协调好的新环境执行实际 case。

实际执行必须分别保留草稿测试和已发布主运行：固定输入、代码、路径、时限及摘要；两次均走正式审批/代码执行，每个根各产生 counter1，合计两次沙箱。初始化成果不能作为主运行结果。

需要的反例和观察点：

- 参数、代码、目标路径、审批 payload 或摘要变化时拒绝；不能由 `approval_policy=none` 推导任意自动批准。
- 原审批响应 UNKNOWN 不得自动重发；已确认的显式幂等探针另记意图和计数。
- headers 按时但 body 或解析完成越过 deadline 时，不得标 CONFIRMED。
- 样本取消/总期限到达后，不再创建工作、发模型/工具/沙箱副作用；正在远端执行的效果另行核对，不能以本地 coroutine 取消证明远端终止。
- 目标实例重建不得获得新的 ACTIVE 时间窗；保留旧证据和未知状态。
- case300、总600创建授权、最多10次 create、并发1等各自核对，不把 HTTP phase timeout 说成绝对总期限抢占。

### 第二优先：F07 超时与错误分支

先完成产品契约与实际可路由 timeout 类型核对，取得完整 receiver/观察器/处理器的已审交付。解析错误、确定无写的超时、可能已有副作用的超时分别设计检查。

若接收器已产生写入而回执丢失，保留 UNKNOWN/待复核，不能走 fallback 宣称成功，也不能重放写入。模型用量未知亦不能归零后放行。超时 companion 缺失时 F07 保持未验收，不删除必测要求。

## 目前能并行做与仍阻断的工作

**可并行准备**：已封存证据只读重算、候选来源清单核对、断言/报告口径检查、独立测试用例整理，以及按授权分配的定向工程验证。所有新结果进入本专项独立目录，不覆盖历史原件。

**本轮不派发正式业务验收**：F05 尚无已交接的统一 active 候选，F07 尚未完整实现；原 suite 仍在运行。只有解决各自前提后才安排真实新库、新账号、新数据和 namespace，不通过关闭 readiness 或私有替换绕开。

需要回归 F01/F03/F06 时限定在实际改动影响，特别是固定版本、父子失败位置、并行分支顺序与根预算；不无目的重跑全部已验案例。

## 报告写入与清理边界

- checkpoint 仍将 Markdown 原子替换 WinError5 标为 OPEN，原持有者未知。后续需定位实际路径、写入者/句柄及受影响版本；自有句柄能重现同码只能说明机制，不能证明原根因已关闭。
- 保存失败必须保留错误、毒化不可消费证据并缺少 ready；不能只见 JSON 正常就忽略 Markdown/JUnit 或入库门禁不一致。
- 原件封存后由另一角色重算相同事实、用量、审计和 gate；不重跑 Agent，不重新导出覆盖原件。
- CLEAN 除快照稳定外，还要求已知终态、无活动租约/沙箱、无未知操作和未决用量。业务 FAIL 可以在已结清条件下回收，UNKNOWN/RETAINED 不能为删库改绿。
- 精确登记本批数据库、进程、端口、数据目录和 namespace；仅回收可证明归属的资源，不以 PID 数字或名称相似认定为自有目标。

## 下一次执行前的交接清单

1. 待测候选完整 SHA、所有改动文件及规则/题集/fixture 摘要，与当前 active 的基线差异。
2. 非作者代码审查与修复复审收据；私有冻结到统一候选的消费顺序及兼容说明。
3. F05/F07 各自 READY 条件与仍默认关闭的原因，明确本次只验哪个 case。
4. 专用本批资源登记、允许使用的沙箱配额和原 suite 释放/互不干扰的确认。
5. 允许执行的固定命令、预算、停止点、取消核对和清理规则；不接受自由脚本替换。
6. 已封存原件的位置与摘要、预期报告字段、独立 reader、预期失败/待复核的准确语义。
7. F07 的 receiver 事实观察及 timeout companion；缺一项则保留未完成。

首批建议先验 F05 的统一候选与工程门禁，F07 可同时准备 receiver 和用例审查；不要求两者互等，但最终由原测试汇总集成结论。本专项启动检查完成，后续领取明确候选后再执行。


## 新建 Workflow 专项 Agent 的首项离线核验（2026-11-04）

新子 Agent `expert_evaluation_workflow_tester` 已接手，与 Work 专项并行。按 focused-change 仅核角色分配范围；旧启动记录保留。没有重启当前W05或新建服务/数据库/沙箱，没有运行pytest或模型。

本批私有目录 `.local/team-workspace/agent-evaluation/qa-workflow-extra/offline-00c1b5e4f8014e8487f7ed3a1743608f`，`verification.json` SHA `e39e3b2c8d67360ee6c20f91d15b837e6d2a5e7056b2a6ff2b42009fed6aa4b7`。独立重新计算31c48包95资产、父255c包75资产、34705独审四组原始结果及顶层JUnit摘要，均一致、原件前后不变。9/65/62/28是核读既有工程结果，不是本角色实跑，不增加业务计数。

当前四个重基base匹配，catalog仅F05变更、其他19题相同，Gate driver不变。新增明确消费阻断WF-CONSUME-01：FixedFixtures当前34fa56名称修复与包记录27d728旧基线漂移；旧patched_fixed_fixtures仍是同case/prefix同名，整份消费会覆盖已修名称。需开发逐hunk重基deadline，复审后统一candidate/pins；caps fixture不能直接安装。发现属于静态兼容阻断，不伪称新的真实API复现。

已在`acceptance-cases-planned.json`和`findings-and-acceptance.md`准备13条F05/F07反例条件：审批/代码摘要、回执丢失不重发、headers及时/body迟到不CONFIRMED、实例重建不续期、取消后四效果围栏、原保存失败缺ready；F07解析错误、可信固定只读超时、普通GET不确定超时、已写或模型用量未知必须分开。全部为未执行用例；F05仍OFF、F07缺receiver到处理器/观察器/validator统一接线。WinError5仍OPEN，不把自造同码当原根因。

执行仍等待审查关闭的统一候选和主Agent具体资源准入。保留原case300/create10/并发1及授权批预算，独立资源，不与当前W05重叠。公共覆盖仍13/20，本专项新增正式业务验收0。

## WF-CONSUME-01 非作者审查与定向工程复测（2026-11-04）

审查对象为私有 `fixtures-consume-v1/manifest.json`，SHA `27ce5f64f05e2c129e04ae697779ce66140cacc54d3a095f1fd3364ffd2c56c5`。独立核算新11资产、旧31c48包95资产、父255c包75资产均一致；所有原件执行前后摘要不变。当前FixedFixtures仍为34fa56，私有候选3845c4。AST对比证明仅`mutate`变化，`agent`方法与当前完整同AST；相对旧提案只增加名称中的model_id，不覆盖717名称修复。

消费增量审查结论 **CLOSED_CONSUMPTION_INCREMENT_ONLY**：完整响应与JSON消费后、CONFIRMED前检查原deadline；HTTP/JSON失联进入UNKNOWN，deadline阻断保留SUBMITTING，二者均禁止再次提交；保留workflow_run_id确认缓存。真实AgentInput schema校验、固定同owner/name唯一409替身验证通过。该替身没有验证实际router/PostgreSQL权限，不将其表述为真实owner隔离或数据库业务验收。

独立目录 `.local/team-workspace/agent-evaluation/qa-workflow-extra/consume-review-20261104-4c38`。复制原7个新增断言、4个body/JSON期限断言和9个W兼容断言；三个测试文件字节一致，仅复制后的conftest父包位置改为绝对冻结目录。保留原网络/进程审计，不修改门禁或业务源码。

- 首次受限环境运行 `engineering_consume_cf5b29bc6cc1`：16通过、4失败。四条在deadline auth的`os.link`遭自然WinError5，尚未到body/JSON目标断言；原始JUnit、退出码、stdout/stderr保留，不删红或改为产品绿。
- 主Agent已明确授权主机工程复跑，`engineering_consume_99bee4c64c01`：原20条独立执行全部通过，0跳过、0错误、native exit 0，0.90秒。前后源码摘要一致，网络审计selfpair事件为空；没有真实API、数据库、任务、容器或模型调用。作者旧20通过没有计入本次独测。

收据 `review-receipt.json` SHA `8fe7788494c3375081dc5ebf8f1d9638d86802707baa4757ccb4dd43a18912a4`。本批新增正式业务验收0，不修改公共覆盖计数。仅关闭消费兼容问题；F05仍OFF、统一候选仍NOT_READY，F07仍未完整wired。原report.md保存WinError5继续OPEN；本次auth硬链接的受限失败不能证明其原根因。后续仍须主Agent统一候选消费、更新initializer/source pins、代码审查及独立真实资源分配后执行。

## 当前1694候选上的F05合入准备（2026-11-04）

对HEAD1694f928、initializer8f8851重新逐文件/函数比较冻结75+95+11资产，全部原件前后摘要一致。本批只产出离线交接，不执行工程或业务，不改active。目录 `.local/team-workspace/agent-evaluation/qa-workflow-extra/f05-merge-map-20261104-971d`，`merge-handoff.md` SHA `17bda45e63fefb6a3e09d81e247b100b068565be6208006a8def91c9352727fb`，`file-inventory.json` SHA `1f6263c6c4b663fe300ac91cc4724d94e59f0db5e7a74e67276cc76fb6f0771c`。21目标中8个缺失、13个有差异；每项都有active/proposal SHA和逐函数diff，供开发逐hunk消费，不能整份覆盖。

明确的新消费冲突：W包旧fixed_run若整份覆盖，将删1694的publish_error诊断及已有FAIL保留判定。只取F05分派/readiness/deadline/cleanup/describe/main分支，保留当前except诊断；FixedFixtures只取已审consume两hunk、名称修复不动；Gate driver当前6a8189字节见证、不再安装；capabilities最后刷新，旧fixture initializer c529与当前8f8851不符。

遗漏风险：suite最终应取封存supervised_evaluation_suite c313…的监督增量，patched_evaluation_suite ca638只是中间层；后者少F05同窗口Supervisor。另CodeSandboxes默认False、目标未传enabled、readiness要求True，原样消费仍不可运行。需开发单独交精确受审的F05 activation条件，不能全局翻True或伪造capability。W target候选夹带F07主动TimeoutError片段应隔离，仍不算F07 receiver接线。

交接包含8新文件精确来源、12源码/定义函数增量及1caps刷新、反例矩阵和后续顺序：统一候选审查→实际合入源码工程→真实隔离PG合同→主Agent容量派发后真实F05三root。没有扩大create10/并发1/case300/原全局600预算，正式业务新增0，F07仍未完整实现。
