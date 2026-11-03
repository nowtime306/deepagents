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
