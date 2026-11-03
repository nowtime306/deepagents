# RDC Runtime / Index / Trace 独立 QA 报告

日期：2026-11-03。最终冻结提交：`fcdc1dd1ef8022dfe80a4d20f4ebd972578b0a0b`。本报告仅负责根任务指定的默认无真实 Sandbox 七组范围。

## 1. 最终结论与两轮区分

最终完整重跑结果为 **401 passed、0 failed、21 skipped**，七组均执行完毕，未修改执行中的生产源码或测试以获得通过。21 项跳过仍为未验证，不计通过。该结论不等于 RDC01–14 全产品、实际页面、真实供应商或真实 Coding 环境全部验收通过。

初轮冻结 `833a4ac212b2103d05e3a73e352d27af74d52e07` 的结果仍为 **332 passed、5 failed、21 skipped**，失败证据完整保留。两轮数字不相加：最终轮是在新冻结版本完整重跑，原五个失败场景均通过，并新增工具 schema 规范化单测文件展开后的 64 项；401 = 原332项通过 + 原5项复验通过 + 新64项通过。

本人是配置历史、早期路由、仓库及通知相关实现作者，不以本人作者自测作为这些模块的独立验收结论。此轮独立 QA 重点为其他作者的 runtime/index/root trace、启动路由及相关后端边界；下文逐项标明范围限制。根任务并行执行的实际 Coding 恢复、exactly-once 与真实 stdio/ACP 专项另有报告，本报告没有借用其结果填充 skip。

## 2. 开始 gate、隔离与源码完整性

根任务明确授权最终七组重跑，确认上述冻结提交工作区 clean、083/backup 与全部阻断审查关闭，并确认 helper02B、history 分类及强类型 canonicalization 修复均已独立复审关闭。前置审查属于开始条件，其作者测试与审查探针数量不计入本报告的 401 项。

- [最终开始 gate](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/start-gate-fcdc1dd1.json)；[最终源码/测试 manifest](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/execution-manifest-fcdc1dd1.json)。共 64 个测试文件、257 个顶层测试函数，参数化展开实际 422 项，不能用函数数量代替结果数量。
- 测试摘要：`d29e5d5eddae1e339059f9d564ce91ce737dd9c381f99347870c69b2f23dc013`；`app/*.py` 与 `migrations/*.sql` 摘要：`7c94347825171b02bac75a82c4c492ac882b03810840eaedc5ec9af1ad47924c`。
- [执行后完整性检查](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/post-run-verification.json)确认测试与后端摘要执行前后相同、旧833结果摘要未改动、七个自建测试库全部回收。本人不执行 Git，提交身份由根任务冻结通知提供，另用内容摘要锁定实际执行源码。
- 每组使用随机 `owner_agent_rdc_indqa_<组>_<随机>_test`、独立数据目录、向量目录及 CredentialVault。只读取本机连接凭据来源，用 postgres 管理库 CREATE/DROP 自有测试库；未连接生产业务库。清理前校验名称前缀及 `_test` 后缀，七组 `owned_database_removed=true`。
- 自动消费者默认关闭；测试自行调用明确的 worker/engine。真实供应商、真实浏览器、RUN_LOCAL_SANDBOX 均关闭。子进程限制非 loopback 网络连接，并禁止 3100/3131；协议替身仅运行本机受控接收器，日志写盘前脱敏数据库连接与密码。
- 未访问此前被拒页面、未构建前端、未覆盖默认 `.next`、未复制新 UI 到 3100。`PLATFORM_WEB_DIST_DIR` 指向私有独立目录。

[执行器](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_runtime_index_qa.py)与[最终机器结果](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/summary.json)保留完整组清单和失败/跳过输出。新增独立验收文件四例全部通过；usage 与 workflow 节点两处旧测试由本人改为实质正反回归，经根任务审阅后执行。其余采用已有/实现作者测试源码，由本审查者在新隔离环境独立运行，不直接引用作者先前的通过数字。

## 3. 最终七组结果

- **acceptance**：4 通过、0 失败、0 跳过，8.58 秒；[完整日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/acceptance/pytest.log)。
- **runtime**：185 通过、0 失败、3 跳过，46.45 秒；[完整日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/runtime/pytest.log)。
- **workflows**：88 通过、0 失败、2 跳过，73.15 秒；[完整日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/workflows/pytest.log)。
- **index**：65 通过、0 失败、2 跳过，72.89 秒；[完整日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/index/pytest.log)。
- **tools**：37 通过、0 失败、14 跳过，20.44 秒；[完整日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/tools/pytest.log)。
- **trace_recovery**：13 通过、0 失败、0 跳过，12.85 秒；[完整日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/trace_recovery/pytest.log)。
- **legacy**：9 通过、0 失败、0 跳过，10.36 秒；[完整日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_84a988f41ab9/legacy/pytest.log)。

总计 401 通过、0 失败、21 跳过、0 错误；各组 pytest 耗时合计 244.72 秒，不含全部数据库管理时间。预期反例中停用、预算耗尽或撤权的错误日志，只在对应断言通过时计通过；不能仅凭日志出现 ERROR 另计失败，也不能用 exit code 0 掩盖 skip。

## 4. 初轮五项失败的最终复验

1. **Q-01 Ask 持久队列启动路由：已复验通过。** 初轮新增测试漏设 `native_search:false`，自定义候选无原生联网能力，入队即 ROUTE_NO_CANDIDATE，未到消费者。最小补前提后，本轮真实 `AskWorker.tick` 消费，断言排队时备选、维护后实际启动选主模型修订2及温度0.49、HTTP trace 路由修订2与实际调用一致；没有 inline 冒充队列。
2. **Q-02 空 properties 等价被误拒：已复验通过。** 保留原 `test_agent_bindings` 替身差异，没有修改它来掩盖问题。产品经过独立审查的约束规范化后，该绑定/撤销场景通过。新增文件的64项同时验证注释/空 properties 等价、同名数据属性与 default/enum/const 内容保留、复杂引用保守处理，以及布尔与数字在本地/expected/remote/最后调用检查中不能混同；真实约束改变仍拒绝。
3. **Q-03 旧 revision 一变就停止：已替换为需求成对回归并通过。** 同一 `resource_run` 中使用真实模型回调与不同 callID：普通 revision/温度维护后，两个受控 provider 调用与两条用量均保持实际原修订/原参数。停用后直接调用已构建 provider，由实时 UsageRecorder 阻断，provider 次数与 usage 行均不增加。没有恢复错误的通用修订阻断。
4. **Q-04 workflow MCP 旧 invoke 替身/旧 URL 断言：已实质改写并通过。** 替身下移到 session，真实 invoke、freeze、权限与接口检查保留。原工作流发布版新 run 使用更新后 URL，两 run 各有实际 used 快照；`list_tools` 内撤销绑定后实际 `call_tool` 零新增，节点记录 MCP_BINDING_REVOKED，后续预检也拒绝。不是增加宽泛 kwargs 让旧替身蒙混通过。
5. **Q-05 legacy SSE 子进程依赖：已复验通过。** 使用当前 venv 的 `sys.executable -I` 隔离系统 user-site，本机受控 SSE 服务正常启动并发现两项工具。这个既有用例只断言 SSE discovery，名称虽含 call，源码没有实际 SSE call 断言；不能将它报告为 SSE 调用全链路已覆盖。另有 Streamable HTTP 实际读写/响应丢失用例通过。

以上修复先由相应作者/根任务处理，必要独立审查关闭后统一新冻结；本轮完整重跑七组，没有只选原失败用例。

## 5. RDC01–14 编号覆盖与缺项

### RDC-01：发布智能体继续引用当前模型配置

agent_versions、resource_decoupling、automations 等用例覆盖发布后仅维护模型仍可创建对话/主动任务、后台实际替身执行，以及停用、非聊天用途和能力不可用等反例。新增 Ask 队列专项本轮已执行到实际消费者并通过，前提失败不再充当通过证据。真实 Work 自动任务路径仍属于三项 runtime skip 之一。

### RDC-02：发布团队成员模型维护与多模型追溯

新增独立场景中，父子及两成员发布 v1 后仅维护研究成员的模型地址/key/温度；真实委派经受控 ToolModel 完成。HTTP trace 保留父与成员业务版本、区分三个模型及 lead/researcher/reviewer 角色，实际修订1/2/1正确，成员提示词仍为v1。成员撤权/下线、共享预算等已有反例也通过。真实 Work 团队环境未启用，不能外推。

### RDC-03：工作流发布版、多入口与组合

旧 v1 设为当前、显式 v2 不漂移、子流程固定业务版、草稿测试门槛、CAS、不可变发布历史、Ask/Work 工具与外部 API 等用例通过。原 MCP 节点串联 agent 的失败场景已改为真实 invoke 路径并通过，维护 URL 无需重发工作流；授权撤销仍拒绝。真实固定代码/Dify 工作流的两项环境 gate 未开启。

### RDC-04：按实际开始解析及同运行固定

同运行实际模型调用之间维护仍固定、新运行获取新配置，以及检索阈值/重排/记忆绑定按阶段固定、实时撤权等通过。实际 `AskWorker.tick` 排队更新后重选路由通过。新增 `WorkEngine.execute` 在路由刷新并首次 factory 解析最新配置后，由受控 `sandboxes.create` 返回 None 触发 CapacityUnavailable：证明启动前解析边界，不宣称已经真实模型推理或任务环境执行，trace 对应 resolved/calls空。

cold cache 确认新 phase 的 HTTP 记录及同 host 不同8443/9443端口差异通过；这是清理进程内凭据缓存模拟，非完整操作系统进程重启恢复。真实 Coding 消费者恢复由根任务另行专项报告。

### RDC-05：本人作者范围，不作独立验收通过结论

本人排除 history 自测文件。新版082在这七组的全迁移依赖中正常执行，root trace/legacy 的关联查询也通过，但不以此代替配置历史分类、事务、脱敏及 actor 的另一位独立验收。history分类9项作者自验和runtime独审探针均不计本报告401项。

### RDC-06：实际配置追溯、当前差异与恢复阶段

新增多模型团队 HTTP trace、实际角色/修订、普通运行与子流程共享 scope、未知旧数据不伪造、resolved 与 used 区分、恢复 phase1保留/phase2独立确认、模型当前差异、owner隔离与脱敏等通过。端口及目标摘要差异能够区分。当前 agent/memory/knowledge 投影与 JSON normalize 的小改仅有先前静态复核，没有本轮专门的三类 HTTP 序列化回归，不扩称已验收。capability-only 历史关联用例通过，历史本体验收仍归 RDC-05。运行页跳历史再返回没有实际 UI 证据。

### RDC-07：仅补充验证其他作者的启动与 trace 接入

本人参与过早期路由实现，不宣称整个路由产品独立验收。root 新增 actual-start 选择及 route trace 的 inline、AskWorker、WorkEngine 边界已通过；旧路由文件中本人修改过的作者测试未作为本范围独立依据。接收范围/同意本体须由另一位独立验收者判断。

### RDC-08：向量身份、兼容映射与多模态冻结

文本/记忆/图片兼容、同维不同身份、旧向量未知、管理员显式兼容证据、同身份 query 输出维度变化的明确关键词降级等通过。video snapshot 用例沿真实 process/index_frame 路径、使用受控解码/向量替身：同 job 三帧保持原目标/key/参数/修订1，新 job 用修订2，停用后无第五次向量请求，台账关联对应快照。不是实际解码镜像或真实供应商输出等价性联调；两项真实 Work 语义记忆场景跳过。

### RDC-09：实验、评测适用性与门禁

历史评测保留、配置适用性、严格门禁与关闭门禁、人工引用/拒答标注、来源变化中断、未知用量/结果不算完整成功等通过。全部使用受控结果；不将合成/替身回归作为真实供应商质量提升的证据。

### RDC-10：MCP/Skill 的热冷配置、接口与审批身份

热目标/key 固定、冷目标变化需确认、发现后实际调用前权限/绑定撤销、本地具体 schema/readonly 改变拒绝、注释性维护不误拒、审批 hash 保持真正约束/布尔数字强类型、旧 Skill 冻结内容及审批轨迹不可替换等通过。浏览器运行器均为替身。受控本机 Streamable HTTP 实际工具调用与 legacy SSE 发现通过；SSE call 的特定用例缺口见 Q-05。真实 stdio/ACP、容器背压与生命周期14项仍跳过，实际浏览器未操作。

### RDC-11：仓库属于本人作者范围

未运行仓库作者自测作为独立验收，未实际推送仓库或执行外部写入。本轮不能关闭仓库身份/凭据/审批范围的独立验收。

### RDC-12：通知属于本人作者范围

未运行通知作者自测作为独立验收。legacy 的旧通知目标反例只作为迁移依赖回归，不关闭通知产品验收；没有发送外部通知，也没有操作通知确认页面。

### RDC-13：实时权限、能力、停用与明确失败边界

账户/资源/成员撤权、业务版本下线、任务取消、租约、预算、写审批、UNKNOWN不自动重复等反例通过。新的模型成对回归证明普通 revision 不误阻断，而 live 停用阻断已建 provider 且无新增账本；MCP schema规范化仍保留真实约束、readonly、权限与目标差异，发现与实际调用之间撤权无工具效果。真实环境 exactly-once 的根任务专项不混入本结论。

### RDC-14：后端定位与中文错误有证据，实际页面未验收

节点/成员定位、资源不可用、向量不兼容、恢复待确认、实际配置与当前差异等 API 字段及错误分支已执行。3100/3131 页面此前访问被拒，本轮未绕过；窄屏、键盘焦点、只读修改记录按钮、未保存状态保护、导航返回与通知目标确认交互仍未验收。不用后端通过推断新 UI 体验已通过。

## 6. 最终21项 skip及外部 gate

- runtime 3项：`test_agent_teams.py:472` 实际 Work 团队；`test_automations.py:229` 自动 Work；`test_work_execution.py:54` 实际 worker 审批/成果。
- workflows 2项：`test_workflow_dify.py:129` 与 `test_workflow_tasks.py:69`，实际 Coding Dify/固定工作流。
- index 2项：`test_memory_vectors.py:243` 参数化两例，实际 Work 语义记忆。
- tools 14项：`test_tool_expansion.py:275` 两例，真实 stdio/ACP；`test_tool_backpressure.py:12` 四例；`test_tool_lifecycle.py:12` 八例，均要求可信 Coding 镜像。

这些为源码显式 gate，未启用而跳过；不计通过。根任务的实际 Coding 专项即使覆盖其中相近场景，也必须另标提交、环境、执行主体与证据，不能直接把本轮21项改为已通过。真实供应商、被拒实际 UI 及上述 SSE call 缺口继续明确保留。

## 7. 旧833证据保留与交付边界

旧833各组仍为：acceptance 3通过/1失败；runtime119通过/2失败/3跳过；workflows87通过/1失败/2跳过；index65通过/2跳过；tools36通过/1失败/14跳过；trace13通过；legacy9通过。总计332通过、5失败、21跳过。

- [旧833机器结果](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/summary.json)，SHA256 `2b5dde4ecff19cbc9e5ef782825a3b08deef3aa77f41babc500b565d2d298bd9`；各组原 pytest.log 未覆盖。
- [旧833原报告逐字节归档](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/original-833-qa-report.md)，SHA256 `49da65b3a3c9bcab5b1b77444cadd156a32a6ad6c6711a516cf65e06f3a98882`。
- [旧833源码清单](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/execution-manifest-833a4ac.json)与[start gate](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/start-gate-833a4ac.json)独立保留；最终轮使用另一份fcdc清单/gate与run目录。

本轮默认无真实环境的指定范围执行完成、没有剩余失败。源码后续若再变更，须针对新冻结版本确定必要复验；本报告不授权部署，也不将作者范围与未开启 gate 宣布验收通过。

## 8. 后续授权补测：实际 Coding 环境的18个原跳过用例

本节为第1～7节结束后根任务另行授权的独立补测，保留原先 **401 passed、0 failed、21 skipped** 的执行范围与结论，不追溯将 skipped 改为 passed，也不与其他角色的专项数字混加。根任务已完成镜像/manager恢复及其独立专项；本角色在根任务释放 manager 容量后顺序执行本节七组。实际页面、外部供应商与生产部署均未执行。

### 8.1 基线、预检与真实执行边界

- 生产源码继续为 `fcdc1dd1ef8022dfe80a4d20f4ebd972578b0a0b`；根任务提供后续仅测试观察层提交前缀 `1dc`。补测开始前逐项匹配原 fcdc 的后端摘要；本轮生产与全部测试文件另存精确内容摘要，执行后再次核对全部不变。没有修改产品、测试断言、kernel 或安全参数。
- 只读预检后选择7个测试函数/18个参数化实例：Work团队1、自动Work1、Dify1、固定工作流1、memory2、backpressure4、lifecycle8。模型使用 ToolModel，embedding 使用随机 loopback 接收器；没有被拒页面或真实外部服务。五组实际 Work 经本机18180管理器，协议组在 `--network none` 的实际 Docker 容器中执行。
- 本地镜像 `owner-agent/coding:dev` ID：`sha256:459420e199a24347bed6091ca0683cc570e7fd4c3737f086c3b43533c89c7ce9`。本角色没有构建/拉取或改变镜像与服务容量。每组顺序执行，遵循既有5Gi准入或测试原有128m/CPU限制；没有绕开内存门槛。
- [补测 manifest](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/manifest.json)记录根授权、组选择、源码摘要及镜像；[机器结果](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/summary.json)记录真实逐例结果。本节不是函数级进程替身：Work任务与协议子进程均使用实际 Coding 环境，外部模型结果仍是替身。

### 8.2 分组实际结果

- **协议背压**：4 passed、0 failed、0 skipped，93.77 秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/backpressure/pytest.log)、[逐例 JUnit](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/backpressure/results.xml)、[清理记录](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/backpressure/result.json)。
- **协议进程生命周期**：8 passed、0 failed、0 skipped，97.10 秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/lifecycle/pytest.log)、[逐例 JUnit](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/lifecycle/results.xml)、[清理记录](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/lifecycle/result.json)。
- **真实 Work 团队**：1 passed、0 failed、0 skipped，21.97 秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/work_team/pytest.log)、[逐例 JUnit](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/work_team/results.xml)、[清理记录](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/work_team/result.json)。
- **自动 Work**：1 passed、0 failed、0 skipped，17.31 秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/automatic_work/pytest.log)、[逐例 JUnit](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/automatic_work/results.xml)、[清理记录](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/automatic_work/result.json)。
- **Dify 模板的持久 Work 适配器**：1 passed、0 failed、0 skipped，17.29 秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/dify_work/pytest.log)、[逐例 JUnit](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/dify_work/results.xml)、[清理记录](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/dify_work/result.json)。
- **固定代码工作流三次审批与 engine 重建**：1 passed、0 failed、0 skipped，68.56 秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/fixed_workflow/pytest.log)、[逐例 JUnit](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/fixed_workflow/results.xml)、[清理记录](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/fixed_workflow/result.json)。
- **真实 Work 语义记忆的已知/未知用量**：2 passed、0 failed、0 skipped，34.11 秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/memory_work/pytest.log)、[逐例 JUnit](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/memory_work/results.xml)、[清理记录](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/memory_work/result.json)。

本次 **18 passed、0 failed、0 skipped、0 error**，没有实际失败需要保留测试库。原21项中另3项（实际worker审批1及stdio/ACP2）不在本角色本节执行范围，根任务另有专项，恰好补足原21项的其余3项；不引用其结果改写原QA的21项skip。根任务另行的仓库2项对应66项历史/运维QA中的2项skip；新增recovery1项是独立新增专项，不属于原21项。各专项执行主体、基线与证据分别记录，不混加或重复计算。

### 8.3 新增的可支持结论及限制

- **RDC-02 团队 / RDC-13共享预算**：实际 Work 环境读取 `/input.txt`，成员观察到真实任务输入，父子共享调用/用量计数、成员两次调用及成功状态成立。证明实际环境和原生委派链路；不证明真实供应商模型质量或团队页面体验。
- **RDC-01 自动任务**：调度产出的 Work 运行在实际环境完成，自动任务 occurrence 记录终态与正确目标，模型用量为受控结果的6 tokens。
- **RDC-03/04 工作流**：Dify模板通过实际持久适配器得到正确输出且0模型调用。固定工作流通过三次独立审批、拒绝错误摘要/编辑替换，在多次 engine/图重建后保持已完成节点，真实代码成果计数文件为单个 `1`，两次受控模型调用和3条成功操作记录不重放。它验证同一测试进程中重建consumer/engine及检查点继续，不是操作系统进程崩溃/重启故障恢复演练。
- **RDC-08/13 记忆**：两例在实际 Work 环境经本机embedding接收器召回，已知用量共享计数准确；缺失用量进入 NEEDS_REVIEW，未继续下一次模型调用。不是供应商向量输出等价性或真实检索效果验证。
- **RDC-10/13 协议资源清理**：实际容器内stdio/ACP的成功/失败/超时/取消均回收后代进程；背压、超大帧与写入超时均按限制结束且不杀无关同级进程。没有放宽真实 schema/readonly/审批校验；这些环境用例不替代独立强类型接口检查。
- 本节没有补齐三类 current 投影的专门HTTP序列化回归、legacy SSE实际call或任何实际UI/第三方联调；原报告这些缺项仍保留。仓库两项及根任务真实恢复/worker/stdio/ACP专项属于另一执行记录，不在本节借用通过数。

### 8.4 namespace回收、测试库删除与计数纠正

每组独立随机 `owner_agent_rdc_indcoding_<组>_<随机>_test`，新迁移生成独立 `work_sandbox_namespace`。读取 manager 时同时限定 purpose=`owner-agent-work` 与精确 namespace；只允许回收本批匹配的ID。七组测试后及cleanup后的manager inventory均为空；直接Docker组原固定测试label下容器也无残留，未删除其他环境。

清理确认后只 DROP 本批限定前缀且以 `_test` 结尾的成功组数据库。补测全部结束后再次通过 postgres 管理库查询这7个精确数据库名，均已不存在；再次按7个精确namespace查询管理器仍全部inventory0。[最终回收及源码核对证据](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-coding/run_4fc59adf8333/post-run-verification.json)保留每个namespace及精确测试库名；没有连接生产业务库，未留下本批执行环境。原设计遇失败保留该测试库与日志，本次没有实际失败。

私有汇总器初版用未锚定的日志正则，错误地把成功kill之后SDK预期“UUID ... failed: not found”中的数字后缀计为failed。JUnit逐例结果与pytest尾部始终为通过、exit0；现以JUnit的failure/error/skipped元素精确重算18例。所有原pytest/JUnit日志未改，原派生JSON保存在各组 `result-before-count-parser-correction.json` 及总 `summary-before-count-parser-correction.json`；修正只涉及计数派生，不修改测试源码、断言或真实失败证据。
