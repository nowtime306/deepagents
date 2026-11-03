# R03 网络审计与导出收据独立审查

本轮为非作者代码审查与最小本地反例，不是工程 QA、真实 A02、HTTP 目标、数据库、沙箱或外部模型验收。没有编辑作者冻结源码或测试；R02 事实留存由另一独立角色审查。

## 冻结基线

基线清单 `services/evaluation/.local/fixed-a02-evidence-freeze/manifest.json` SHA256 `84f130989c1054f463c930d38ec8c0bee34bfe9eff2101988faf6d760aadecd7`，10 项文件初次逐项核对吻合。随后作者仅对 R02 三项作修复，叠加清单 `services/evaluation/.local/fixed-a02-evidence-review-fix/manifest.json` SHA256 `789d5b04f350e4c5f27dc30a10e0cd6685ea63126bd73d1941ce3640221c5f85`；本次反例运行使用其新版 fixed_run/fixed_evidence，网络相关 fixed_audit/network/metrics 保持基线字节。

## R03-01 / P1 OPEN：旧 PASS 收据未绑定当前完整审计索引及导出会话

`fixed_audit.validate_ready` 没有使用 `ready.session_id`，也未要求 ready 的审计引用等于当前索引。底层 `validate` 只检查已声明引用仍存在，允许引用为当前索引的任意子集。后续失败 CLI 保留 ERROR 会话，但原 ready/三份报告尚在时，独立消费者仍能接受原 PASS；不能据此安全判定当前清理已闭合或删除目标。

本地探针先使用作者合法的 saved_report 元数据 fixture，经实际 offline report 生成有效收据；独立读取时清空 ACTIVE，避免执行中会话豁免。随后分别复制结果目录，只改收据或追加合法审计元数据，实际调用 validate_ready：

- ready.session_id 换为无关随机 UUID：仍接受 PASS。
- ready.network_audit_refs 设为空列表：仍接受 PASS。
- 保留旧 ready/report，追加新的 SEALED ERROR cleanup 会话：仍接受 PASS。
- 追加新的 PENDING cleanup 会话：正确拒绝，错误为 `An audit session was not sealed`。

这些不是重写全部证据的恶意操作模型；第三项直接对应后续真实清理失败而原收据未失效的正常生命周期。应要求 ready 引用精确闭合当前索引并与报告引用一致，ready.session_id 指向当前最后、身份匹配、完成 execution/export 的 COMPLETE 导出会话。新会话开始、未完成或失败期间不得借旧 PASS/CLEAN。report 会话也需补齐并核对 suite/repeat 等作用域，不能只核环境、owner、target、candidate。

已将缺陷和反例直接交作者并抄送 root，待最小修复冻结后复审。本轮不得标 R03 通过或直接推进真实 A02。

证据：`.local/team-workspace/agent-evaluation/r03-ready-review-probe.py`；输出 `.local/team-workspace/agent-evaluation/r03-ready-review-4fe20f7fa97a42a8be3513cf4bcd8d55/result.json`。该探针只操作自有本地合成结果目录，Platform 被设为拒绝调用，没有执行 HTTP、数据库、实际 DeepEval metric 或业务任务。

## 其余已读边界与限制

会话在命令动作前登记 PENDING，封存使用独立 UUID 文件及文件字节摘要；Journal 的进程锁覆盖命令索引写入。run、report、cleanup 不再直接覆盖此前网络事件文件。run 验证要求 execution/import/metric/export 均有完成阶段，零事件阶段仍显式保存起止、状态及 event_count。DeepEval 入口是 lazy import，import 与实际 measure 分开记录，ControlledJudge 明确拒绝供应商调用并核 judge 调用数。

Python audit hook 在 DeepEval import 之前安装，限制 socket.connect/getaddrinfo 为目标 loopback 端口；目标契约拒绝凭据、URL 路径、query 和 fragment，HTTP client 禁重定向。会话事件只存事件类型、阶段和 allowed 布尔值，不归档 header、query、host 或请求正文。私有证据中的 loopback origin 仅作为策略，不是完整流量抓包。封存后 hook 会拒绝目标端口的后续 socket，导出先 staging、逐文件替换、最后原子写 ready；不宣称三个文件跨文件事务。

以上为源码核查，不等于本轮已独立重跑作者网络测试。即使修复闭合，也只能说明 Python evaluator 可观察 socket 路径；不证明操作系统、原生子进程、平台 worker 或 SSE 全轨迹无外连。作者报告的 97/103 项工程结果不计本角色独立 QA。原首轮 A02 的业务事实缺失与网络覆盖问题仍保留原记录，不回填旧报告。

## R03-01 最小修复复审：CLOSED

修复清单 `services/evaluation/.local/fixed-a02-ready-review-fix/manifest.json` SHA256 `d15d0f028880152a1e17115651eebaf07f0dec54d18e2ea5e605d308c007baf6`，两项文件摘要吻合：fixed_audit.py `592d475415113bf11cc1fac7dd5212ac2fb8726df57df8074761ae233ba793f8`，test_fixed_ready_closure.py `f382fb8220b80c29133627a470425328d6103fc71c86f5da18738bb45b354031`。

读者现要求 ready 引用精确等于当前完整索引及每份 report attempt 引用，导出会话必须是索引最后一项，且 action/status、完成 execution/export、无拒绝 socket、全作用域身份匹配。publish 补齐 suite/repeat 等身份而不覆盖冲突，validate_ready 复用 Journal 锁阻止和活跃 CLI 并发读取；只有真正 PENDING 的当前会话可暂豁免 run 验证，SEALED 不再因 ACTIVE 相同而跳过。

原同一私有探针无断言放宽重放：baseline 仍 PASS，上述四项现全部拒绝。结果为 `.local/team-workspace/agent-evaluation/r03-ready-review-717f6ff754744073ae1cc33c2b57aed4/result.json`；旧复现保留。本结论是缺陷复审关闭，不是 124 项作者测试的独立 QA，也未执行真实业务或网络。工程 QA 仍待 root 明确授权。

## Root 编写的 suite 消费端独审

清单 `.local/team-workspace/agent-evaluation/suite-ready-consumer-freeze.json` SHA256 `0361eaf0ea93e7bb2bb51fbe9d55b7c66fac419898cec1d5609df90b31394287`。两文件逐字节核对：evaluation-suite.py 为 `228a1a427a170c0cee71bcba3d276fbed282f8eb674d76473805376b38139251`，其测试为 `1fb6e8aedfaa21ea445430e6a1773278824b381a6189cd9d4ceee4548cd1f0b7`。

本角色没有编写这次消费端修改，只读核对其增量与调用链：采纳报告 PASS、读取报告 root ID 之前调用 validate_ready，随后用实际 child target manifest 和父 suite-context 绑定 owner/environment/target digest、case/definition/catalog、suite/repeat、candidate；正式 cleanup 和合作 stop 后、target-cleanup 前再次校验。journal 的备用启动/FAIL 路径先执行相同身份绑定，其他环境的 journal 不得提供业务 root 或事实 FAIL。当前自有 journal 的持久事实 FAIL 仍能在 CLI/export 异常时保留。

该两文件增量暂未发现新的阻断项，允许 root 在 R03-01 修复复审关闭后授权工程 QA；不能据此把依赖的 validate_ready 缺陷也标通过。作者 37 pass 不作本角色独立执行结果。本角色尚未运行该两文件测试；独立 target cleanup 入口的收据守卫是另行授权的后续修改，不包含在此结论中。

## 授权后的独立定向工程 QA

R03-01 关闭后，root 明确授权执行精确 network_sessions 与 ready_closure 两文件：**24 passed、0 failed、0 skipped，2.74 秒**。三个叠加冻结清单的 11 个源/测试文件前后摘要全匹配。证据 `.local/agent-evaluations/r03-independent-add6aaf251084d2b95437f32e6ddb0bc/`，含原日志、JUnit、before/after 和 receipt。实际使用自有 loopback socket receiver 以及真实固定版本 DeepEval deterministic metric，judge 调用为零；没有业务 API 目标、PG、沙箱或付费模型。不把原私有四个复审探针混计为这 24 项。

Root 随后补充 suite 读文件时序修复，清单 `.local/team-workspace/agent-evaluation/suite-ready-consumer-read-freeze.json` SHA256 `fdfc98069b361a3bb83ce2056cc6b20d4c74e17bad1000ab75f6703601677855`，源 `3d4d9d96744b825c1b3a2dfd68645abc4c2c8ce04a6ca1677d36000826e38a48`、测试 `af35bf1315e1304dc4ca1a322d40b33a04d87f4acdc5fb4996a0d902a285496e`。只读复审确认 validate_ready 之后对报告只作一次有界字节读取、摘要匹配后解析同一份 UTF-8 bytes，替换后的内容不会被使用。root 授权后独立执行 suite 精确测试文件：**38 passed、0 failed、0 skipped，0.47 秒**，包含前 37 项而非相加。前后叠加冻结文件摘要全匹配，证据 `.local/agent-evaluations/suite-ready-independent-9dce223aec4c4492b4df911eb80bafa7/`。该组无 HTTP、业务目标或 PG。

两组结果均为定向工程 QA，不代表 A02 新一轮真实队列/事实/网络验收已完成，不覆盖独立 target cleanup 守卫新增实现；后者由本角色开发，须另由 root 审查及授权测试，不能作为自己的非作者 QA。
