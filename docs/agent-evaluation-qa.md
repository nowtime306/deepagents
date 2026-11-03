# Agent 评测初始核心与A阶段平台接入：独立QA

2026-11-03。本记录不代表A20业务闭环、B/C/D、真实UI或部署通过。原产品方案和学习文档未修改。实现作者与本批审查/执行者分离；采用的作者测试源码由本角色在独立环境实际执行，并标明来源，不直接引用作者通过数。

## 核心独立审查与工程回归

原核心freeze86123...发现R1操作证据缺失误判零、R2扩大/不执行预算、R3评分异常丢失事实失败；修复后48f9...独立12探针关闭原三点，同时发现R4消息证据缺失误判零。最终70702...freeze逐文件匹配，独立5探针关闭R4。详情见[审查报告](agent-evaluation-core-review.md)，定向探针不算业务任务数量。

经根任务批准，针对最终freeze中的4个测试文件独立执行 **37 passed、0 failed、0 skipped，0.34秒**；前后所有冻结文件摘要一致。证据 `services/evaluation/.local/core-independent-qa/run_3cb299d91310/` 的manifest.json、summary.json、pytest.log。使用evaluation独立venv，受控HTTP MockTransport与真实安装的DeepEval指标；不继承供应商密钥，阻止Python socket连接/解析，私有缓存/状态目录。未执行实际平台/数据库/Coding任务；37工程tests不是20任务成功或真实语义质量证明，profile_support新增范围不在该freeze内。

## 可选预算证明与Workflow根用量接口

根任务复审测试后授权执行两文件完整相关接口批次，结果 **32 passed、1 skipped、0 failed，20.63秒**：新增workflow_usage14项全部通过，原environment19项18通过/1跳过。跳过是此Windows进程无目录symlink权限，祖先与marker symlink组合未执行；Windows leaf/ancestor junction两例实际执行通过，不将skip算pass。

新增14项覆盖：无execution_limits时保持旧响应；typed schema/布尔数值/负数/未知mode/scope/额外秘密字段拒绝；同root两次已知调用累加，其他mode及owner的同UUID记录不混入；运行期PENDING与终态UNKNOWN区分且不改原台账；无调用零tokens和未知调用None分开；evaluation.read/workflows.read任一撤销拒绝、他人root404、隔离marker变化拒绝；Ask/Work/ExternalWork嵌入子流程拒绝伪装独立用量。

这些用例用真实隔离PostgreSQL和HTTP，台账/关联为受控已保存fixture，不是实际嵌套流程或controlled-model工厂执行。环境证明是受管实例注册/隔离标记与声明，不是任意宿主绝对安全的证明。

测试期间模块SHA256 `5f5ff03b8ac8df229eefb65e100f9d0524596b8001be00334fa5b1136ecf6156`；app/migrations及所选测试执行前后摘要一致。证据 `services/api/.local/team-workspace/agent-evaluation/attestation-qa/run_be548601b066/` 的manifest.json、summary.json、ae_workflow_usage/pytest.log。独立随机库 `owner_agent_rdc_indqa_ae_workflow_usage_f64ef7b517_test` 已校验名称后回收，私有vault/data，无生产业务库访问。未修改产品、断言或权限以获得通过。首次每文件skip解析未归属汇总行，已仅校正新旧文件归属；总32/1始终准确。

## 前序独立批次分别保留

- 原环境证明：21通过/1跳过＝新environment18通过/1跳过＋账户权限3通过，17.02秒；证据run_0fbb0451cc51，两个marker/路径P2按范围已独立复核关闭。
- 专用连接：9通过＝新connection6＋账户权限3，8.02秒；证据run_62cd0136c66d。只用ObservedWorkbench捕获构造连接/serve/close，不叫真实沙箱通过；默认globalkey由虚构返回值替代，未读取真实globalkey。早拒绝/专用连接/禁fallback及旧认证HTTP兼容有实际断言。

这些与本轮32/1存在重复，不相加制造总任务数；各自manifest按执行时实际工作区内容冻结，不误称旧178/21bb提交包含后续attestation代码。

## 剩余门禁

A仍须完成20个不同业务任务的固定profile init/observe/cleanup、正式API真实队列、受保护事实验证与完整报告，不把工程脚手架或目录中20条声明计作20次执行。真实语义效果/付费调用保持未测；实际UI saveddeny未解除，未绕过访问或抓HTML；B/C/D、完整生产备份恢复与部署分别验收。当前QA未确认上述未实施或未执行范围已完成。


## 后续 UTF-8 冻结批次（不与旧批次相加）

新freeze58551292175caeaec8bb542eebeca0d059715a6be72f38420bec54e2ad4c256f已独立核对全部内容。仅CLI target/dataset和journal读入显式UTF-8；原中文/emoji/文件名未ASCII化，原R1～R4仍关闭。实际Windows-I/cp936、utf8_mode=0正负例先独立2passed（0.66秒），然后新冻结工程集39passed、0failed、0skipped（0.99秒）；2已包含在39内，不合计41，也不累加旧37或API32/1。

证据 services/evaluation/.local/core-utf8-independent-qa/run_ba2bc0da5205/{manifest.json,summary.json,utf8_targeted/pytest.log,engineering_39/pytest.log}。执行前后全部冻结摘要一致，只有受控Platform替身和真实本地CLI/journal/report编码I/O，无真实平台、A20、付费模型、数据库、沙箱或页面访问。API32/1原结果和范围保留，未重复执行。

preflight的ATTESTED仍不表示每项profile、权限和预算可用；固定fixture集成应新增只读readiness并明确BLOCKED未实现项，不能用ATTESTED绿色标签代替20任务闭环。详见核心审查最新附节。


## 后续 AE-05 strict 契约冻结批次

freeze7881a2fde54cb4d1aadabd6ff2f78f183b0e29d6b099423d11be16abb94fdab7的实际内容已独立核对。原version=True/计数False被拒；布尔数字输出区分、规范虚拟artifact路径、摘要格式、非finite阈值/JSON、未知metric及隐式judge参数均由独立28边界检查验证；前向已登记但尚未实现的检查在业务POST前BLOCKED，不冒充A20可执行。

复审关闭后，新冻结6文件工程测试独立 **54passed/0failed/0skipped，1.06秒**，源码与测试摘要前后相同。证据services/evaluation/.local/core-strict-independent-qa/run_f20d23585db8。28是审查探针，不与54合计；54不与旧39/37或API批次合计。仍仅真实本地DeepEval加HTTP替身，无实际A20业务、付费模型、UI或部署验收。详见核心审查最新附节。


## 后续 A02 fixed 客户端冻结批次（非真实A02执行）

叠加core strict 7881、A02 base e6fc6b104c6aa83d76a3cdcd9567e1b8dfac94877d50451f5166d42f625eb07a与修复manifest dd12cba4c666cdc798f3e4e0acc883fb4ef60131335441e4a875b826cd426222。客户端独审发现离线report忽略请求case/suite的P2 R01；修复后先核身份再导出。独立13离线探针覆盖匹配、错case、suite candidate/ID、target owner/environment/commit/digest、attempt owner/environment、snapshot/catalog/rules；错误均exit2且原报告字节不变，未构造HTTP客户端。R01按范围CLOSED。

根明确授权后的完整客户端工程 **66passed/0failed/0skipped，1.23秒**：原核心6文件54、新fixed profiles4、新report identity8。66包含旧54，不能相加为120；13审查探针也不与66合计。全程私有无供应商凭据环境，Python socket连接/DNS一律拒绝，观察事件0；37个合并冻结文件摘要在工程和探针前后均一致。

证据 `services/evaluation/.local/fixed-a02-independent-qa/run_525fed88fddc/`：manifest.json、summary.json、pytest.log、results.xml及independent-probes/probe-results.json。独审细节 `services/evaluation/.local/fixed-a02-independent-review/review.md`。仅本地真实DeepEval确定性metric、MockTransport及CLI/journal/report I/O；无实际HTTPtarget、数据库、沙箱、付费请求或UI。A02真正两轮持久队列执行及A20门禁仍未验收。cleanup CLEAN在本客户端合同中指无活动/未知效果并保留私有目标历史，不能称数据库/资源已删除；实际目标deprovision另留证。


## 首次实际 A02 suite：旧执行记录PASS，独立事实验收待核验

非作者ops在候选 `3f1f680f5a43653be81400f50b6b7402adad4060` 执行一次正式隔离suite：仅AET-A02、1case/1repeat，经真实平台HTTP与持久Ask队列，两轮正式run。代码gate PASS及原始报告保留；**本角色只读独立核验现有证据，没有再执行一次任务。** 它不计为A20完成，也不与前述66工程测试合计。

19项记录一致性检查通过：父JSON/JUnit与子JSON/JUnit/MD均为1样本PASS，3项required事实PASS、实际本地DeepEval ToolCorrectness score=1且judge.generate=0；candidate/suite/case/catalog摘要一致，case snapshot与target摘要可重算；297个source_sha256与冻结文件匹配。原两次提交均CONFIRMED，prior run `eee38303-e769-4e61-a5df-6a3a2a712faf` 与最后run `053ed3e5-3291-4cc0-a6a6-46d9b68f002b` 引用都保存在子journal。持久受控provider账本各root恰1call/15tokens，累计native usage记录2calls、20 input/10 output、unknown/pending均0，最后平台状态SUCCEEDED。父root_run_ids仅列最后run，完整有序两run见child prior_run_refs及completion receipt，不能据父数组长度误称只有一轮。

**R02 / P1 OPEN：业务事实原观察未留存，不能独立复算。** `fixed_run.execute` 只保fact PASS/最终平台status/累计usage，evidence_refs为空；原GET messages、两root run、context observations及每root native usage最小脱敏响应未归档。数据库已按合同删除，无法从当前留存字节重算两条消息、最后JSON、received_context三个事实或prior原终态。已核结果/账本一致性不等于独立事实验收。原gate PASS不改写，但验收状态是 `NEEDS_REVIEW_R02_RAW_FACT_EVIDENCE_MISSING`。根已登记修复：写入最小脱敏快照及hash引用，保存失败/缺失/篡改不PASS且不清库，离线验证身份；独审关闭后另用新库执行，不为补证复活旧库或重放旧run。

网络证据也有明确局限：最后cleanup CLI覆盖journal.network_observations，只剩execution阶段2条allowed事件且无destination。它不能证明整次多进程、import/metric/export全部阶段无外连，也不是OS出站隔离。固定模型工厂/策略核验与完整出站流量证据分别报告；不称AE-02完整验收。真实外部模型/裁判与UI未执行，sandbox_creates=0；基础设施成本NOT_PRICED、total/cost仍null/UNKNOWN，没有将未知费用记0。父层只有JSON/JUnit，中文MD在子层，父汇总可读报告缺口另由ops处理。

旧执行的停止/精确回收收据已补齐并只读核验：正式cleanup/stop/target cleanup退出码均0；库 `owner_agent_evaluation_3c54514f3b694275acb498c80291db83_test` 的ops只读pg_database结果不存在、会话0；CIM核PID63816进程数0。上述是对ops收据核验，本角色未另查生产DB或重跑业务。`completion-receipt.json` SHA256 `96d180a74db7d85c37d818bfe29681aa1f2325fcc530d69e825fbd722db422d6` 及其8项artifact hash核对匹配；本角色此前记录的10项原artifact摘要未变。精确路径 `.local/agent-evaluations/suite-a02-first-76d819ab/`，含suite.json、父results.xml、子result/{journal.json,report.json,report.md,results.xml}、completion-receipt.json、pid-receipt.json。核验私有收据 `services/evaluation/.local/first-a02-evidence-review/artifact-receipt.json`。本次仅记录已执行观察与缺项，未修改原数据/报告或产品实现。
