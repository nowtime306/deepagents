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


## R02修复复审（仅8探针，未执行新业务）

原R02/P1及复审发现的3个P2已在客户端代码审查范围关闭。新差异freeze `789d5b04f350e4c5f27dc30a10e0cd6685ea63126bd73d1941ce3640221c5f85` 叠加base84f130的10文件摘要一致；已实现最小脱敏事实快照/hash引用、两轮输入/最终JSON/上下文/终态/用量重算，以及缺证据阻断cleanup/offline。另修单次有界读取同bytes校验解析、缺本root context为Blocked而非已知FAIL、当前工具证据不足时拒绝旧评分PASS。

根授权的8个最小独立复审探针全部通过，执行前后10文件hash不变，无HTTP/DB/业务/付费/UI；未运行作者103工程组，不与旧66或实际任务计数相加。证据 `services/evaluation/.local/r02-independent-review/run_71f3fd8b56f6/`。首次旧A02仍保留code gate PASS与独立事实验收NEEDS_REVIEW_R02的历史结论；代码修复不补回已删除数据库中的证据。R03及消费路径独审、完整工程QA与新隔离实际suite仍是后续独立门禁。


## 私有W01观察模块工程批次（未接线，非真实Work）

W01-S01/P2未知操作状态可被当已结束的问题已复审关闭：严格035五种原生状态，仅SUCCEEDED/FAILED视为settled。私有模块冻结SHA075a44f4...及测试14b0b7ea...后独立 **20passed/0failed/0skipped，0.13秒**；另7个原状态边界探针通过，不与20混加。5个直接源码/测试摘要前后相同，私有无外连环境的实际网络事件0。

证据 `services/evaluation/.local/w01-independent-qa/run_0342944bff0a/`；细节 `services/evaluation/.local/w01-independent-review/review.md`。仅MockTransport正式GET形状与纯事实重算，无真实目标HTTP、Coding沙箱、PG或UI。该模块尚未接线/READY，移入src后的接线与证据/清理消费仍需独审，本批不增加已验收A20任务数，也不与A02工程/原实际suite计数混加。


## R02/R03合并冻结完整客户端工程QA

R02事实证据复审及R03-01当前audit-index/ready闭合复审后，根明确授权完整客户端工程执行。活动冻结采用base `84f130989c1054f463c930d38ec8c0bee34bfe9eff2101988faf6d760aadecd7`、R02fix `789d5b04f350e4c5f27dc30a10e0cd6685ea63126bd73d1941ce3640221c5f85`、R03fix `d15d0f028880152a1e17115651eebaf07f0dec54d18e2ea5e605d308c007baf6`；未改的核心/首fixture依赖亦逐文件纳入本次manifest，不误称旧提交已包含后续未提交修复。

独立 **124passed/0failed/0skipped，4.77秒**，11个冻结测试文件：core18、modes3、review_regressions12、review_r4 4、UTF8 2、strict15、fixed profiles4、offline report identity8、evidence retention34、network sessions3、ready closure21。35个选定源码/测试/契约与依赖摘要执行前后相同。124替代本范围旧103/66工程快照，不与它们相加；W01私有模块未接线且未收进本124。R02原问题8审查探针、R03其他角色复审探针、W01私有20+7亦分别保留，均不增加真实业务任务数。

环境无供应商凭据、私有home/temp/DeepEval目录。父pytest进程socket/DNS一律拒绝，实际事件0；唯一实际连接工程场景在fresh `-I`子进程自建临时loopback HTTPServer，由源内守卫限定该端口。其留存audit摘要hash匹配：execution记录allowed DNS+connect以及对固定外部测试地址的denied DNS；import/metric/export均COMPLETE且event_count0。外部地址在解析前被拒绝，没有访问外部服务；子进程只运行本地真实DeepEval确定性工具指标。该审计是Python层测试观察，不是OS防火墙、真实平台worker、供应商调用或完整轨迹验收。

覆盖包括消息/输出/context/终态/nativeusage从已留存事实重算、缺失/篡改/跨scope/保存失败不能新导出PASS、完整事实先于cleanup、三报告hash/ready/current-index闭合、最后session/全身份/旧引用/错误或pending后继/reader锁等反例，以及此前预算、未知提交、事实优先、严格类型和Windows中文CLI回归。仍仅MockTransport正式API形状、真实本地CLI文件I/O/DeepEval与上述临时loopback测试；无真实target业务、PG、Coding沙箱、页面或付费。

证据 `services/evaluation/.local/a02-complete-client-independent-qa/run_e1604e09040e/{manifest.json,summary.json,pytest.log,results.xml}`。summary包含逐文件实际数量及临时网络场景hash/phase摘要；初次摘要未匹配pytest缩短目录名，随后只读定位原audit补入summary，未重跑测试。root另行开发/审查suite消费checked-ready及相同bytes导出绑定，不在本客户端35文件范围。真实新A02仍须完整ops消费复审及新开始门禁；首次旧A02的NEEDS_REVIEW_R02历史结论不变。


## 新候选533417e：此次A02受控工程事实验收通过

候选 `533417e396e93282324d1381d904e52b4ff14eee` 的全新隔离suite仅AET-A02、1case/1repeat，经正式平台HTTP与持久Ask队列执行。**此次A02受控工程事实验收PASS**：非作者ops实际执行，独立QA随后只读核验同一执行的封存字节，没有重POST、再执行、调用report重新导出或追加审计。它证明一个A20任务，不是A20全部完成；不将两root计作两个任务，不与旧失败证据或124工程测试相加。

31项只读核验全通过：`validate_ready`验证最新ready/index/三文件摘要；两次不同run `76677c29-0968-4277-b99a-7a7133d925f7`→`b9959326-3013-41da-a593-4c616e4652bc` 的保留正式终态均SUCCEEDED。直接根据固定case输入重新计算两个user text SHA，按run逐一匹配；最后declared_output为`{"project":"星桥","people":3}`，people保持整数，规范JSON摘要与封存输出一致。final-root服务context的两轮内容摘要、human_turns=2、项目与更新标记可独立重算，3项required事实与原记录均PASS。原4条消息完整且无工具；保存的实际本地DeepEval空工具约束score1/PASS及judge.generate=0记录与该证据一致，不代替真实模型语义质量。

native六计数完整且与原报告一致：calls2/reported2/unknown0/pending0/input20/output10。另独立核归档provider账本，两root各1call/15tokens、pending0/unknownfalse。native usage是conversation aggregate，provider账本是受控root计数，两者用途分开，不声称保留了每root原生usage行。

网络索引完整覆盖本次preflight/init/run/cleanup四个CLI，均SEALED/COMPLETE、引用顺序与journal/report/ready一致，最后ready绑定cleanup当前session。各CLI恰2个execution allowed事件（DNS/connect各1）；run包含execution/import/metric/export四phase，import/metric/export为0event，其他CLI的execution/export亦完整。全部引用hash匹配，无denied事件。此为Python evaluator的受控origin策略及审计，不含平台worker/native进程或OS级出站证明，也不是全SSE/完整Agent轨迹。

父JSON/JUnit/中文MD与子JSON/JUnit/MD均1样本PASS，三报告摘要与report-ready相符，suite/candidate/catalog/case/owner/environment绑定一致，301个source_sha256与冻结文件MATCHED。核验前后17项原artifact摘要未变；没有修改事实、原报告或审计索引来获得通过。

回收证据齐全后才给出本结论：ops completion receipt只读PG记录精确owned库 `owner_agent_evaluation_810fa813c667439093efb51314c44943_test` 不存在、pg_stat_activity0，CIM PID49504gone/进程数0；stopped同environment且graceful=true；正式CLEAN、stop、target reader/drop退出码均0。无ManagerKeyFile配置，本次Ask无沙箱创建；Docker只读exact opensandbox.io/id标签容器计数0，不是一次真实manager/sandbox释放验收。成本保持external_models NOT_EXECUTED、infrastructure NOT_PRICED、total/cost null/UNKNOWN；不把未知费用记0。

原始证据目录 `.local/agent-evaluations/suite-a02-evidence-533417e-6e8e4b3c/`。fact快照SHA `ec8c4a0a685be09a8ccff55a11479190f83331e51bf3c59025c58cfa27322f7b`；completion receipt SHA `e6a1c00882de16214513893b68e089ea58abeb956a2b0f439770323d00cf9181`；ops verification receipt SHA `dc59be057ea1f28d449f2f4233409976febdc877966d57fa298800ca9ed58273`。本角色独立核验程序/日志/结果在 `services/evaluation/.local/a02-real-evidence-independent-review/review_7673976faeb4/`，verification.json SHA `581751078dac857c2861e3e7dbd7cd4449a1aa139d7fbff6b3104b7de78db692`。回收元数据是对ops收据交叉核验，本角色没有另查DB/manager。

首次3f1f680旧A02依然保留其原code gate PASS与独立事实验收NEEDS_REVIEW_R02的历史结论，本次新证据不回填旧运行。真实外部模型/裁判、付费、语言能力效果、完整SSE、Work/沙箱、实际UI、生产部署、其余19任务及B/C/D均不在此次通过范围。


## W01观察及留证层冻结工程QA（非实际W01）

2026-11-04。W01留证层独审发现E01/P1“artifact BLOCKED仍允许cleanup”、E02/P2“binding true与1相等”；新freeze f8258b4b32b872ce04468e83bf33250a23b2f63b8a23298c8c789b49bb1ffab1叠加base7e2后两项均复审CLOSED。现在仅已证artifact_exists FAIL且其余事实全PASS可清理，outer binding以规范JSON digest保留严格JSON类型差异。

根授权独立完整工程 **39passed/0failed/0skipped，0.46秒**（observation20、留证19）；另6个原边界探针通过：完整缺文件、清单未完整、文件意外存在、boolean repeat、float repeat及integer repeat对照。39包含前序20，6为复审探针，不累加为45/59或业务任务。4个冻结文件及6个直接依赖hash前后不变，私有无供应商凭据环境，socket/DNS拒绝且事件0。

证据 `services/evaluation/.local/w01-evidence-independent-qa/run_550b3116c92c/`；私有审查 `services/evaluation/.local/w01-independent-review/retained-evidence-review.md`。只验MockTransport正式GET形状、保存/hash/身份/全部旧refs与纯事实重算/cleanup门禁；公共runner/reader接线尚未独审/READY，无真实Work、PG、manager或UI。本批不改变此前仅A02实际受控工程事实通过的覆盖，真实W01还须另获开始信号。


## 公共/W01接线与Workflow修复后的完整客户端204工程QA

2026-11-04。base freeze `7d427a2221221b5a6dff101ad681f6c965128925082ae4d9b8c8ed242590968b` 与fix `4da73561fc4a182afb2731c620c7a9b726e3e5b2e18115cde2772a1bb7392908` 的公共/W01独审问题已关闭：离线stored CLEAN调用同cleanup_ready；初始化namespace持久后绑定Platform，提交前最内层attest也比较，变化0POST。A02旧默认None兼容、严格binding、immutable snapshot和四phase要求保留，空metrics只需execution/export。

根授权先跑9个最小独立复审探针（3处namespace漂移0POST；4类旧CLEAN不成立拒绝；合法负控制CLEAN及待核验标记对照），全部通过。之后独立完整客户端 **204passed/0failed/0skipped，10.52秒**，17文件：原core54、fixed profiles4、report identity8、retention34、network3、ready21、WF17、binding3、W01集成1、root观察20/留证19、cleanup修复20。204含root39，不再加39；旧184/124/66及9探针都是重叠历史或复审，不累加为任务覆盖。

46个选定源码/测试/契约及依赖hash前后不变；root四Work helper保持原冻结字节。根已另复审WF cleanup的未决计数/provider/sandbox预约及offline同校helper修复，静态关闭；本204包括其新增回归，无需重复跑20。profile/ASGI29并非本次独立执行，实际SQL与目标业务仍待后续独立门禁。

私有HOME/temp/DeepEval与无供应商凭据环境，父进程socket/DNS拒绝且实际事件0。fresh子进程临时loopback工程场景的audit hash匹配，execution记录本机DNS/connect及拒绝外部测试地址解析，import/metric/export各0event。仅本地真实DeepEval、CLI文件I/O与MockTransport/纯重算，未调用真实业务API、PG、Work、manager、UI或付费模型，不是OS级出站验收。

证据 `services/evaluation/.local/shared-workflow-independent-qa/run_6e09861bc5e5/` 的manifest.json、summary.json、pytest.log、results.xml及probe-results.json；公共审查 `services/evaluation/.local/w01-independent-review/public-wiring-review.md`。W01/F02/F04实际任务仍未执行/未授权；本次不增加已验收A20业务数量，候选533417e那次A02限定事实验收与旧first历史REVIEW均保持。

## 2026-11-04：90dbe95三例真实任务独立复算

本节为新结果，不覆盖上述历史阶段。固定候选 `90dbe95f1d52959d0a077d3a0e03241f814f687b`、新批次 `.local/agent-evaluations/suite-a01-w03-w04-90dbe95-613af8e2/`：A01/W03/W04 各一个不同案例、一次重复，实际任务均 PASS。A01 两根4次调用40入/20出；W03/W04 各2次调用20入/10出。无 pending/unknown。A01 草稿、显式启用、唯一真实调度及已用配置；W03 拒绝无写；W04 同 checkpoint 重建后单次执行与原确认重复，均从封存原事实重新计算。三个根模式分别为真实 AskWorker、真实 WorkWorker/Coding；未调用真实外部模型或裁判。

独立验证目录 `services/evaluation/.local/a01-approval-real-independent-review/actual_850895fc8066/`：`review.md` SHA256 `e81874463b29ac2c941aea1ef9322b3753b66f72fe0f68e7239f3e0875965db1`；`verification.json` `9e29d3ae53ffa6d6cc567a4875cb04ab6ee6ff0483ba048698c3eecf96071ea3`；`metadata-verification.json` `2502c484db60aec18926811f40fe5f9daa79a29b4a6bf691b88b453514ccf72c`；独立完成收据 `4c84a9929dd43ad535226c3e64be6ebcfb199163ab20e83a25c9689f695b50cd`。

独立只读核验三例原始快照、12个逐CLI审计、当前精确 audit index/ready、三种报告与父汇总；315项来源冻结和同候选目标清单一致。另核对同一次运行的精确数据库/0会话、同环境 graceful/PID退出、两个 Work 各两个 session/sandbox/镜像4 CPU/5 GiB/namespace、管理器连续库存和最终 sandbox/sidecar0。W04 还留存12次真实 RUNNING 租约采样，只保留令牌摘要。

W03 的 Docker 历史事件缺失保留为 `MISSING_NOT_CLAIMED`；连续管理器0→1→0→1→0、两个实际 inspect、原生 RELEASED:2 和最终清单0满足本次限定归属与回收要求，不宣称完整 create/start/destroy 历史。W04 两容器历史完整。执行者曾有过强的私有历史事件断言，审查者曾修正私有脚本的路径前提；两者都未改业务原件、弱化既有需求或重跑任务，原失败输出保留。

本次仅新增三个限定案例验收，累计7/20；旧4627隔离库是停止且保留的诊断现场，非 CLEAN，旧失败报告不改。没有实际页面、OS/worker出网、完整原生轨迹或生产部署结论。原始执行完成收据 SHA256 `580acfc43662d78259cab43ab3647fc1b9a58fabb1538d5bed458bd5e237b49a`；源码闭合收据 `e69ebd997018e1b18a644c3f118d6fb8ef2030f6d7ee972f96af1395f8700049`。
