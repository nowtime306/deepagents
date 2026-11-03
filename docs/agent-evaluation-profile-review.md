# 受控评测Profile独立代码审查

2026-11-03。审查者未编写本次profile源码或作者测试，未编辑这两个冻结文件。范围是受控模型factory及真实API/队列生命周期装配，不是完整profile客户端、A20验收或生产发布。

## 冻结及结论

核对`services/evaluation/.local/profile-freeze/manifest.json`，两文件SHA256一致：

- `profile_support/evaluation_target.py`：dd98682102ba0041737897228e9964f27ca627aad5809e229f8ba122c59d281c。
- `profile_support/tests/test_controlled_model.py`：6ada0184d10dc489bbbe21a227ec8e0cedd35199ea3ea08962f7e9a7c72a046f。

初审发现1项P2；**下述新冻结复审已CLOSED，profile单体代码审查通过**。作者单测不是本次独立结果。初审独立新增8项定向探针通过，另1项身份不变性反例失败并实际确认下述缺陷；原始记录保留，不改写为首次通过。

## P2：普通名称被当作执行脚本身份

`evaluation_target.py:225`用`config.name == "AE AET-W05 reviewer"`选择reviewer，否则选择lead。保持同一个config.id与`eval::AET-W05::v1`，只改普通显示名称，会从预设成员TimeoutError变成lead的task工具调用。这改变被测行为和未知结果分支，不能由稳定modelID/注册profile证明，更不能将它当作普通标签维护。

独立反例通过真实`model_factory(...).invoke(...)`：旧名称产生预期TimeoutError，改名后`pytest.raises(TimeoutError)`失败。两次使用独立ProfileState排除第一次故意poison对身份比较的影响；未接触数据库或远端服务。

作者已确认缺陷。修复应按明确受管modelID及固定case/role注册关系选择脚本；不能继续依赖可变名称，或通过放宽反例维持原实现。具体注册接口由root批准，不能顺便开放任意脚本、SQL或未核实用户引用。修复后需重放同ID改名行为不变、未登记身份拒绝及原有共享预算探针。

反例：`.local/team-workspace/agent-evaluation/test_profile_name_identity_repro.py`；日志`profile-name-identity-repro.log`，1 failed/0.63秒。

## 已核查且未发现该范围缺陷的部分

源码装配沿用原`create_app` lifespan，真实`AskWorker.serve`和可选`WorkEngine.events.serve`；原WorkflowQueue通过runtime动态factory运行。没有伪造业务HTTP响应。显式sandbox_config传入root新增的`evaluation_sandbox_connection`参数，profile本身不读取仓库manager.key。API注入hook由另一角色独立审查，本报告不重复声称其完整QA通过。

factory精确解析`eval::<已登记caseID>::v1`，拒绝外部模型名、未知case、其他版本/多余段及缺失resource scope，没有调用供应商resolve fallback。预算按资源根owner/mode/run共享，role、模型重建和嵌套资源上下文不产生新预算。持久pending在重建状态时转unknown；生成前写盘失败会阻止效果并使后续调用阻断。观测只保存计数、上下文摘要和固定布尔断言，未保存原始prompt或凭据。

独立探针`.local/team-workspace/agent-evaluation/test_profile_independent_review.py`实际8 passed/0.66秒，日志`profile-independent-result.log`及JUnit `profile-independent-results.xml`：四个精确模型身份反例、实际`resource_run`嵌套保持同root预算、显式unknown工具标记持久poison、真实临时文件系统reservation失败阻断生成、状态及观测无原始canary。

## 尚未证明的边界

`ToolMessage.additional_kwargs.result_unknown`仅验证显式标记会触发保护。当前冻结源码没有提供正式工具把真实OperationJournal UNKNOWN回执转换为此信号的完整链路；content JSON含UNKNOWN不能据此宣称已被识别。作者确认真实W07 receiver/回执/操作状态链仍待接入，必须在formal profile验收，而非用本次标记探针冒充真实未知写入已验证。

生命周期装配目前为代码审查；本次没有启动API、PostgreSQL、沙箱或目标，没有模拟真实进程重启，也未执行A20、真实供应商或浏览器。根角色与成员/嵌套预算探针使用真实factory/资源上下文函数和临时本地文件，但不是完整平台HTTP业务执行。评测观测digest不等于通用隐私安全证明；共享manager也不是Docker daemon隔离。

## P2修复独立复审：CLOSED

新清单`services/evaluation/.local/profile-p2-freeze/manifest.json` SHA65786454e71aca45da745fe64154bb6376cd182b4fbcb41c7e7912584e04f6ea。逐文件独立核对：profile源码3a03649c0f04821d0bd3ab7b0cf6aee42480d6673b64ba44c266504453bd4a32；作者测试f6054dc45d9e0f9f054ab9927f1cea13f1b2d575740f61d81b7263aa55c2ce87。

角色现来自持久sealed modelID→owner/case/role绑定，不再读取config.name决定脚本。`model_factory`核当前scope.owner、精确case与已登记ID；未知ID、跨owner或换case均在生成前拒绝，重绑冲突拒绝。独立探针实际普通改名并重建ProfileState后，reviewer仍抛TimeoutError；该未知状态再次重建后继续阻断，不重放。

注册动作仅由受控`build_app`增加POST `/api/v1/agent-evaluations/profiles/model-bindings`，普通`create_app`路由无此动作。源码顺序为真实environment证明→administrator→evaluation.run→核owner→ModelRegistry.get实际配置→chat/openai/精确eval字符串→sealed登记。typed body拒绝额外script字段、未知case/role；没有任意代码/SQL接口。权限与配置检查在本轮为代码审查，实际HTTP鉴权和目标启动另行实测，不能用函数探针替代。

独立复审`test_profile_p2_independent_review.py`最终11 passed/5.10秒：在原8项中只增加新契约所需的显式绑定setup，生成/预算/失败断言不放宽，再加实际改名Timeout及持久拒重放、unknownID/跨owner/错case、target-only路由及严格body三组。首次复审脚本两处setup问题（绑定写盘发生于故障注入前、路由集合含无path的IncludedRouter）已修本地探针，首次日志`profile-p2-probe-harness-failure.log`保留；没有修改冻结产品或作者测试。

最终日志`.local/team-workspace/agent-evaluation/profile-p2-independent-result.log`。原core R1–R4亦已由其他独立角色在core-r4-freeze报告CLOSED，本报告只核对其状态，不把他人的重放算自己的执行。

本结论允许root按既有授权推进单独非Work目标HTTP启动/证明检查，不代表A20、完整fixed profile初始化/观察/清理、真实沙箱执行、UI或部署通过。

## A02扩展bundle独审：PF1/PF2 OPEN

新增冻结`services/evaluation/.local/fixed-a02-freeze/manifest.json` SHA e6fc6b104c6aa83d76a3cdcd9567e1b8dfac94877d50451f5166d42f625eb07a，11文件逐hash核吻合。本轮审查范围仅profile target/observers/sandbox_cap/embeddings及API-venv作者测试；固定客户端另由其他角色审查。前一单体名称P2保持CLOSED，不把它误说成新增bundle已整体通过。作者15单测不是本次独立QA。

**PF1 / P2 OPEN：上下文观测没有按mode隔离。** observers的`context_assertions`调用`state.summary(owner,run_id)`；summary只筛owner/run UUID，不筛mode。同owner、同UUID的ask和workflow根均有合法受控调用时，ask观察返回正确的ask root_summary，却同时包含workflow的上下文布尔/摘要。调用者能给workflow指定run UUID，因此不能依赖不同表UUID永不相同。这可能将其他mode的上下文约束当作本次根证据，需完整owner/mode/run三元组过滤。

独立探针创建两个真实ResourceRun上下文与真实ControlledModel调用，再调用实际observations路由closure；仅environment/owned读取边界使用受控stub，不运行HTTP或PG。ask根只有一条普通问题、workflow根有两条项目/人数消息，返回context_assertions混入workflow，预期只ask的断言失败。

**PF2 / P2 OPEN：预算拒绝发生在原生用量start之后。** controlled embedding先`meter.start(texts)`再`state.reserve`。真实受控chat已耗尽A03根额度后，embedding在provider效果前被reserve拒绝，但meter.start和meter.error均调用1次。原EmbeddingMeter.error写UNKNOWN，因此会把已知未发起的embedding效果记录为用量未知，污染调用证据。应先执行预算准入，receipt存在后才允许finish/error；meter初始化失败也须保留正确的根poison/持久状态，不能伪造无调用用量。

独立探针实际调用6次ControlledModel耗尽根上限，再经真实install_embeddings的embedder尝试，确认ProfileState调用数仍6，而受控Meter收到1次start/error；“未发生embedding时不得启动native usage”的反例失败。Meter是接口替身，未宣称实测数据库用量写入；UNKNOWN结论同时依据原resource_usage.EmbeddingMeter.start/error源码调用链。

证据：`.local/team-workspace/agent-evaluation/test_a02_profile_review_findings.py`、`a02-profile-review-findings.log`，实际2 failed/1.71秒。没有编辑冻结源码/作者测试，没有启动target/manager/容器或模型供应商。root已要求作者最小修复并重新冻结，修后独立重放关闭再决定工程QA和真实A02。

其余已读边界：BoundedSandboxes使用真实DurableSandboxes，生成前持久计数，只有已知本地reserveNone退款，异常/未知不退、重建保留最大2；browser种类早拒。capabilities只返回计数/本namespace管理器可达，明确image_execution_verified=false，不等于镜像执行已验；readiness未泄其他namespace内容或manager key。embedding只允许已封存owner/case/kind/protocol匹配，rerank显式拒绝无供应商fallback。上述为代码审查，仍不代表真实沙箱或完整A02业务通过。

## PF1/PF2最小修复复审与独立工程QA：CLOSED

修复清单`services/evaluation/.local/fixed-a02-review-fix/manifest.json` SHA dd12cba4c666cdc798f3e4e0acc883fb4ef60131335441e4a875b826cd426222。叠加base e6fc后的13个源/作者测试文件，执行前后逐hash一致。R01固定客户端/report修复由其他独立角色审查，不借其结果冒作本角色验证。

PF1的summary签名现在强制owner/mode/run三元组，observer传入mode并精确筛选。原同UUID跨模式反例重放只返回ask行，独立探针关闭。PF2先执行持久state.reserve，预算拒绝不会调用native meter.start/error；receipt为空时不调用meter.error。meter.start失败会持久保留7tokens reservation并poison为未知，重建不能继续调用；这是保守额度，不伪造已报告用量。源码与原预算拒绝反例均复审通过。

root授权后，精确指定4个冻结测试文件（controlled_model/embeddings/observer_scope/sandbox_cap）及本角色原2个独立反例，在API独立venv实际执行：**20 passed、0 failed、0 skipped，1.95秒＝18项工程测试＋2项独立缺陷探针**。没有把作者报告直接计作QA，没有HTTP目标/PG/manager/容器/真实供应商或A20执行。只调用已安装API依赖，私有输出，禁dotenv和自动插件，不传供应商/Confident凭据。

结果在`.local/agent-evaluations/profile-pf-independent-exact-59381ce2/`的私有日志/JUnit；字节核对及口径在`.local/team-workspace/agent-evaluation/profile-pf-review-qa/before.json`、`result-exact.json`。首轮按目录收集意外多含2个未冻结test_case_cap WIP，22 passed日志保留于`profile-pf-independent-18b726de/`，该额外2项明确不纳入本scope；随后用精确文件重跑，未改产品/断言来获得通过。

按root要求新增最小`suite-capabilities.json`：schema1、已审fixed_run SHA d51f5294558901c73f0cc2084de255b2d1e959bea89cfe3761b303f5b2400684、仅A02 sandbox_bounds=2；文件SHA 3abdab11f794347dcd2efccba54d9077b084a141c35cf588f18bcd6d9ef4f8b4。2是describe的保守预留上界，A02固定实现无创建沙箱步骤；本轮工程QA也未创建沙箱。该契约等待root静态核对和提交固定HEAD，并非真实A02完成声明。其余case继续BLOCKED；真实单例仍须全局短暂停源更改、核source集合后另获授权执行。

## F02/F04 profile/API 静态独审（2026-11-04）

冻结清单`services/evaluation/.local/fixed-workflow-freeze/manifest.json` SHA256 `7d427a2221221b5a6dff101ad681f6c965128925082ae4d9b8c8ed242590968b`，source HEAD `c4d83a243832562b132d1affe8d38ec8eec7aedf`。清单14项逐字节摘要吻合；本角色审查新增evaluation_target/evaluation_observers及profile测试，Workflow事实封存和公共/W01接线由其他角色分别审查。本轮不编辑这些冻结源、不运行作者测试、探针、HTTP目标、PostgreSQL、Work或Docker任务。

静态审查结论：本范围未发现新的阻断缺陷，允许root决定后续工程及真实PG/HTTP计数专项；不等于三例业务或SQL执行已通过。

新增观测路由只由受控build_app的attach_observers挂载，原production create_app不增加该路由。environment沿用真实登录、marker/环境UUID、DB_test、owner、namespace和候选校验；请求只接受UUID workflow ID，没有SQL、文件路径或任意代码输入。单条SQL先用workflow ID及已证明owner筛选，其他owner无行返回404。取回后要求modelID在持久封存表中属于同owner、F02/F04和lead角色；普通name不参与身份判断。该接口只返回固定计数、UUID及完整性标记，不导出节点日志、文本、凭据或SQL。它是独立评测owner的总量观测，不应描述为只计当前单root的用量。

DB计数使用同一SQL语句快照，区分当前workflow的run IDs和owner全部workflow/events、Ask、Work任务/运行、原生model_usage、Work操作、沙箱会话，以及活动/未知状态。provider总计在ProfileState锁内按owner聚合，其他owner不会混入；持久storage_failed、pending或unknown root使complete为false。沙箱服务缺失且Work启用时complete为false；Work关闭时0预约是明确能力约束，不用缺失观测冒充真实空管理器。DB计数、内存provider状态与沙箱计数并不构成跨存储事务，不能声称为宿主全局效果或共享daemon隔离证明。

F02/F04固定LIMITS为0calls/0tokens，真实model_factory仍要求资源运行上下文及sealed ID匹配，reserve在生成前拒绝。更换root、重建模型或重载ProfileState不会获得非零额度；A02限制未随此修改。作者新增测试包含真实ControlledModel零cap和ASGI fakeDB归属/缺失/完整性断言，但仅作审查材料；本角色没有将作者11项或完整29项结果计作独立QA。

后续真实计数专项需要在新随机_test库和私有marker下验证：SQL实际可执行及字段类型；同owner/其他owner/不存在workflow隔离；绑定case/owner不匹配拒绝；初始化及事件计数准确；Ask/Work/model_usage/操作/沙箱及活动/未知状态分别影响固定计数；provider storage/pending/unknown与Work开启但无service不能形成complete零。只有root明确授权后执行，且该专项仍不是W01/F02/F04真实业务验收。新三例capabilities/initializer摘要、父预算和固定候选要在全部独审/工程闭合后单独更新冻结，不能现在沿用仅A02的契约派发。

## 授权后独立工程及真实 PG/HTTP 投影专项：通过

root明确授权后，API独立venv精确执行controlled_model、embeddings、observer_scope、sandbox_cap、workflow_profiles五个文件：**29 passed、0 failed、0 skipped，1.95秒**。9个相关源/测试文件前后摘要一致，新增三项匹配7d427a冻结。证据`.local/agent-evaluations/profile-workflow-independent-7575a1a506cb425cbac6089f11c26049/`含日志、JUnit及before/after摘要；29包含新增11，不能再相加。

随后执行一个真实PostgreSQL与物理loopback HTTP专项场景，**230条检查通过**。这些是同一场景内的断言数量，不是230个pytest或业务案例。全新库`owner_agent_projection_5d6fb758ef9f42a38a3ab403772c9a2b_test`、新data/Vault/两专用账号及marker，使用实际build_app的路由、正式登录、环境/owner/namespace校验、ModelRegistry和sealed model-binding端点；替换生命周期仅打开DB池及本次ProfileState，不进入原生命周期，不启动任何Ask/Work/Workflow消费者或调度器。没有调用Agent/Workflow/Work业务运行接口，没有管理器、容器、供应商或DeepEval评分调用。

真实端点SQL已执行，覆盖固定字段/JSON整数类型、UUID run IDs、当前workflow和owner总量，以及其他owner的全部夹具行排除。通过正式HTTP验证匿名401、实际环境证明、模型创建201、封存绑定200、其他账号的marker归属拒绝及missing/foreign workflow拒绝；内存注册表的明确负面夹具覆盖错owner/case/role及unsealed模型。SQL夹具覆盖workflow/节点事件、Ask、Work任务与运行、原生模型用量、操作和sandbox session计数；正负状态切换包括PENDING/RUNNING、终态、Work等待审批/暂停/NEEDS_REVIEW、操作STARTED/UNKNOWN、用量PENDING/UNKNOWN/KNOWN及session五状态。

同场景直接调用真实model_factory与资源上下文，证明F02/F04跨root及ProfileState重载均被0/0额度阻止，不启动业务runtime或记录虚构供应商调用。provider pending/unknown/storage失败、Work启用但缺沙箱服务都返回不完整；沙箱计数服务属性分支使用明确受控对象，**没有把它当成真实manager或容器验证**。夹具中的UNKNOWN/沙箱会话仅是手工SQL种入的测试状态，未对应任何远端未决效果；工人注册表为0，不能将这些行声称为实际未知写入验收。

成功收据`.local/agent-evaluations/workflow-projection-pg-20f2d85d3ce346939500d880d4a3e826/receipt.json` SHA256 `ffe41cc7fffa2d35a896360305c85ac697305dc691462b787ba367a786993abf`，含所有断言和HTTP投影，profile三文件前后摘要匹配。服务合作停止、DB池关闭后核该库连接0，再不带FORCE精确DROP，核pg_database不存在；私有Vault/marker及证据保留。没有使用生产库、生产Vault或用户数据。

两次harness问题如实保留：首个目录`workflow-projection-pg-c7193244bfdb4e5a94ce671e16c69c9e`使用短账户名设置ACL导致创建data子目录WinError5，发生在CREATE DATABASE/HTTP之前；只改私有harness为已有New-EvaluationOutput授权方式。第二个目录`workflow-projection-pg-ef120e183af04573bb4bef23a26d8503`误将正式模型创建201期望为200而停止，前四项HTTP/认证已过；仅按真实model_api契约修断言，再用全新库执行。第二轮原收据保留，独立核其audit真实201一次及所有业务/用量/操作/沙箱/worker表为0、连接0后，精确回收其自有库；`fixture-failure-cleanup.json` SHA256 `1e9c30d3d2690c0f1cb0c9c375709fda02276132e6a37b480e872f0c8c48a8e2`。这两次不计产品失败，也不混入成功轮断言。

本结论关闭profile/API代码与计数投影专项，不扩大为W01/F02/F04三业务通过。真实Coding、Workflow初始化/发布/执行/拒绝输入、固定事实快照、真实namespace创建释放及最终ready清理仍须在各自审查、固定新候选与单独授权后执行。A02原已验收范围不受此夹具专项代替或增加数量。
