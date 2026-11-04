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

## 统一候选f700非作者代码审查（2026-11-04）

按用户“审查通过后才交测试”，本批仅静态审查、不导入候选、不运行QA。active HEAD30916664；manifest f7000088…的20base/candidate、35review assets、5protected摘要均匹配且前后未变。partial/既有FAIL异常handler、稳定agent名称、Team ProfileState/model_factory AST、catalog其他19题均保留；Gate及public report reader未覆盖，F07片段已隔离，caps仍待最终刷新。

结论 **REQUEST_CHANGES**：D02 guard.cancelled()未证明same-suite归属就消费父cancel.request并读取suite.json；合法standalone父目录有无归属marker、无suite.json时，读取异常经capability转持久POISON，导致四hook误拒。对foreign marker提前proof.safe亦属同一归属顺序问题。开发需区分未归属和此前已确认本suite后损坏；不能一律吞文件异常来修复。仅为静态控制流推导，未声称执行复现。

私有 `.local/team-workspace/agent-evaluation/qa-workflow-extra/f05-unified-review-20261104-e650/review.md` SHA `c4a9080d07cb902fe17f7bc83cfd50b03641de4fafbcb05e53d018c845c4df04`，收据同目录review-receipt.json。另记录STARTED原实例篡改检测/独占启动和跨实例删除唯一sentinel的覆盖边界，等待开发明确后补反例。修复后新封包复审，通过才QA；D02未关闭，工程及正式业务新增均0。

## v2复审及审查后独立工程（2026-11-04）

新manifest `a8bed11264d8a19e8d14d2c3336f0b1344b294da17c9a6bc0871a83327418cf7` 的R1已静态关闭：首次无法完整证明父suite归属时不消费其marker；同一live guard完整匹配并缓存归属后，元数据消失/破坏/错配仍poison；owned result优先不变。20active base、v1的35/v2的15review assets及protected5匹配；19产品映射与v1同字节，partial/FAIL、name、Team/Gate保留。静态收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/f05-v2-review-20261104-23d1/review-receipt.json` SHA `aee462fef979f6669ef91f96779899c932b14556ea2536fb4b446780026ccba9`。原f700失败收据保留。

主Agent明确确认代码复审通过后，才执行本批独立工程。新目录 `.local/team-workspace/agent-evaluation/qa-workflow-extra/f05-v2-qa-20261104-77b8`，从v2复制141个顶层文件，产品源码和全部原断言字节保持；每组新HOME，按原网络/进程守卫，仅允许标准库自管socketpair，其他外部连接/进程被禁止。使用已授权主机工程环境，不修改源码来避开文件系统权限。

独立执行结果如下，均native exit0、JUnit失败0/错误0/跳过0，包含原正例：

- activation43：`engineering_activation_c7517b6fa95f`，5.31秒。
- guard62：`engineering_guard_7694d7040b36`，5.45秒。
- client65：`engineering_client_7671bd47f882`，9.67秒。
- profile14：`engineering_profile_5220b6cb34ab`，4.06秒；沿原获批组选择，不改断言。
- consume7：`engineering_activation_84d0149f88a5`，0.18秒。
- W兼容9：`engineering_rebase_9ee2e655c541`，0.48秒。
- budget3：`engineering_budget_ef7618b899c2`，0.12秒。
- coordinator/监督8：`engineering_coordinator_4931d7c7b5bf`，0.10秒。
- partial边界8：`unit_6629be96bc29`，0.17秒。

收据 `independent-engineering-receipt.json` SHA `7b4a2f3a1a6cd344634ac498db53e674063eda0e4c4a8d5dafe501d7c4e5a3b1` 含逐组精确argv、JUnit/原始stdout/stderr/result/network摘要及源前后见证。复制的141文件与原源前后逐字节一致，原件、active/protected pins无漂移。各组有重叠断言，不相加冒充独立业务案例。

activation中4条safe路径拒绝属于既有proof.safe失败注入，只验归属顺序；物理symlink原WinError1314未验收、失败保留。未使用真实DB/target/SDK/manager/容器/模型，正式业务新增0。尚未验收真实PG归属/事务/lease合同和真实F05三root；F07仍未receiver统一接线。Chrome站点仍拒绝，未尝试UI或部署；caps fixture未装，最终源码pin刷新/资源准入仍待主Agent，READYfalse。历史MD保存WinError5原根因未由本次工程关闭。

## v3已知F05关联缺失的静态复审（2026-11-04）

新manifest fe976fb5…仅CodeSandboxes.reserve两hunk改动；有效leased run为SQL主行保owner/model，owned workflow snapshot改相关子查询，缺关联/foreign关联返回NULL仍识别为fixed并在native reserve前按exact graph拒绝，不降普通2。20active bases、其余19产品同字节、v2 guard及protected5均匹配。静态审查通过/known-fixed问题CLOSED，未执行新QA。无效lease原底层None/refund/诊断路径保留，不额外要求不得调用底层；Browser拒绝是正式原基线，不计新回归。

收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/f05-v3-review-20261104-9d23/review-receipt.json` SHA `fed773feb785694e883aa3fa6df5d5c782b5e62a10266a9c9a60c92bb3cb63de`。六作者反例仅SQL结果替身，真实PG查询/owner/lease合同待验；v2工程不回填v3。新六条和activation/profile窄回归仍等主Agent准入，正式业务新增0。

## v4固定模型权限分类复审与窄工程（2026-11-04）

v3封存后开发追加识别到同类foreign owner/role分类缺口，故本角色没有启动v3六例QA。v4 manifest `b3eb5c1801296b1362db4ddc3ef2328b9cecdff2b1df75af4211a176f0a4bfad` 仅reserve继续最小修正：单次profiles.lock复制binding，以case_id识别knownF05后再严格校验lead/runOwner/guardOwner，foreign不再降普通2；v3主行+owned关联子查询保留。19产品/v2guard同字节、20active bases/protected5核一致；AST仅reserve变化，静态审查CLOSED。Browser仍按旧基线拒绝，未扩需求；无效lease允许原None/refund/诊断。

主Agent本批预授权“静态关闭后可执行”后，独立新目录 `.local/team-workspace/agent-evaluation/qa-workflow-extra/f05-v4-review-qa-20261104-226b` 运行原admission10、activation43、profile14，分别4.10/5.34/3.43秒，全部native0、JUnit失败0/错误0/跳过0。145复制文件、源码及断言前后与封存v4逐字节一致；active/protected原pins无漂移，每组新HOME、原守卫和原始日志保留。静态收据SHA `1ef6fb7c66bc0934ed5083c33d4c4fd9029d7763e6189f01cbb210ab2634395b`；独立工程收据 `independent-engineering-receipt.json` SHA `f20774a3fc91546e23b861f3df0b975efbcdfeb8c72a6c5d21627e0c75a486eb`。

没有重跑不受影响的62/65组，它们仍仅v2结果，不回填v4。新10为SQL结果替身，真实owner/关联/lease查询和PG7待验；4safe注入不算物理链接。未用真实DB/SDK/target/manager/容器/模型，正式业务新增0。最终源码合入/pins、真实F05三root、Chrome UI/生产构建/部署、F07 receiver与原MD保存故障边界均未由此关闭，READYfalse。

## 正式可移植测试源码审查（2026-11-04）

封包manifest76dbc87f…完成非作者静态审查，PASS，未collect/导入/执行。16映射及8assets摘要匹配；14源测试文件342条assert AST逐条保留，测试名、参数化/skip边界未删改。产品改普通导入，唯一helper精确加载正式两脚本；bootstrap改真脚本加载后替换原effects，临时启动输入不作为产品副本覆盖。PG7保留显式独立_test/data_test、newowner与空空间准入、内外fixture失败保留、SDK/模型禁止及成功自有清理。

独立核root排序后的3profile递归非importAST及全部import绑定集合一致，actual target e331f1a2…/observers0ae57582…/parent_guard b68a9f98…需作为执行新pins；runner8f3a与caps initializer实际匹配，不使用旧SHA冒称当前。收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/portable-review-20261104-180d/review-receipt.json` SHA `44423938d15db689b38560714c5fb22bd3361845b8aaae7f4f2e5b5637521288`。342仅静态assert数量，新增运行/业务0。等待主Agent正式安装后才进行普通导入非PG工程，PG另等运维新库准入。

## 正式安装后的普通导入API独立工程（2026-11-04）

主Agent确认16映射正式安装后，本专项在新目录 `.local/team-workspace/agent-evaluation/qa-workflow-extra/ordinary-api-f05-20261104-693b` 以正式API pytest配置/asyncplugin执行8个正式测试文件，不含PG7：target_deadline、既有ParentGuard、bootstrap、activation、admission、effects_profile、workflow_task_contract、budget。没有-k/删除/降级断言，native0、132 passed、0错误/失败/跳过，8.51秒。此为普通导入后的本次新结果，未回填此前私有包；不将assert342当测试数。

直接使用已授权host工程环境，未先触发受限失败；独立HOME和临时数据目录，原网络/进程审计保留，120条允许事件仅标准库socketpair的bind/connect，其余连接/进程拒绝。34个已加载产品模块__file__均定位正式services源码，不含.local产品副本或sys.modules替换。测试与actual产品before/after摘要一致，原始stdout/stderr/JUnit/import-paths/network/result保留。

独立收据 `independent-receipt.json` SHA `918d050036e20c03a11785df945917c0a26f4a4c0400eb0535f427b5dc7825ef`。PG未执行、真实SDK/模型/target/manager未运行、正式业务新增0；物理链接仍未验收，READY/部署均false。真实PG7及F05业务生命周期仍待后续独立资源准入，Chrome拒绝未绕过。

## 九测试文件格式修复复审及文档事实核对（2026-11-04）

history封存f090c070…的9文件修复经本专项独立静态复审PASS：218条API assert及所有字符串完全相同，全部342原assert仍保留；递归非import AST唯一差异是未使用的For元组目标index→_index，循环body无该值读取，参数化/fixture行为不变。其余client断言与product21源码摘要不变，无新增ignore/noqa或门禁放宽。

按主Agent“静态PASS即可原132组复测”准入，新目录 `.local/team-workspace/agent-evaluation/qa-workflow-extra/lint-review-retest-20261104-0d19` 主机普通导入复测132通过，0失败/错误/跳过，7.97秒、native0；新HOME/原审计守卫、原断言、actual源和测试before/after保持，34模块均正式services来源。原132结果保留、不重复累计业务。收据 `independent-receipt.json` SHA `e6910e0c0f233853f5f252900954b7f5c3e1ca898ad3cb564c94cb7a061231ca`。

并行只读核对integration QA文档当前9份链接收据SHA，另核原JUnit575/221/122/PG7均0fail/error/skip；PG收据十五表0/会话0/已删除新成功库、保留旧失败的边界与文档相符，build仅PASS_BUILD_ONLY/未重启，文档明确未部署和未UI验收，没有把工程数量相加成业务完成或放宽实际授权预算。生产健康/PG现场按收据核读，本角色未重连生产/重跑PG。建议文案把认证协议“双端点”精确为runner/profile两处文件，并给600创建预约上限标单位；不是新增产品缺陷。物理链接、F05真实三root、F07、ChromeUI和历史MD故障等未完成项继续保留，业务新增0。
