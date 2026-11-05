# 剩余案例独立审查与核证（2026-11-05）

此记录保留工程、故障控制和业务目标成功之间的区别，不自行更新A20总数，不开放正式执行。

## W05 新批故障传播控制

独立审查prepare脚本摘要9781525b…及inputs57713…与根批准一致，实际HEAD为5e4e5f64baa811a983319aca7e186f7812c892d0，完整source_fingerprint匹配；usage.py保持已审a74b4c9…修复。准备只写私有新批目录和既有suite命令，不启动任务。新批范围300秒/10调用/20k Token/最多2次沙箱创建，旧批不复用不退款。

根执行后，独立核 `.local/agent-evaluations/w05-acceptance-f842c0868cbb4cb3abfe4097eaa65006/AET-W05-r1-63aada3a/reviewer-independent-verification.json`：父NEEDS_REVIEW、usage_unknown=true、resource_budgeted=true、model_calls3、used_tokens30；唯一reviewer成员UNKNOWN且模型/原角色对应。原生2KNOWN调用输入20输出10，第三UNKNOWN输入输出均null；lead仅1调用、reviewer2，无后继主助手调用。provider唯一根3调用/45预留，unknown=true、pending0；真实member-read独立审计对应team-evidence/13字节及SHA。CLI事实child_failure_propagates=PASS可接受故障控制，原任务、gate及成本仍NEEDS_REVIEW/UNKNOWN。

自有sandbox session为RELEASED，实时认证manager按原namespace分页库存完整且0；Worker与活跃租约0、目标记录PID及日志server PID不存在、stopped graceful、351源码守卫匹配。数据保留，不改原gate或宣称业务CLEAN。首次只读核证脚本误用了不存在的started_at排序列，改为call_id排序后通过，属于QA脚本错误，未触发产品写入。

## W06 初始化实际消费与修复候选

新批 `.local/agent-evaluations/w06-acceptance-0b756d01e82a4155850c60a566c24df9/AET-W06-r1-05459984` 未提交Work根，但原PG/provider均确认索引已实际消费1次KNOWN embedding、输入7/输出0，resource_operation成功、provider无pending/unknown。文档与向量READY、revision/model_revision1、维度4；不将business_started=false解释为零消费，不重开旧target。

确切失败为评测适配器契约：正式knowledge_documents.operations返回usage_api.summaries的行，实际JSON含run_id、六计数及routed字段；index_baseline原样传入，而validate_index要求恰六计数。以原保留PG SELECT、正式summaries/jsonable_encoder以及原SQL/provider事实重构其他HTTP响应，旧入口确切抛Index-only actual provider/native evidence differs。

私有候选 `.local/team-workspace/remaining-cases-20261105/reviewer/w06-index-usage-v1/manifest.json` SHA256 6b37fa2af14c77b9ae52d96ba8da961d8cf6cf904e9a6fcce481668d2f21bbce：仅在边界先严格核usage.run_id==operation.id，再投影六计数，原validate_index/metadata断言不改。旧红→新绿，foreign/missing run_id、unknown和布尔计数负控制均拒绝。receipt与actual-api-reconstruction.json保留。新生成/根0，旧数据未改；待根非作者审查复测与后续准入，未合入正式源码，不能算W06通过。

## F05 现行合同核对

正式suite-capabilities及fixed_flow_code要求现行v2：draft/published/reject三个不同真实根，累计10次沙箱生命周期（4+4+2），并发最多1，三根0模型，所有会话RELEASED、0未知/活动。300秒是从初始化seal开始覆盖审批、代码、取证的共同期限，不逐根重置。默认max_created2与F05专属max_created10是不同层；旧“2沙箱”草案不满足现行整例，需明确采用v2并单独准入，不能沿W05预算放宽。

## W07 与 F07 基础候选

W07 receiver基础52f1f5b0…独审无阻断。真实write注解为非只读/破坏性，Bearer与四元scope前置，SQLite事务先提交固定one效果再丢HTTP回执；重复尝试可见但不产生第二效果。独立复制TCP测试4项通过、0失败/错误/跳过、2.240秒；收据 `.local/team-workspace/remaining-cases-20261105/reviewer/w07-base-9edc80ca4c9e4585b4df0ce25a81bbb9/independent-receipt.json`。0模型/产品PG/业务根/沙箱，仅基础，不证明平台恢复或完整W07。

F07-v2核心静审指出裸PureNodeTimeout类没有实际deadline过期证明也可能被compiler认作known并fallback，作者测试漏同类裸实例。已交开发在新候选中以真实过期证明绑定节点并补反例；旧v2保持，完整新候选尚待冻结后审，不在此批准执行。

## F05 v2 实际整例与回收独立复核

真实新批 f05-acceptance-5aa779838aad4077be7e98c048982287 / AET-F05-r1-d95a2b06 的sealed事实重新通过正式fixed_flow_code.verified。draft/published两根各三次approve、各唯一SUCCEEDED代码操作，实际下载counter均为1且SHA正确；reject根第一次拒绝后FAILED，无代码操作或counter。三根10个不同sandbox身份，4+4+2且均RELEASED，0模型/Token/未知效果，共用300秒期限已核。原suite PASS、source MATCHED、formal_cleanup/stop/target_cleanup均exit0。

框架已正常删除该独立新库，复核仅使用保留封存事实与当前pg_database/pg_stat_activity无库无连接、CIM原PID不存在、认证manager原namespace完整0，不声称删库后复核原SQL行。case内reviewer-independent-verification.json保留所有独立结论，未重建库或重放业务。

## W06 修复集成后的真实整例与限定结论

根非作者独审私有修复、真实旧PG红绿及反例通过，正式集成最小单文件并在575项工程回归后提交cbdab61c4b934dd440476c84d9e1c4be944f7f68。随后新批 w06-postfix-bcf7e0524e144bf6ab9509319bb939ab / AET-W06-r1-4d6885a2 完整执行，独立只读复核原PG、正式sealed verified、provider持久账与receiver审计。

唯一Work根a0eaeef0-3c91-480c-ac14-bd676c76cb07为NEEDS_REVIEW/WORK_BUDGET_EXCEEDED，token_limit=used_tokens=16000，model_calls6、usage_unknown=false。唯一reviewer成员SUCCEEDED，原角色、模型与发布版本对应。真实SQL总7次KNOWN：初始化资源embedding1次输入7；Work内lead聊天2次、reviewer聊天3次及query embedding1次，共输入15047输出953=16000。运行内embedding与初始化不同call及snapshot，属于同原Work成员；整批输入15054输出953=16007，provider恰两根1/7及6/16000，无pending/未知或后继调用。

真实读取bounded/7字节及SHA正确，hybrid检索命中原文档片段。达限后的budget-write审计为DENIED/WORK_BUDGET_EXCEEDED且调用前已6次/16000；SQL所有成果无/budget-after.txt。独立sandbox session RELEASED，当前认证manager原namespace完整0，Worker/活跃租约0，原PID及日志实际server PID退出，stopped graceful，351源码守卫MATCHED。

事实budget_no_later_effect=PASS，可限定接受共享预算达限后无后继副作用控制。原gate/cleanup仍NEEDS_REVIEW、成本UNKNOWN且数据保留，不宣称整例绿色或业务成功。旧初始化失败批1次/7Token继续计入累计，不退款、不重开。case内reviewer-independent-verification.json记录fresh SQL和退出证据。独立核证脚本首次将PG UUID对象与JSON字符串比较失误，修为规范字符串比较后通过，无产品写入或额外模型消费。

## W07 完整R2候选独审与无生成回归

新完整候选work/w07-handler-v2-r2/manifest.json SHA256 b0c4fb6f9e2d7260ce70d32ed7ed26237a925a0f80d504fe940f367dec3f7386，21文件与7正式依赖hash逐项匹配。原v2真实MCP工厂headers签名、FixedFixtures safe投影丢tool_name、非原子/非持久小预算门、真实Work HumanMessage不符四处问题均保留旧候选并在新版本纠正。

R2精确检查正式FixedFixtures的发布instructions与WorkEngine真实instructions+换行任务目标+goal，不接受裸goal或工具结果后二次提案。Service仅替换自有实例的MCP HTTP工厂与ProfileState.reserve；调用原生工厂，并用独立锁覆盖持久统计检查及原reserve提交，将真实唯一owner/root/lead限制为1调用15synthetic tokens。owner受限GET receiver在通用POST safe投影之外核真实id/model/tool。Recovery核真实NEEDS_REVIEW、UNKNOWN操作、原批准、已知1/15、独立1attempt1write和sandbox释放后合作重建消费者，仅观察同一检查点/事实；不重放图或清除UNKNOWN。

独立复制25项工程通过（6.27秒），收据reviewer/w07-r2-97e83e78a5f0423c9310810bb95ad3ca/independent-receipt.json，0生成/产品PG/业务根/沙箱。未发现新增静态阻断；真实PG初始化、真实Work审批与未知写入/消费者重建/退出尚待根独立批准入，不能记为完整W07通过或READY。原坏候选不启动。

## W07 无生成真实PG预检发现关闭SQL缺陷

根批准的独立新preflight库执行实际Registry→Service.initialize→真实MCP发现后，关闭SQL因mcp_servers不存在revision列失败。该缺陷在完整R2及预检中均存在；独审先前漏对正式85迁移列核验，不能将该静审结论当成关闭schema已通过。真实schema只有由082触发器维护的configuration_revision，修复应使用既有空串/停用更新，不能为通过测试新增revision列。已交作者新R3及preflight-v2；R2不进入业务。

失败原件ops/w07-r2-v1/preflight-4eeae0c1fb2245148d0793440ea5bf5b保留。独立fresh只读SQL与端口核证见该目录reviewer-failed-preflight-observation.json：模型1、真实MCP write工具1、READY但未绑根、provider roots空、无effects.sqlite，Work/usage/sandbox均0、其他库连接0、listener端口无监听。server仍enabled且原加密key保留，cleanup_complete=false，不冒称清理完成，不自行重发关闭。

## F07 R1 与 W08 后继截止阻断

F07完整R18812784b…关闭旧三项阻断：实际JSON node.failed与node.handled输出区分、started布尔与独立计时字段、完整初始零作用及限额准入。新审发现Controller/model/root入口与seal/close的DB等待后仍需在实际效果前复检截止/UNKNOWN及并发绑定，根在新私有R2修复并补延期反例，原冻结不执行。产品overlay仅四个兼容修改及新增纯转换timeout helper，真实过期证明绑定节点/参数/task/scope，裸APIError或同类无证明均不可成为known。

W08完整d2bf…的setup guard与sandbox DB等待后缺截止复检，且handler/service有两个300秒锚点，作者新R181dd777e…已统一持久锚点并补等待后检查、已知未发SDK的本地预留回收。进一步沿真实DurableSandboxes.create发现reserve之后还有namespace DB与父guard等待，read/write也有最后原guard等待，已交作者将自有实例最终effectguard与原锚点绑定并补真实链反例；旧候选不启动，工程数不能当真实物理链接/业务验收。

## W07 R3 schema修复与 F07/W08 后继独审结果

W07 R3 manifest ce506f8ef7a361c7504f6b84c91ba18b837d53e3ec91bf18b192bc577dec79f7 的真实服务改动仅删除不存在的revision赋值，保留空串清密钥与原UNKNOWN关闭前置；082迁移触发器负责实际configuration_revision。独立27项工程通过6.38秒，收据reviewer/w07-r3-fb9182828a00402c80bfe8f9f905f9a5。preflight-v2 749a2ac7…在实际information_schema先检查真实列并使用修复SQL，原PG失败库不复用。新的runtime运维5cd69848…及338输入ec0a09d5…逐项核对，无新静态阻断；独立新库/账号/命名空间/随机端口，真实manager key摘要，双原生进程身份与合作退出，ready起300秒自停，实际1/15 provider限制、不扩大2Coding预算，无强杀或旧任务重启。

F07根作者修后R2 manifest bd5d5e886f2b5100b026af4d3e85401cdfa412df89402d1353b07fa7a21a60ad 独立非作者审查通过：全部manifest文件及183个未改API依赖同时匹配正式源码；R1到R2仅Controller模型预留、锁内root预留、seal、close等待后复检截止/未知与并发已绑定状态，五个产品overlay内容不变。独立exact复制62项工程通过8.62秒，收据reviewer/f07-r2-9f015c2fc90e49b1835c93974f37f698；涉及真实timer/runtime/compiler/ASGI与明确DB等待替身，不是实际PG或业务根。可准备新独立环境、关闭所有后台/Work、120秒4根1调用15未知预留0沙箱；仍须实际业务和清理核证，不记F07完成。

W08 R2 manifest 6065ba12611042cd1a80d1425792e139f7f05237f7c5ed16a52b310ed6799236 独审通过。唯一持久300秒初始化锚点由handler原样传入服务，不能重置；prepare、容量等待及最终自有db.effectguard覆盖最后的原父guard等待，并核真实ResourceRun数据库对象/owner/root。已知未发SDK的CREATING且sandbox_id为空的本地预留只能精确释放、不退计数；原native closing清理路径保持。独立40项工程通过12.69秒，收据reviewer/w08-r2-1b56a6d47725419fa03db4e9d9a4f523，含实际DurableSandboxes.create/WorkBackend路径但SQL/SDK均明确替身，0实际PG/生成/根/沙箱。可准备1根6调用90synthetic、300秒1Coding、原生step6/token1000的新批，真实Linux链接/RPC/最新导出仍待执行；旧候选不启动。

## W07 R3 实际完整批独立核证

真实新批ops/w07-r3-v1/runtime-ad290146af4d45a899cd189a5d21949b执行及合作停止均exit0，28.813秒。独立fresh只读SQL、SQLite、持久provider、封存证据hash、最新checkpoint和退出库存核证通过，收据为该批reviewer-independent-verification.json。唯一root cf982567-6371-4e87-9b12-f65fa4bca80b仍NEEDS_REVIEW且lease为空，唯一真实MCP操作UNKNOWN/finished_at NULL；唯一approve已消费且对应原工具名/参数/digest，未重批。原响应在真实提交one后故意断开，h11长度错误是合同预期故障。

receiver独立SQLite唯一attempt(1,1)、唯一effect(1,one)，四元scope及digest与恢复前后完全一致。真实consumer实例UUID前后不同，而SQL最新checkpoint 1f1d8f15-5ee9-6bf8-8007-449fd50f68e6、原operation id/digest、receiver及provider事实未变；不是重启整台服务或继续图。唯一KNOWN调用输入10输出5，provider1/15，无后继调用/退款。两个实际不同sandbox均RELEASED且无retention，receiver正式关闭后MCP disabled、密文空串，原UNKNOWN不清。

fresh CIM原wrapper/server PID42568/45364不存在，API与receiver端口无监听，合作exited nonce一致；worker0、其他DB连接0、frozen338输入逐hash匹配，认证manager原namespace分页完整0。可限定接受未知写入不重放及同一终态事实的消费者观察恢复；数据保留，不称业务任务成功或未知副作用已解除。旧预检失败/消费仍留存，本批1调用15synthetic Token持续计账。

## F07 R2 后续真实用量准入阻断（撤回业务准入建议）

沿正式WorkflowRuntime.recorder→output_reserve→UsageRecorder继续核调用链后，发现完整fixture把模型max_output_tokens设4096，而图token_limit仅1000。真实UsageRecorder.raise_error=true，on_chat_model_start在provider前判断spent+estimated_input+4096>1000，必先拒绝，预期F07Model真实计时器及1次UNKNOWN无法发生。该问题未被62个无真实PG/UsageRecorder工程覆盖；前述R2静态“无新增阻断/可准备”仅保留准备与0effect预检，撤回业务执行建议并已立即通知根。

必须新冻结后继缩小固定模型输出预留，使真实原生准入能够达到受控计时器，保持原图预算和1调用15synthetic未知预留合同，不伪造usage或放宽原生预算；补真实recorder入账到实际timer路径。旧R2和任何既有批事实保持，未得到新后继审查前不以工程数替代完整F07。

## F07 R3 后继与两次中止事实

F07 R3 90f90af6…唯一行为源改动为固定模型输出预留4096→64，图1000与受控provider1/15未知合同不变。新增实际WorkflowRuntime/UsageRecorder预算SQL与计时器桥接，明确权限/SQL替身：旧4096在provider前拒绝，新64真实callback PENDING先于provider而timer后native UNKNOWN/null、provider1/15 UNKNOWN，二次调用原预算拒绝。独立64工程通过9.39秒，收据reviewer/f07-r3-4a8da05bb9af4432afc9d4e38dd169e9；工程中一次本机controlled timer不等于业务批完成或真实PG验证。

旧R2环境run-83783918bc81413bb2fc8e9a427d9c92在QA批准摘要断言处、login/qa-started之前中止。独立fresh核controller文件无、10表0、其他DB连接0、双PID33456/37168不存在、49367无监听、exited nonce与336冻结输入匹配。收据reviewer-independent-zero-effects.json；这是启动校验失败，不是业务执行失败，不复用旧目标准入。

新R3实际run-09b24f6bab36474a80b984c3baab61ce初始化又因model_configs.id实际为text、seal传UUID参数而SQL text=uuid失败。未出现workflow或模型生成，但已创建1个model(max_output64)，controller已ACTIVE，activate/model意图CONFIRMED、seal UNKNOWN；保留1个孤立测试auth_session。独立fresh其余workflow/usage/Work/sandbox/operation/worker均0、其他DB连接0、双PID/端口退出、exited nonce与336输入匹配，收据reviewer-independent-init-failure.json。不是所有元数据副作用0，旧库/UNKNOWN/会话不改，根新R4只修typed参数并实际旧PG只读验证。

## W08 R2 实际消费与观测失败

真实runtime-b969872c806d464cb7df9149a44c4b4f唯一root abccfd26-98cf-4070-927b-23743c09668d已COMPLETED，原PG6次KNOWN输入60输出30=90，provider6/90无pending/unknown；Linux物理symlinksetup CONFIRMED，唯一实际Coding session RELEASED且无retention，原批准和操作执行记录0。最新artifact SQL恰input/result两个，原磁盘字节均SAFE_INPUT/10及digest正确；本次明确未走最终正式HTTP下载。

GET observations因最新checkpoint channel_values消息加载缺失抛500，未封存完整观测，不接受W08，不能据qa-result actual_business_cases0把已发生6调用90Token/1Coding记成0。独立只读核证与退出收据reviewer-failed-observer-verification.json：CIM26988/14100退出、API无监听、worker/其他库连接0、认证manager原namespace完整0、frozen340及真实editable filesystem摘要匹配。保留原500与数据/消费，开发必须核真实saver namespace/blobs/版本，不能返回空消息或放宽验证来通过。

## F07 R4 typed id 修复独审

根作者R4 manifest 90a19a9bdc22a9089ea792ef0de0af41e9fda6be68e641e1d60867a0ca0fe652，仅seal-model SQL参数UUID→str，吻合正式model_configs.id text。独立新增ASGI typed-param反例1项通过，基线64项前轮已独立通过未机械重复；再以旧09b真实PG default_transaction_read_only=on 独立验证原UUID比较UndefinedFunction、新str查询成功，1model/0workflow/0usage/0sandbox前后不变、原seal UNKNOWN不改。收据reviewer/f07-r4-9e831920ba3149f0b4af86ca53f07240，允许新独立目标准备，不复用旧失败环境。

## W08 R3 DeltaChannel 原生状态重建修复独审

R3 manifest 6aff455cbaf5762a2d46098bba4f00acf52418d89fd01c43f595ccaea6f7a9b0 保留原业务控制，仅替换观察端读取方法。当前Deep Agents/LangGraph使用DeltaChannel，raw saver checkpoint不承诺含完整messages；新reader以真实run plain snapshot/instructions构造原build_work图，使用显式禁止生成/SDK/工具的InspectionModel及WorkBackend，PG新连接强制default_transaction_read_only=on。固定实际最新same thread/root/ns/checkpoint_id，调用原生graph.aget_state重建Delta状态，读后核最新id未变，拒绝空消息/pending/interrupt/异scope，不挑旧checkpoint或resume。

独立52项工程通过12.70秒，依赖及安装的native reader源码hash匹配；独立对旧b969保留真实PG复核raw缺messages红→新reader重建12条消息/5次实际probe绿，合法读取/三拒绝/合法写的ToolMessage error序列False/True/True/True/False，safe存在、canary未见。真实最新id相同，model/sdk/tool effects全0，checkpoint45/write56/root1/usage6/operation0/sandbox1前后相同，provider账文件SHA不变。收据reviewer/w08-r3-fe992a037d9c4c8bac6f91b6ccd5c85b/independent-pg-red-green.json。原500与6调用90/1Coding消费保留，不把这次只读红绿当成原批正式HTTP下载/整例通过；允许根新独立批准入复验。

## UX-MODEL-01 文案与测试Web运维独审

独审实际单行diff，仅将模型管理底部提示补齐已支持的重排检测，并保留其他用途按卡片/协议限制与保存不等于连通的说明，吻合model_catalog.probe_hint，无功能/数据改变。生产构建5K-bmM5RqkJSebxCkO4sS的测试3100部署包98800bfe…/assets dd196493…对已审脚本仅BUILD_ID一行变化；新旧各1137资产、18required、preload/构建receipt摘要独立逐项核匹配，旧bundle保留回退、APIservice_changed=false。允许根单次合作替换3100 Web后产品实际页复验，不涉及DB/API/3131；未把审查当作已上线或页面验收完成。

## W08 R3 新完整批独立验收

ops/w08-r3-v1/runtime-a7e27341e2324ed1a410d4f785a1ceb2 新批执行/合作停止均exit0，24.125秒。唯一真实root e1c4e9f3-f3cf-42d7-8b33-85be537c5030 COMPLETED，原SQL6次KNOWN输入60输出30=90、provider6/90，无pending/unknown或其他批准/操作。唯一Coding session RELEASED/noRetention，真实Linux物理输入/外部合成canary/软链fixture与原唯一300秒anchor对应。

独立fresh强制只读原生graph.aget_state重建最新同id的12消息，五次实际ToolMessage与封存观测完全相同：合法读成功、三种边界读拒绝、合法写成功，safe内容存在、没有canary。原正式HTTP下载的封存receipt SHA和两条路径/尺寸/digest验证；独立查询最新artifact SQL并读取原UUID存储字节，两文件各SAFE_INPUT/10及SHA c156a3ba…与原下载记录逐项匹配。没有重开已停HTTP服务冒称再次下载。

CIM双PID20308/43916退出、61673无监听、exited nonce一致，worker/其他DB连接0，认证manager exactnamespace完整0。frozen346文件以及对应全部原实际editable/installed依赖均逐hash匹配。收据为该批reviewer-independent-verification.json，可限定接受实际文件边界和正式最新导出控制。原R2失败6调用90Token/1Coding消费另记且证据仍保留，不退款、不把新的6/90混成同一批或重复计案例。

F07 R4旧ffeb批独立另核：真实1个timer根SUCCEEDED/输出已处理超时，node.failed/node.handled都对应WORKFLOW_PURE_TIMEOUT且无normal；provider UNUSED、0usage，但3workflow/1model/1孤立auth session元数据已发生。原usage wrapper读取KeyError与未完成的F07保持，双PID/端口退出、336source一致，收据reviewer-independent-observer-failure.json，不能按qa业务完成0将实际timer根计成0。

## F07 R5 wrapper 修复及真实四根完整验收

R5 manifest feb45e92307bdda8467800f091cb12de0cf3d479606b586e5bf8a6c1960f1d85先严格验证正式用量wrapper的same workflow/root scope、complete=true、六计数真int非负/和及无pending，然后使用total；模型1UNKNOWN/null与纯节点全0原断言不改，封存保留完整wrapper。独立77工程通过6.96秒；再独立旧ffeb保留PG以正式workflow_usage/owned/聚合SQL和JSON序列化重现旧observe KeyError(calls)→新通过，原1root/3workflow/1model/0usage/0sandbox前后相等。证据reviewer/f07-r5-985610ffa5ed4d63bf79d3323218b51e，旧目标没有重开。R5 ops dc1459aa…/336输入b4db8a4b…相对已审R4仅目录及candidate摘要更新，独审通过。

新真实ops/f07-r5-v1/run-795addb628ad4bf3989ec5857d6ded82完整执行/合作退出均exit0，1.938秒，收据reviewer-independent-verification.json由非作者fresh只读SQL、正式wrapper与封存事件重算。四个实际根：timer初始化与发布v1两个纯计时器根均取消受控await并SUCCEEDED走超时fallback；JSON根SUCCEEDED，原node.failed为NODE_EXECUTION_FAILED，node.handled输出TRANSFORM_FAILED；model根FAILED于action、无output、无fallback或handled，未知效果保持失败传播。三纯根原生调用0，模型根唯一usage UNKNOWN、输入输出null、pending0，与same root/scope/owner的正式usage wrapper及sealed完全相等。

controller为CLOSED且4根/2pure、原120秒anchor不变；受控provider只1调用、15synthetic预留且UNKNOWN。原生estimated_input>0/output_token_reserve64继续保留，不把合成15预留说成实际模型输入输出已知，不退款/重发未知。0Work/Ask/sandbox/operation/worker，其他数据库连接0，双PID40252/32264退出、49606无监听、合作nonce与336冻结源匹配。一个孤立测试auth_session及原库保留。可限定接受真实纯转换超时错误分支和未知模型失败传播；不宣称真实供应商推理效果、整个平台执行开放或旧失败已转绿。

## 正式模型与密钥同步后的真实接口验证

用户本轮明确授权将正式模型及对应密钥同步到测试环境，并允许真实模型测试。已通过测试环境的正式模型管理接口创建三项配置：deepseek-flash、BAAI/bge-m3、BAAI/bge-reranker-v2-m3。源密钥仅在进程内解密并比对，测试环境用原有独立主密钥重新加密；没有复制主密钥、输出明文或改写正式模型记录。同步前后正式记录摘要一致，三项测试凭据与源凭据的内存比对均通过。

随后分别通过各自检测接口执行一次真实探测，无重试：聊天、文本向量、重排均 HTTP 200 且检测成功。共三次真实服务请求；检测接口没有返回 Token 和费用，因此记录为未知，不能记成零。探测证据位于私有 `model-sync-498165a213324b7f973b16c2679c0f34/real-probes.json`，同步证明位于同目录 `receipt.json`。

同步保留原启停状态：聊天模型原本禁用，测试副本也禁用；向量与重排模型启用。连接探测通过不代表已启用聊天选择，不代表 Ask/Work/Workflow 完整真实模型质量验收，也不增加限定受控案例覆盖数。后续上线必须继续保留双方各自的数据、模型记录和主密钥。

追加真实知识检索烟测：使用一份无用户信息的合成巡检材料，在测试3100完成实际向量索引、查询向量及重排。文档向量状态READY，检索mode=hybrid、reranked=true、无降级warning，首项正确包含预期的“每周四下午三点”。真实用量账本新增三条KNOWN：索引输入60、查询输入11、重排输入71，输出均为0，共输入142 Token。只发出这三次模型请求，无自动重试；费用未设置不能称免费。已经通过正式删除接口清理本轮合成知识库并核列表不存在，原用量记录保留，模型配置摘要不变。证据为私有 `real-knowledge-smoke-v2/receipt.json`、`search.json`、`usage.json`。

首次烟测辅助脚本在只读基线查询中误用model_usage.id，实际主键为call_id；在登录和创建资料前中止，未发起模型调用。保留v1失败意图，修正辅助脚本后v2新目录执行上述实际烟测，不把辅助脚本错误算作平台业务失败或额外完成案例。


## Canonical v2 集成稳定产品段独审（完整包尚未准入）

先仅核integration-v2-stage/product-mapping.json与product-changes.patch中的六个实际产品文件：AskWorker和F07 API/compiler/definition/pure_timeout/runtime。映射内7项baseline/source/staged原始hash均匹配，但本轮不将evaluation_target或CLI注册/薄adapter视为已审通过，完整manifest后另审。无正式拷贝、服务启停、PG或生成。

AskWorker只新增keyword-only可选mcp_client_factory和每次创建MCPService的实例工厂，默认None沿原生工厂，构造仍使用同database/vault，execute仍在真实ResourceRun内装载capabilities；原permissions、before_call/before_write、审批、租约、预算及UNKNOWN传播代码未改。注入只是已授权服务端测试钩子，不由用户请求设置。

F07五文件与已实际验收段相同：未设置timeout_ms时仍原同步预置转换；设置时严格1..5000真int，仅纯transform包内部时限。known分类必须来自实际expired的证明，绑定同Node/参数/task/resource scope，外部TimeoutError、取消及裸同名错误不成为known；HTTP/模型等副作用不包装。新Runtime参数keyword-only默认None，workflow_api仅传已有实例钩子，原权限/审批/预算未改。

独立私有镜像以正式app为基底，仅覆盖六个精确staged产品文件，42项工程通过5.73秒，无skip：既有workflow schema/compiler默认行为、真实纯timer/runtime/API桥接与反例、Ask默认/可选MCP工厂构造实例隔离。未启动HTTP或模型，不用TCP服务测试越过本轮边界。证据reviewer/canonical-products-c392b99b460e4c2f90363029f559d976/receipt.json及JUnit。结论仅为稳定产品diff无阻断，完整canonical manifest、CLI、packaging和正式发布仍待后续审查/准入。

## 真实知识库三请求烟测的独立只读核证

原烟测目录 `.local/acceptance-resume-20261105-root/real-knowledge-smoke-v2`，独立收据 `reviewer-independent-verification.json`。本轮仅读取原测试/正式数据库及已封存文件，不再次发起HTTP、POST、模型调用、业务或服务启停；不增加A20案例数。独立SQL脚本首次误用knowledge_documents表名，按正式003迁移纠正为documents/document_chunks后通过，属于只读核证脚本错误，无产品写操作。

真实3个call_id逐全行与原usage.json一致，状态均KNOWN，输入60/11/71、输出均0，总142 Token；分别为索引embedding、查询embedding和重排。共同真实owner、resource模式，索引根1调用，查询根2调用；两resource_operations均SUCCEEDED且finished_at存在/error_code为空，原KB、索引文档及revision1归属吻合。三个resource_snapshot_id的owner/mode/root/phase/model/revision与用量行对应，未将其他调用挪入烟测。

原搜索封存结果hybrid、reranked=true、warning=null、score_type=rerank，单条命中实际合成文档的每周四下午三点内容，文档/上传意图/搜索原文SHA相同；原文档READY、向量READY、1024维、模型revision1，无降级提示。fresh SQL自有KB/doc/chunk均已删除，原3用量及2操作记录保留。未新开被live进程占用的Qdrant客户端，结论仅为SQL/原API清理事实，不冒称底层向量文件物理删除。

正式3模型含原密文的行摘要仍匹配model-sync原source_snapshot_digest；测试3模型配置字段、启停状态和内存解密的凭据逐一对应正式来源，密文因测试key重加密而不同，未复制主密钥。对应目标模型UPDATE配置审计0；当前两套key字节摘要分别匹配live-environment-independent记录，且二者不同；测试351冻结源逐项hash匹配。核证不输出或保存明文凭据。原smoke只在进程内比较before_models、未持久其摘要，因此独立结论使用sync配置、凭据对应与零UPDATE审计交叉核验，不把receipt布尔值本身当独立证明。原v1查询主键错误的意图/失败保持，不重算为业务案例或请求退款。

收据额外保存本轮读取的JSON证据当前SHA清单，便于后续固定来源。结论为真实向量索引/混合检索/重排及用量和自有资料清理的小范围烟测通过，不代表聊天、judge或语义质量评测全覆盖。

## production-release-v1 运维完整包静审：阻断，未执行正式动作

独立逐读package-manifest/release.py/cold_host.py/tests/README，7个manifest文件hash匹配，9个临时文件/Mock工程守卫独立通过。证据reviewer/production-release-v1-isolated。没有连接正式库、启停服务、创建备份/克隆或请求模型；这些工程不能作为真实Windows原生退出、备份还原、冷启动或浏览器验收证明。

发现P1已交运维与主根，旧v1保持不可执行：maintenance直接CAS UPDATE未获取正式maintenance.GATE710035排他锁，无法等待持shared request_slot的已准入请求退出，后续stop可能取消原在途请求；quiescent只识active而未验证本contract lease_token/job_id，也漏正式maintenance.idle的resource_operations等在途保护，stop/backup可能借用另一维护所有者状态。须新v2严格维护排他drain、完整idle和每阶段本lease核验，并以隔离PG共享请求/外来owner/资源在途反例验证。

另P1为Windows入口：backup-clone分支asyncio.run使用默认Proactor，而psycopg.AsyncConnection要求Selector，会在已stop后才失败。需与cold_host一样显式Selector Runner，并补入口反例。v1成功的9项Mock未覆盖这些问题，不绿洗为已上线。

其他链未发现新增明确阻断：备份必须原项目ROOT/原模块SHA且保留710014/710017及45秒心跳拒绝；仅新随机_test库restore，失败保留、无migration/drop/生产restore；旧5身份经CIM/创建FILETIME/父子/listener+token绑定后持句柄合作停止，无强杀；原owner_agent/data/key及限权flags、Cold Ask先于API、Web3131代理8101和以最新数据应用回退边界合理。最终canonical与生产Web仍待固定，不以静审代替执行准入。

## Canonical CLI v2 完整候选独审（2026-11-05，第一轮）

候选 manifest SHA256 `30c732f67b6b897c79875ecc4453ee049cc240203ffe99fe29feb87d85532af8`：82 项正式基线和暂存 SHA 均匹配。已逐差异审查版本闭合注册、默认 v1、固定入口/工厂、生命周期事实重算、原生 UNKNOWN/null/清理保留、业务 JUnit 与独立 control_gate、递归 profile 文件和 LF/wheel 原字节约束。独立执行 104 项正常及私有工程检查全部通过（38.12 秒），无跳过；结果 `.local/team-workspace/remaining-cases-20261105/reviewer/canonical-v2-independent.xml`。此前产品 42 项结果独立保留，不计新业务案例。

**仍阻断正式复制：P1 运行依赖未纳入源冻结。** `scripts/evaluation_sources.py` 没有收录此前 W08 R3 已明确验证的六项实际依赖：editable Deep Agents 的 `graph.py`、`_messages_reducer.py`、`middleware/filesystem.py`，以及 API 环境实际安装的 LangGraph `pregel/main.py`、`channels/delta.py`、`checkpoint/postgres/aio.py`。六个当前真实文件 SHA 都与原 W08 R3 固定证据一致；但在全新临时目录复制后逐项加入变化，六次均无法改变本候选 source_fingerprint。缺失/漂移不能在执行前拒绝，可能把变更后的 Work 图、路径中间件或 Delta 重建标为 SOURCE_MATCHED。已交开发和主 Agent 修复，不要求扫描整个依赖环境。

反例脚本和收据：`.local/team-workspace/remaining-cases-20261105/reviewer/canonical-dependency-counterexample.py`、同名 `.json`。只修改临时复制件；0 正式源码修改、0 数据库连接、0 模型调用、0 服务启停、0 新任务。旧失败、UNKNOWN 和已消费额度均未改变。第一轮结论为等待最小修复后复审，不把工程通过视为迁移后完整 CLI 业务验收。

## Canonical CLI v2 R2 后继独审（2026-11-05）

manifest SHA256 `6d26c41746c2a72042d94a4ea8e21bfd85dfb46ed6e4e9ae2ca10438bdd35b8d`：84 项正式基线/候选 SHA 全部匹配；基线 HEAD 前移仅三个已审 Web 文件，不与84项重叠。独立核九处增量，六实际依赖的缺失、漂移与 API 解释器解析身份现在在 v2 执行前严格拒绝，v1不强制安装v2环境；此 P1 修复成立。ready 从严格 context 选 catalog 并重算事实与控制门禁、初始化前版本拒绝、PS1 参数转发均已复核。

独立运行29项直接专项：全部通过，无跳过，3.19秒（`reviewer/canonical-r2-focused.xml`）。新 wheel 的58个包内 Python/JSON 与对应候选/未改基线逐字节一致，实际 SHA256 `e93048a543c22cf145a71adc4aa3f1e144dc7fb50c9386928e984fb5d953c083` 与 manifest 相同；不重复无变更的124项广测。

发现剩余实际接线遗漏：suite `perform_attempt` 的新退出码逻辑按 attempt.profile_version 判别，但 `run_suite` 创建真实 attempt 时未写入该字段。真实 v2 仍会默认走 v1 的退出核对，把预期 control PASS/business NEEDS_REVIEW 误记 EXIT_REPORT_MISMATCH；测试直接构造带版本的 attempt，未覆盖此创建路径。已交开发补最小字段和从真实 suite 创建的闭环反例；业务 UNKNOWN/JUnit/清理保留尚未被洗绿，仍须后继修正后准入。0正式代码修改、0PG连接、0模型/服务/沙箱/业务批。

## Canonical CLI v2 R3 正式复制准入独审（2026-11-05）

manifest SHA256 `e13e3bbf79ffd06b10348fa2870e9ad878cf7f7ea2ea6dc7d81e715f4d461621`：独立复算84项当前正式基线/暂存SHA全部匹配，R2的84项原件SHA未变，R2→R3确仅两个文件。一处产品行为增量为suite真实attempt构造写入已校验的profile_version；另一处为新增两个真实run_suite→perform_attempt闭环工程测试。

独立执行新W07/F07两反例，2/2通过，无跳过，0.42秒；结果 `reviewer/canonical-r3-wiring.xml`。已观察真实suite构造的版本贯通，预期control通过不再误报EXIT_REPORT_MISMATCH，业务仍NEEDS_REVIEW、cleanup仍RETAINED、下一批停止且suite返回1。六依赖P1和版本/ready/PS1修复继承R2独立29项结果；其余无变化部分继承R1独立104项，不重复泛测。wheel源未变，继承已核58项原字节及e93048…SHA。

结论：本完整R3源码候选代码审查通过，允许主Agent按manifest原SHA守卫精确复制84项；不是正式上线或迁移后业务验收通过。仍需主Agent登记新批、冻结实际canonical源码，完成四v2案例完整CLI的实际验收、停止及未知现场核证，再决定部署。此次0正式写入、0PG连接、0模型/服务/沙箱/业务批；旧UNKNOWN、失败和配额原样保留。

## Canonical CLI v2 R4 常规质量修复独审（2026-11-05）

manifest SHA256 `033f8edf5377a14d3ffe8576eec468ef6a2ae80184a0b63daf37896fa2ca87eb`：20项精确before/after及84项正式最终postimage独立逐SHA匹配；R1/R2/R3 manifest与全部私有源原件仍匹配。完整读取20项diff，导入/格式/未用项、lambda局部函数、合并with、endswith tuple均为等价质量修复；F07循环reader的默认参数只绑定本轮base/root/responses，同步观察时不引入新副作用。四处registry raw pin更新符合对应实际字节；initializer和两caps字节与修改前一致。

独立使用正式evaluation环境运行两个受影响正常测试文件，56/56通过、0跳过、3.89秒，结果 `reviewer/canonical-r4-affected.xml`。包含pin/reader事实重算、六依赖、ready/context和真实suite版本闭环。未泛跑631/221，未生成新业务。结论：R4代码审查通过，可由主Agent固定候选提交；必须重新构建wheel并核验新字节，再按新实际canonical批验收。旧wheel/旧业务不能替代。本次0正式修改、0PG/服务/模型/沙箱动作，旧UNKNOWN与预算保留。

## Canonical A05 v2 标准 CLI 完整批独立验收（2026-11-05）

新批 `.local/agent-evaluations/canonical-v2-a05-1f1bf643f8e5461f853cf52728af373d`、candidate `e08eb2135b66c72fa671a887c86366652e33f0c0`，标准suite→target→fixed阶段。独立重算sealed ready/网络审计/绑定事实、用量、receiver SQLite和provider持久账，业务/控制/清理分别成立：注入场景根SUCCEEDED；撤权场景根FAILED/MCP_TOOL_DENIED为预期拒绝，最后unknown ToolMessage和tool.interrupted仍保留。每receiver真实lookup1、write0；每根2次已知受控调用/30合成Token，总4次、输入40/输出20=60，原父预留5次/75不返还，0sandbox。两receiver记录CLOSED，封存清理复算CLEAN。

fresh admin只读确认新库确不存在、连接0；原生wrapper14028/server12552均无，API51854无监听；receiver同server进程退出。formal cleanup/stop/target cleanup均0。完整源指纹与六个真实API运行依赖当前匹配。没有重开/重放或POST；已删原库未进行事后原表SQL复验，原生结果依封存证据，不能声称真实供应商模型抗注入已验证。

收据：上述新batch的 `reviewer-independent-verification.json`；脚本 `reviewer/verify-canonical-a05.py`。结论为canonical A05迁移后的限定控制验收通过、预期拒绝业务通过、清理通过，不增加不同业务case数；旧私有A05、失败、UNKNOWN与已消费预算均保留。

## Canonical W07 v2 首批现场独立复核：控制成立，清理失败（2026-11-05）

新批 `.local/agent-evaluations/canonical-v2-w07-f1b3bc3a1ea546b1a7dac7abc05acbd8`。独立fresh只读PG核1根NEEDS_REVIEW、1项真实已批准操作UNKNOWN且finished_at仍NULL；唯一审批approve已消费，模型账唯一KNOWN输入10/输出5，provider持久记录1次/15。真实receiver SQLite只有1attempt/1effect；consumer UUID换前后checkpoint、操作摘要、receiver和provider事实一致。无重放、无后继调用，原预算不返还。

业务控制重算PASS，原业务NEEDS_REVIEW/未知操作保留。两sandboxes均RELEASED，receiver CLOSED、MCP disabled且密文空串，worker实例/租约/其他连接0；双原生进程及API/receiver监听无，认证manager精确namespace库存0。完整source/六依赖与e08冻结一致。

**完整CLI未通过**：formal_cleanup_exit=2（ValueError），stop_exit=0，库保持RETAINED。cleanup失败后的publish_error已移除最终report-ready.json，partial-report明确acceptance_ready=false/ValueError；之前report内控制PASS事实仍可独立重算，但不能称最终封存ready或完整清理通过。缺陷已交开发做私有R5幂等修复，旧journal/报告/UNKNOWN/库均不改不重放。收据为本新batch的 `reviewer-independent-verification.json`，脚本 `reviewer/verify-canonical-w07.py`。没有新POST、模型或目标启停。

## W07 重复清理 R5 窄独审（2026-11-05）

manifest SHA256 `73fe6680d6f76f1e9cddb9ce2d612cfa3ce56cb6d1eee668935f76202074cab3` 三项正式基线及私有postimage逐SHA匹配。关闭态仅在原关闭证据hash/binding/stage、唯一CONFIRMED close intent（路径及空payload摘要）、当前整body与原关闭body相同且相对after只有closed=true时只读确认；仍复用完整before/after/recovery/v2facts校验。不发送POST/save，不延长开放态deadline，返回原保留UNKNOWN字符串。缺证据、漂移、关闭未知、重新开放均拒绝。

独立用本次真实canonical保留现场11个文件只读复现旧ValueError→新连续两次确认；11项哈希和journal结构不变，0网络/SQL/写。收据 `reviewer/r5-retained-red-green.json`。独立针对性工程37/37通过（1.09秒，`reviewer/canonical-r5-affected.xml`），新wheel58源/JSON原字节匹配且SHA符合manifest。结论：准主Agent精确复制3项、固定新commit后登记新完整CLI批；这不修复或回填旧失败ready/journal，旧UNKNOWN、库和已消费账保留，不把只读回放称真实新业务验收。

## Canonical R5 W07 新完整 CLI 批独立验收（2026-11-05）

新批 `.local/agent-evaluations/canonical-r5-aet-w07-581496725eb44a7d910755e39663b1aa`，candidate `ff48a0228df5a372e23fdffcd1912bfe373ff019`。已实际只读重算最终valid ready、封存控制事实与网络审计，control PASS；formal_cleanup_exit=1为原UNKNOWN精确保留语义，stop_exit=0，无ValueError/partial失败报告。业务及suite仍NEEDS_REVIEW、库RETAINED，不能标业务成功或允许删库。

fresh PG唯一根NEEDS_REVIEW、唯一已批准操作UNKNOWN且未finished，唯一审批已消费；唯一KNOWN用量输入10/输出5、provider1次/15。receiver真实SQLite 1attempt/1effect，重建consumer前后同checkpoint及receiver/provider/operation事实，未重放。两个sandboxes均RELEASED，worker/租约/其他连接0，receiver CLOSED、disabled且密文空串，双PID和API/receiver监听均退出，认证manager精确namespace0。全部source及六实际API依赖匹配，HEAD与candidate一致。父预留5次/1000Token/2sandbox仍保留；旧e08失败库、UNKNOWN、错误ready现场及消费不改。

收据位于本新批 `reviewer-independent-verification.json`；脚本 `reviewer/verify-canonical-r5-w07.py`。限定结论为新canonical完整控制/停止/保留序列有效，可接后续独立案例；未增加不同案例数，0新POST/模型/目标启停。

## Canonical R5 A05 新完整 CLI 批独立验收（2026-11-05）

新批 `.local/agent-evaluations/canonical-r5-aet-a05-006d4a6e9b06414db7be65c92019eafc`、candidate ff48，独立valid ready/审计及lifecycle重算PASS，预期注入拒绝与权限撤销两根原生记录吻合；最后unknown ToolMessage仍保留。provider及receiver SQLite各2次/30合成Token、lookup1/write0，总4KNOWN输入40/输出20=60；父预留5/75不退款，0sandbox。两receiver CLOSED；formal cleanup/stop/drop均0，fresh admin确认新DB不存在/连接0，wrapper/server 20072/36000及API监听无，全源/六实际依赖匹配ff48。

收据为新批 `reviewer-independent-verification.json`，脚本 `reviewer/verify-canonical-r5-a05.py`。封存事实重算与新库删除后的存在性/进程核查分开；不声称已删原库事后SQL，也不声称真实供应商抗注入质量通过。本次0新POST/模型/业务，旧A05批及消耗保持独立，限定canonical控制/清理通过。

## Canonical R5 W08 新完整 CLI 批独立验收（2026-11-05）

新批 `.local/agent-evaluations/canonical-r5-aet-w08-7c6654a0b56049958d52acfeb7e11335`、candidate ff48。valid ready/审计/control/business均PASS，封存Linux物理symlink与合法读写/三种拒绝/无canary、最新Delta只读重建proof、真实HTTP最新两成果下载均严格重算；provider6次/90、KNOWN输入60/输出30，原300秒单anchor。两份持久成果独立fresh读取均为10字节SAFE_INPUT，SHA与原HTTP下载回执相同。formal cleanup/stop/drop均0；fresh admin库不存在/连接0、双PID/监听退出、认证manager精确namespace0，全源码/六依赖ff48匹配。

正常删库已经完成，原生行及最新Delta消息不能再fresh SQL查询；收据明确引用原执行封存证明，不声称事后读取已删数据库或重连已释放Linux。receipt位于本批 `reviewer-independent-verification.json`，脚本 `reviewer/verify-canonical-r5-w08.py`。限定canonical控制与清理通过，旧失败消耗不退、不重放。

## Canonical R5 F07 初始化中断独立核证（2026-11-05）

新批 `.local/agent-evaluations/canonical-r5-aet-f07-fc2a44e3b76043a29e83a14b0fcb977a` 不完整验收：INIT_REVIEW、未调run阶段、formal cleanup2/stop0、无最终ready，partial明确未接受。fresh只读PG确认真实timer-init一根SUCCEEDED，原生事件WORKFLOW_PURE_TIMEOUT→handled并输出已处理超时；3个workflow、1个published version、1个model配置均已实际创建并保留。同owner通过workflow归属关联核验。模型账0、providerUNUSED/0调用/0Token，不把已产生的初始化效果报为零业务效果。controller仍ACTIVE；0Work/Ask/MCP/沙箱/operation/worker，其他连接0，wrapper20928/server45740及监听退出，全源码/六依赖匹配ff48，父预留1/15/0保留不返还。

收据位于本新批 `reviewer-independent-verification.json`，脚本 `reviewer/verify-canonical-r5-f07-partial.py`。未重启目标、未POST/重放、未改journal/ready/库。根因已交开发：真实init根started后publication要求尚不存在的run审计，正在私有修复。四案例最终标准CLI复验仍缺F07修复后新批，当前不能给出四例全部通过的发布结论。


## F07 初始化审计 R6 正式集成验证（待新批业务复验）

私有五文件补丁manifest `44967d23d641c863613ddd585760bb87f41abf94591cb655603d321ee4b7633c` 经非作者独审通过后由主Agent逐前后SHA精确集成。初始化仍记录真实根与started，仅明确v2 F07、单个OBSERVED timer-init及原证据完整绑定可使用对应COMPLETE init审计；已有run ID时不降级借用init，v1及一般run要求保持。初始化正常交接exit0，业务/control仍NEEDS_REVIEW，不能冒充整例通过。

正式源码完整评测工程652项通过，profile支持221项通过；这是工程检查数量，不是新增业务任务。profile首次调用因主Agent未给完整测试PYTHONPATH而收集失败，原日志保留；补齐原测试模块路径后通过，未修改产品代码或屏蔽警告。普通自动发现Ruff另发现新增测试导入顺序I001，已交开发做单文件排序并独审；原R6显式配置检查不足以覆盖此发现规则。旧失败批六封存文件只读红绿证明及独审61项检查均保留；旧批不补写审计字段、不重开、不恢复ready。

原始记录：`formal-final-r6-evaluation.xml`、`formal-final-r6-profile-green.xml`、原失败profile日志；私有reviewer/f07-init-r6独立审查与封存红绿收据。此节不宣告新F07完整业务验收、部署或整体发布。

R6-style独审通过：manifest `15ae8238530b7aa884ef5ff67e827748ef2b503809cb9c1be7d4ea93df1c7940`，仅导入移动一行加空行，普通Ruff及独跑9项通过。主Agent精确集成；未改运行源码、initializer pin或旧失败现场。
