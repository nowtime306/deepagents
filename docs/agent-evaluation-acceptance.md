# Agent 评测阶段验收记录（开发中）

最新限定覆盖：**11/20个不同案例各1次**，包含既有8例、c71候选A03/A04及d072候选F03；其中W01原FAIL、F03原业务FAILED保留为两项有效负控制，其余九例受控事实PASS。不是20项全部成功、100题/三次重复或真实模型质量验收。W02缺本批原错源码实际对照，暂不计完整验收；F01预约不足的失败现场保留待修复新样本。专项原件及复核见`agent-evaluation-knowledge-review.md`、`agent-evaluation-java-client-review.md`及`agent-evaluation-versioned-workflow-review.md`；以下各历史记录按当时范围保留。

需求：`agent-evaluation-requirements.md`；开发：`agent-evaluation-development.md`；测试依据：`agent-evaluation-test-plan.md`。本篇不宣称全部阶段完成、20个业务任务通过、真实模型质量达标或已上线。

## 已完成的独立验收范围

- 平台隔离环境证明、专用连接注入与只读工作流用量代码：提交 `ae69f50b6`。新环境/用量批次 **32通过、1跳过**（14个用量/证明新增用例均通过，环境18通过/1符号链接权限跳过）；连接另批次 **9通过**（新6＋既有权限3）。批次包含重叠，不相加。使用真实隔离PostgreSQL和HTTP，但台账和Workbench部分是明确标注的受控fixture。
- 独立DeepEval执行器核心：基础提交 `9df446cdf`，严格契约修复 `ca4da393c`。DeepEval4.3.2已在独立Python3.12环境安装并锁定依赖；实际指标、日志、JSON/JUnit/Markdown及受控HTTP适配工程测试最新 **54通过、0跳过**。包含实际Windows `-I`/cp936中文、emoji及坏UTF-8反例、禁止布尔值冒充版本/数量、未知或非法验证参数在提交前拒绝。此前39项批次保留，不累加。该批是工程测试，不是20个Agent任务或付费语义质量评测。
- 核心审查已复现并关闭：缺操作/消息证据误当零、小预算被放大、评分异常覆盖事实失败、Windows UTF-8读取错误、验证参数类型与注册边界。原冻结和失败证据保留，不抹去失败或累加历次通过数。
- A02固定客户端及离线报告身份修复另经非作者独审，R01关闭；最新完整客户端工程 **66通过、0跳过**，包含上面的54项，不能相加。另13项离线身份探针通过。该结果仍使用受控HTTP替身，没有执行真实A02任务。
- 固定受控模型工厂的名称决定成员角色问题已独立复审关闭，改为密封的模型ID/owner/case/role绑定；共享根预算与重建探针通过。后续沙箱上限和完整profile接线仍需单独复审，不用此前单体结果覆盖后续修改。
- A02扩展profile另经独立复审，跨模式上下文污染PF1及embedding拒绝仍启动计量PF2关闭；精确18项工程与2项原缺陷探针共20通过、0跳过。未审case总预算模块移入私有WIP保留，没有混入本次候选；其他case仍由readiness阻断。此批没有真实目标或沙箱，沙箱上限仅工程验证。
- 安装/运行/测试包装器的前一轮运维自验21项为当时工程集调用，不累计到39，也不是业务任务数。目标脚本已在全新测试库完成非Work HTTP启动、401/登录200、环境证明200及A02模型绑定；任务与模型调用数为零。
- 批次/回收脚本经根角色非作者审查，O1～O7已修复；独立工程最新批次27通过、目标守卫4通过。首轮工程夹具10失败保留，修复只使用真实注册的验证器，不放宽产品校验。此处没有启动业务任务或容器，见 `agent-evaluation-operations-review.md`。

详细证据及范围见 `agent-evaluation-core-review.md`、`agent-evaluation-profile-review.md`、`agent-evaluation-qa.md` 与私有 `.local/team-workspace/agent-evaluation/`。仅原管理服务按授权恢复，没有创建第二个同Docker管理器、没有执行生产数据库迁移或部署。

## 正在补齐

1. A20固定profile的正式初始化/独立事实观察/清理：Ask5、Work8、Workflow7共20个不同任务。Java保护验证、受控接收器副作用、审批恢复、未知结果、团队与预算等须真实走正式API及队列，不拿MockTransport或清单条数代替执行。
2. 每个独立重复采用新目标/数据库/账号/数据/命名空间；批次调度器硬预算、source/规则/题集身份、取消与回收须复审后才运行。预检ATTESTED仅表示身份匹配，不代表所有case READY。
3. 故障注入保留真实FAIL/NEEDS_REVIEW及原始结果，不能为清理或汇总把UNKNOWN改KNOWN或重放写入；未知环境不复用。
4. B100任务/平台持久队列/中文页面、C比较CI、D持续评测仍依赖A门禁；未实施、未执行或真实外部前提分别列明，不当作本轮已交付。

## 首个真实A02：已执行，独立事实验收待补证据

候选 `3f1f680f5a43653be81400f50b6b7402adad4060` 已推送。非作者在全新隔离目标，通过正式HTTP和真实AskWorker完成1个不同任务、1次重复、两轮对话；原代码报告gate=PASS，三项事实断言PASS，原生会话聚合2次调用、输入20/输出10 Token，无pending/unknown；实际DeepEval确定性工具约束分1、judge.generate=0，不代表真实模型语义质量。预留预算10次/20000 Token/2沙箱与实际2次/30 Token/0沙箱分开记录。

非作者交叉核验父子报告/账本19项一致，297个来源文件集合和摘要匹配；正式cleanup、合作stop及精确自有测试库删除均退出0，另有只读库不存在/会话0和PID不存在收据。证据 `.local/agent-evaluations/suite-a02-first-76d819ab/`，完成收据摘要 `96d180a74db7d85c37d818bfe29681aa1f2325fcc530d69e825fbd722db422d6`。

**独立验收仍为NEEDS_REVIEW。** R02：未留存可重算的正式消息、两根终态和上下文观察快照，原库已删除，只有断言结果不能独立复算业务事实。R03：每次CLI覆盖network_events，末尾只剩cleanup进程记录，缺全程评分/导出审计。不得事后伪造响应或将本次称为完整AE02/AE11验收。开发正补最小脱敏事实快照、摘要引用与逐CLI审计；缺证据/篡改不能PASS或CLEAN。修复独审、工程测试关闭后，另建全新环境重跑；原报告与失败门禁记录保留。

后续修复已完成代码独审及工程QA：R02客户端三处读取/缺证据分类/旧评分一致性问题关闭，R03最后收据与完整审计索引闭合问题关闭；最新完整客户端124通过、0跳过（含旧66等，不累加），另网络/ready定向24通过。批次消费端38通过，核当前目标、case/repeat和同份报告字节，裸PASS或他环境收据不能计作本次成功或允许删库。独立回收入口新增业务历史必需收据检查，API环境12项、独立reader10项通过；不引入API的DeepEval依赖。这些都是工程结果，仍需全新真实A02复验，旧执行验收结论不改。

## 新候选533417e：A02限定验收通过

全新 `.local/agent-evaluations/suite-a02-evidence-533417e-6e8e4b3c/` 已完成1个A02任务、1次独立重复。非作者实际执行，另一角色对同一次执行做31项只读独立核验：两输入摘要、最终星桥/3 JSON、两根SUCCEEDED、上下文摘要和3项事实可从封存字节重算；原生2次调用/输入20/输出10 Token，provider账本吻合；实际DeepEval确定性工具约束分1、judge.generate=0。四次CLI审计SEALED且索引闭合，run四阶段及0连接阶段可核；父子JSON/JUnit/中文报告及最后收据摘要一致。

CLEAN、合作stop和只读reader/精确删库均成功；库不存在/会话0、PID退出和同环境终止收据齐全。冻结期间301个来源文件匹配，原证据未改。完成收据摘要 `e6a1c00882de16214513893b68e089ea58abeb956a2b0f439770323d00cf9181`；独立核验摘要 `581751078dac857c2861e3e7dbd7cd4449a1aa139d7fbff6b3104b7de78db692`，详细范围见 `agent-evaluation-qa.md`。

**本次通过范围是A02受控工程事实、Python评测进程审计及回收。** 不包含真实外部模型语义质量、OS/worker出站、完整SSE、Work沙箱、实际UI或整批A20；首次3f1f680的历史NEEDS_REVIEW不被回填。本轮A阶段实际任务验收为1/20；W01、F02、F04正在开发接线/审查，其他阶段继续依照既定门禁推进。

## 保留的门禁

下一批 W01/F02/F04 已完成开发及独立代码审查，清理与 namespace 三项审查问题修复后关闭。完整客户端独立工程测试 204 通过、0 跳过（包含此前 124、Work 39 及新增修复，不累加）；profile 独立 29 项通过，另以真实 PostgreSQL/HTTP 验证 1 个观察投影场景、230 条断言。投影场景没有运行 Agent 业务，测试库均按精确身份回收。能力清单仅放行这三个新增固定处理器，实际任务数仍为 A02 的 1/20，等待固定候选后的真实三例。详见 `agent-evaluation-workflow-review.md`、`agent-evaluation-profile-review.md` 和 `agent-evaluation-qa.md`。

不付费、不登录/上传Confident AI，真实外部模型效果仍未验证。Python socket约束不是OS级完全离线保证。生产业务、凭据和既有数据保留；实际页面访问的最近明确拒绝仍有效，界面验收未执行，不换通道获取被拒页面。没有新正式标签或本批生产发布。

## 候选 f8ca28d：三例真实工程验收

源码 `f8ca28d2e3ec4dff7952e8a2f4ce6805514c9bdd` 已正常推送。全新批次 `.local/agent-evaluations/suite-three-f8ca28d-8f61b2c9/` 运行 W01/F02/F04 各一次，每例独立库、账号、目录和 namespace。

- W01 正式 Work worker/Deep Agents/Coding 执行后为 COMPLETED，但最新完整 snapshot 没有 `/report.txt`；独立事实正确判 FAIL，原 task/gate FAIL 保留。工程负控制验收通过不改写任务结果。原生调用 1 次，输入 10/输出 5 Token，provider 一致。
- F02 正式已发布只读流程通过；初始化和实际执行的节点输入输出、转换、条件分支及版本摘要均可重算。F04 先取得非法类型输入的 422 与零副作用证据，再运行同一发布版合法对照；两例 PASS，原生六项用量计数均为已知 0。此处是正式 HTTP 同步流程，不称为队列或 LLM 节点验证。
- 父门禁保持 FAIL、退出 1；三份父子报告与最后收据一致。12 次 CLI 审计均 SEALED/COMPLETE；这些无指标用例只有 execution/export 阶段，不伪造 DeepEval 评分。冻结的 305 项来源摘要一致。
- Work 实际沙箱 `ed1cd1df-1567-432c-bfd8-ece2225fc576` 与本次 namespace/session/run 关联；镜像 ID `sha256:459420e199a24347bed6091ca0683cc570e7fd4c3737f086c3b43533c89c7ce9`、4 CPU/5 GiB。管理器观察 0→1→0、Docker 创建/启动/退出/销毁及封存的 RELEASED:1 相互吻合；未建立第二管理器。
- 三例 CLEAN、合作 stop、只读 reader 和精确删库均退出 0。三个库不存在且会话 0、PID 已退出、同环境 graceful 收据及各 namespace 的管理器/Docker/侧车清单 0。未知状态没有强制清除。

执行者封存后，另一角色实际只读核验同次事实/审计/生命周期及回收，没有重跑业务或重导出报告。完成收据 SHA256 `f048f511dab3397cafcd7181fe3958e1602b6c56eb26f25ec4e3ac2df7b44e13`；独立结果 `services/evaluation/.local/three-case-independent-review/run_6e004cba171e/verification/verification.json` SHA256 `65517a5c5c3977bc4c5c2e93c71595fe45ff57fed01f06d93ab4382586684ad4`。

目前 A20 有 **4 个不同案例完成限定工程验收、各 1 次实际重复**：A02/F02/F04 的任务结果 PASS，W01 的预期负控制正确检出原任务 FAIL。不是 4 个任务全部成功，不含真实模型质量、界面、完整 SSE、OS/worker 出网或生产发布。其他 16 个案例与 B/C/D 继续按原门槛开发；旧 A02 缺证据记录保持原结论。

下一批基础另已独审/工程 QA：审批/消费者客户端指定三文件 67 通过（包含旧 53）；原生空用量聚合指定 API 单元文件 6 通过，明确空查询为 0、已有未知调用仍为 null。两组不是实际 A20 任务，不相加；消费者目标控制器、Java 保护及完整共享链路仍待各自审查和实际验收。补齐需求见 `agent-evaluation-remaining-requirements.md`。

A01 与 W03/W04 共享链路随后完成独审修复及独立客户端 310 项/API profile 69 项工程 QA，范围见 `agent-evaluation-a01-shared-review.md`。Java 验证器另有独立 47 项工程测试和真实 Coding 的五组固定源码保护探针通过，收据、两次私有脚本失败和范围见 `agent-evaluation-java-review.md`；不是 W02 正式任务，尚未接入 READY。上述进展仍不增加已实际验收的 4/20；A01/W03/W04 等固定候选后的真实执行和独立复算。

候选4627实际A01在正确创建暂停草稿后被客户端引用修订类型错误阻断，未启用/调度，其他两例未派发；已停止实例、保留隔离诊断库，不称CLEAN。修复后独立客户端328项及真实PG/API投影契约1场景通过（旧310/69不混加），准备新候选/全新环境复验。原失败报告和旧ready拒绝不改写，详见共享审查记录。

## 候选90dbe95：自动任务与审批恢复限定验收

修复提交 `90dbe95f1d52959d0a077d3a0e03241f814f687b` 已正常推送。新批次 `.local/agent-evaluations/suite-a01-w03-w04-90dbe95-613af8e2/` 在三个全新隔离目标实际运行 A01/W03/W04 各一次，均 PASS；执行者封存后由另一角色只读重算同次证据，不重新运行任务或导出报告。

- A01：正式 Ask 工具创建 disabled 草稿，启用前发生次数为0；显式启用后真实 scheduler/AskWorker 只执行一次。源/发生两根均实际调用维护后的模型，快照引用 TEXT `"2"` 与原生整数修订2分别严格核对。共4次 KNOWN 调用，输入40/输出20 Token，与跨根 provider 账本一致；没有 Work 或沙箱。
- W03：正式 Work 上传修订2，审批前计数0/操作0；拒绝后同根恢复，最终计数0/操作0。W04：WAITING 时合作重建消费者，保留原根与同一 checkpoint，批准后计数1/成功执行回执1；显式重复原已确认决定没有新增执行。两例各2次 KNOWN 调用、输入20/输出10 Token，各两个真实 Coding 会话均 RELEASED。消费者对象重建不称为 OS 进程重启。
- 12次 CLI 审计 SEALED/COMPLETE，最后收据覆盖当前完整审计索引；原始事实、JSON/JUnit/Markdown、父子报告及门禁一致。无指标用例仅 execution/export，不伪造 DeepEval 评分阶段。315项源码集合及候选 HEAD 在冻结期间匹配。
- 三例正式 CLEAN、合作 stop、只读 reader/精确自有库回收均成功；三个新库不存在/会话0、三个 PID 已退出、同环境 graceful、每个 namespace 及四个 sandbox/egress 侧车清单为0。旧4627诊断库仍按原状态保留，不能称它已 CLEAN 或用新收据回填旧失败。
- **留证限制**：W03 Docker 历史事件在批次末读取时缺失，未补造 create/start/destroy；其实际管理器连续0→1→0→1→0、两个精确容器 inspect/镜像4 CPU/5 GiB/namespace/session/root、原生 RELEASED:2 与末次库存0相互吻合，支持限定的资源归属与回收验收。W04两个容器的历史事件完整。此限制保持可见，不靠重跑覆盖。

完成收据 SHA256 `580acfc43662d78259cab43ab3647fc1b9a58fabb1538d5bed458bd5e237b49a`；独立完成收据 `4c84a9929dd43ad535226c3e64be6ebcfb199163ab20e83a25c9689f695b50cd`，详见 QA 记录。现在有 **7/20 个不同案例完成限定工程验收，各1次独立重复**：其中6个任务 PASS，W01原任务 FAIL 被正确检出。真实外部模型效果、完整轨迹、OS/worker出网、页面和生产部署仍未验收。其余13个案例及B/C/D继续按原门禁开发；W02保护工程验证不计作正式任务通过。

F06 后续实现另已独审关闭两项留证 P2；独立客户端353项、profile71项及真实PG/API共享节点预算契约1项通过，详细分组、原失败和范围见 `agent-evaluation-parallel-review.md`。这只是下一固定候选的工程门禁，正式 F06 样本尚待执行，不能提前记作8/20。

## 候选8a15c8d：F06正式样本限定验收

提交 `8a15c8d78f022e191f53fbd6e96a1e9bb065d4dc` 已推送；新批次 `.local/agent-evaluations/suite-f06-8a15c8d-9df3c2e4/` 的 F06/一次重复 PASS。三次初始化与一次已发布运行的原事实、lower/upper/lower固定版本、三条分支和60条节点事件、四根显式原生零用量及owner/provider零副作用，均经非作者只读独立重算。四次CLI当前审计索引/ready/三种报告一致，316项来源冻结匹配；新库、PID及namespace精确回收。执行完成收据 `575f1f9d83fa2b88602660a52a2e9b75179265eee2e3d15703bbfe006be81a33`，独立完成收据 `9600ef8f9fae51cd10c652e7219ab45914590138c480622a7e9fab6bd92ac289`，详细边界见并行专项审查。

目前 **8/20 个不同案例完成限定工程验收，各1次独立重复**：7个任务 PASS，W01 缺文件负控制正确保留原任务 FAIL。其余12个案例与B/C/D未完成；实际供应商效果和页面仍未验证，没有正式部署或新标签。F06是同步Workflow HTTP，不称为队列运行；PG预算工程样本不另算案例，保守沙箱预约不是实际用量，旧4627诊断现场不被回填。
