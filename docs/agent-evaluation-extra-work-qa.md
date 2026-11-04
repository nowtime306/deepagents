# Work 专项新增测试角色启动与交接

日期：2026-11-04。角色：`expert-evaluation-work-tester`。本轮仅做只读准备、封存证据摘要复核和测试交接，不启动业务、数据库、沙箱或模型，不修改实现与公共验收计数。

## 基线和阅读范围

开始及结束读取的 HEAD 均为 `71780807df30f95a5c6eb7f6dc6ad5b7b60e5018`，分支 `onwer_agent`。起始工作区已有未跟踪 `docs/deepeval-learning/` 和 `docs/system-architecture-2026-11-04.md`，均未改动。

已读专项角色定义、专家测试规范、DeepEval 公共知识和测试岗位笔记；并读取 AE 需求、当前检查点、剩余需求、团队专项审查记录。源码定向阅读 `fixed_run.py` 的 implemented/readiness 分派、`fixed_team_evidence.py` 的定义/初始化/readiness/事实断言、`team-definitions-v2.json` 和受控模型中的 W07/W08 分支。

原会话正在执行新一轮 suite，本角色不重复派发。以下只是本轮读取时的状态，不能覆盖运行中批次的后续结论。

## 场景准备状态

### W05 团队成员失败传播

- **候选已有**：团队 v4 审查及工程/PG合同已完成，题集 v2 已合入。旧候选 `5ae6303f` 初始化因父子智能体同名被正式 API 拒绝；名称修复已经独审，当前 HEAD 含该修复。
- **可以立即并行做的事**：等待原执行者封存新批次后，只读核对同一批次原件、实际队列/成员状态、原生用量、成员读取记录、审计、报告及资源处置。无需再开一批重复执行。
- **必需事实**：父 Work 为 NEEDS_REVIEW，成员 UNKNOWN/TEAM_RESULT_UNKNOWN；先有真实成员读取，再有超时；两次已知调用共输入20/输出10 Token，第三次未知；不能继续主助手调用或额外写入。角色、模型、root/team_call/资源快照必须关联。
- **目前未获得的新证据**：名称修复候选实际 Work 封存结果。本轮不宣称 W05 业务通过。受控失败被正确识别可作为控制验收结论，但原任务 NEEDS_REVIEW/未知用量不能改写成任务成功。

### W06 共享预算达限

- **候选已有**：团队 v4、成员知识请求上下文、原生预算守卫及观察器均有审查记录；`fixed_run.py` 已注册 W06，readiness 仍须逐目标实证。
- **必需事实**：先读 `/input.txt`，再有成员查询 embedding；运行内六次已知调用共16000 Token，初始化索引另一次7 Token，整例七次/16007。初始化与运行 call ID 不复用，父子用量不转移、不重复累计。
- **预算停止证据**：到达硬额度后 `/budget-after.txt` 无写入，拒绝事件为 WORK_BUDGET_EXCEEDED，拒绝前 provider_calls=6/tokens=16000；保留真实检索来源、节点/工具顺序和账本。预期原任务为 NEEDS_REVIEW，不伪称目标业务顺利完成。
- **待交接**：独立新目标、固定候选/定义摘要、初始化基线、实际队列/沙箱/向量调用和逐 CLI 审计。现有131工程项及PG合同不能替代这些证据。

### W07 结果未知恢复

- **当前 BLOCKED**：当前 `fixed_run.IMPLEMENTED` 和 `evidence_module` 无 W07 正式处理器。受控模型虽有 `ae_write_record` 分支，不等于独立接收器、观察器、恢复及清理完整实现。
- **需要的实现与验收交接**：受控接收器确已写入且回执丢失；同 root 重建/查询恢复；持久意图、接收器计数、UNKNOWN和原生用量可重算；不得再写或换根重跑。不因清理需求把UNKNOWN改成KNOWN。
- **本角色可提前准备**：写入前失败、写入后丢回执、恢复查询、重复投递的断言清单；等完整候选审查通过再执行。此轮没有发现可直接转成正式验收的已封存 W07 样本。

### W08 文件边界

- **当前 BLOCKED**：当前 `fixed_run.IMPLEMENTED` 和分派无 W08 正式证据处理器；受控模型目前展示合法读取、`../` 路径和结果写入，不证明软链与导出边界已经覆盖。
- **缺失覆盖**：需求还要求真实软链、授权根外合成 canary、合法成果精确 SAFE_INPUT，以及最终导出不跟随软链、不包含 canary。无法创建/观察链接须如实记缺覆盖。
- **执行前提**：需已审完整候选及明确测试边界，并尊重旧风险检测中断。当前不通过新脚本、其他通道或换身份绕过原拒绝，不读取真实宿主密钥。

## 本轮独立只读核对结果

1. 读取旧 W05 非作者收据 `.local/team-workspace/agent-evaluation/ops-development/w05-init-independent-readonly.json`，其 SHA-256 为 `df8b1535434988a5e4d29cf1f327c5973da8e72a3a34451499c531428b159449`。
2. 对该收据登记的18份原文件逐项重新计算摘要，均与收据一致；没有修改、重新导出或运行旧批次。原批次为 INIT_REVIEW/NEEDS_REVIEW，business_started=false、dispatch_attempted=false，不能计为成员超时测试通过。
3. 读取名称修复后的顶层客户端工程 JUnit：`.local/agent-evaluations/team-active-client-0d9d444f0a5e4720a57fa91216d63990/results.xml`。记录496 tests、0 failures、0 errors、0 skipped，文件 SHA-256 为 `41e73bf36cbfdee9a27ffdd75c32cb8b191f6db16249f9aa370327d9fbcbbf86`。本角色仅核读现有结果，没有重跑这496项。
4. 初次读取脚本误递归遍历工程测试生成的嵌套 XML fixture，遇到刻意损坏 XML 而解析失败。改为只读已声明的顶层 JUnit 后完成核对；此读取范围错误不作为产品缺陷，也不累加嵌套 fixture 计数。

## 首批优先顺序和 Ready 条件

1. **优先W05新批次独立复算**：原会话运行完成且原件封存后接手，可与执行者后续工作并行；未封存前不读取变化中的文件给最终结论。
2. **随后W06**：确认与原执行者不重叠；工程候选已审、实际目标attestation、团队/原生守卫/成员资源桥及case预算证明齐全，且全新目标无pending/unknown旧占用，才安排实际执行。
3. **W07/W08先补实现交接**：只有登记case或受控脚本不满足Ready。完整init/observe/cleanup与验证器、反例、预算和审查收据到位后再派发；被拒绝的路径不通过额外测试身份规避。

每次接手需要：候选 SHA 和源清单、用例/规则/素材摘要、审查关闭收据、执行者与独立复核者、精确环境/资源范围、预算与停止规则、原件目录和封存摘要。运行后需完整事实/审计/用量/配置/报告及保留或回收收据；不存在原件时结论为证据不足，不补造。

## 可独立指出的验收缺口与协作边界

- 当前 W05 的工程通过不能填补初始化后尚未执行的成员失败链；新批必须观察实际成员读取和未知传播。
- W06 必须将初始化与运行计量分开，且整例合并；只对模型脚本或SQL投影单测不能证明沙箱/向量真实链。
- W07/W08 在目标模型分支出现但没有正式证据处理器，此状态是已知未完成，不新增为未经复现的产品Bug。
- 工作区未发生新的已确认业务缺陷；本轮无实际业务执行。公共覆盖仍按原检查点13/20，不由此报告修改。
- 后续只写本角色分配的测试文件或本报告。原测试保留公共链路与总验收；本角色可在封存后承担独立复算，减少原执行者等待，但不重复抢占沙箱。


## 新增实际子Agent首批交付：717 W05 封存复算（2026-11-04）

角色 `expert_evaluation_work_tester` 已实际创建并执行首项任务。原启动记录保留。本批342项源码和20份显式原件前后摘要同；0网络/进程尝试，未连接DB/manager或重跑业务。

ready三报告摘要匹配，但仅2个审计引用，最终index已有preflight/init COMPLETE、run/cleanup ERROR共4条；实际只读生产reader精确拒绝当前ready。journal.started=true且原根已受理，report仍旧init快照started=false/NOT_CHECKED；缺team-facts，直接验证journal也被Blocked。当前capacity真/假与伪fact PASS三个内存反例均仍拒绝。此为离线检查，不新增业务案例，W05不是成员UNKNOWN控制验收通过。

运维现场收据 `ec9fe38283fde5e7b6ce0586ddae940139a0863d61df2abd82b8515762fa8d7f` 摘要已复核，现场DB/PID/manager查询仍由运维承担。容量根因未证，不把现在probe可用当历史可用；等待开发补出当时拒绝类别、审查新候选，当前不授权重试原根或扩大预算。W06未执行，W07/W08仍缺正式处理器/边界交接；W08原风险拒绝不绕过。

完整报告：`.local/team-workspace/agent-evaluation/qa-work-extra/review_717_64ee72ff82b44576a0029498b2c63ace/findings.md`；机器收据同目录 `review.json`，SHA `8b5ad91de0bdb1a0abed1d57c3dba0a148bcd30ed5fdc78cccafd26d9d766e9c`。实际来源/反例/逐案Ready门禁及未验证范围详列其中。公共覆盖仍13/20，新增业务通过0。


## 容量拒绝调查：未证历史根因

Work专项另完成新target-shape只读探测，目录ACL/私有cwd/HOME/TEMP等匹配，两次策略预算均约6.92GB>=coding5Gi；没有加载原API内存压力或重建旧任务，不能证明历史可用。8项纯替身控制确认WORK_CAPACITY_WAIT可能来自预算、已有预留、lease查询/INSERT或UniqueViolation；探测错误/解析失败也会变成budget0。因此不先认定内存不足或Docker上下文丢失，不修改准入逻辑。

证据/建议在`.local/team-workspace/agent-evaluation/qa-work-extra/capacity_21dbf015f06e409580a86c3cacde8133/findings.md`与summary.json。真实probe SHA4a06418155e6a735f31b4aca50b41303c5bc156aab1fc9afe96898d081ae5baf；主机纯控制收据28017d817ed101380db46938f8ecd04520fafa4e5db8447bf2e9ae5dadc3ada3（原脚本字节相同）。受限首次纯助手asyncio等待未完成、已向主Agent登记60420/35624，不冒充已清理或产品缺陷。未启动DB/Task/Sandbox，新增业务通过0。

## 容量诊断候选：独立审查及工程复测

固定 manifest `393757def126dfe81dacf7ad1543eb9b4c09499836ded4c07a36438c3072f10f`，逐项核对资产后复制到新私有目录。独立审查两模块未发现诊断增量的阻断问题；原公式/5秒cache/lease/UniqueViolation和单次SDK未知策略保留，ContextVar回传同次采样，cache原时点不改写。事件在准入事务退出后，从确切DB run派生owner，同一连接原子分配event序号并插入；真实PG并发/权限仍待独立库验证。

原17断言副本实际通过；再加强私有harness，记录拒绝网络/进程事件并令其失败，同17仍通过。新增5条非作者替身反例检验事务先退出、诊断失败回滚、缺run不造owner和缓存不可被调用者改写，最终22 passed/0skip/0error/native0，网络拒绝0（只允许标准库asyncio自管socketpair）。重复17不累计，22工程检查不是22业务任务。候选原资产与active两模块保持未变，新增业务通过0，公共仍13/20，历史根因UNPROVEN。

报告：`.local/team-workspace/agent-evaluation/qa-work-extra/diagnostics_review_53bb3ab18c2246f8ba086f2c87f5f405/findings.md`；机器收据`review-summary.json` SHA `e13822ec44d835bdffe9361790e3e1e1a1bb6ba5152e53dc826f3b44f7026d08`。本轮没有DB、模型、沙箱、target或Docker真实调用。

前批受限助手后记：主Agent精确核60420/35624均ABSENT，无需终止；本角色再次收集session22413时工具已Unknown process id，末尾结果不可恢复，该次助手不计通过或产品QA。

## 359 容量诊断真实 PG 合同：8断言绿，外层审计门禁失败

独立核359cae05包和393父候选后，在全新`owner_agent_capacity_qa_b4fb7e9687e04f6c9b671d5807a7a632_test`执行8合同，真实PG/ASGI断言8passed/0skip/0error/2.133s，包括20并发序号、DB owner/foreign-admin404、原生PK冲突回滚仍None、原生lease/唯一索引拒绝。原断言/两模块未修改，all_assets/source-pins前后一致，没有worker/SDK/manager/模型/业务执行。

严格wrapper记录1次被拦截socket.bind尝试，guard在系统调用前拒绝；child exit3，因此整体仍FAIL_AUDIT_GATE_NEW_DB_RETAINED。原审计无调用栈，来源未证，不能由8绿覆盖外层门禁，也不称成功外连/监听。原h已成功清本批精确rows；额外只读核work/tasks/runs/events/session、usage/operations/team/users/auth_sessions全0，库连接收尾0，库仍保留，未重跑或drop。

证据目录`.local/team-workspace/agent-evaluation/qa-work-extra/capacity_pg_8c600f47c1c347bd8ee2e2e4e33a9aa4`，详见findings.md/receipt.json/results.xml/network.json/retained-readonly.json。已准备未执行stack-only wrapper候选，维持网络约束和拒绝退出，必须经主Agent审查后复制至新唯一批次执行，禁止覆盖本次原件。新增业务通过0；历史原因UNPROVEN。

### 后续已授权stack新批定位

主Agent批准785a原wrapper后，在新目录`capacity_pg_stack_b5159ecfccd04bc1b98f6c8eb1a493d7`及全新测试库执行。仍8passed/0skip/0error/2.10s、外层exit3，整体失败保持。真实拒绝栈为urllib3.util.connection._has_ipv6:127，module137导入时调用；只读核dependency `2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74`，它尝试AF_INET6绑定::1随机端口探测能力，无listen/connect，guard拒绝后由库except吞掉。这一新批证据证明依赖导入probe造成审计失败，旧首次无stack不倒填。

新库`owner_agent_capacity_qa_5bcdde8bbe4c43e2afb6ca841dadc8fe_test`保留，原h清本批精确rows后独立只读核业务/用量/账号行全0、连接收尾0。未改白名单、断言或原资产；sourcepins/资产结束后同。原件/完整说明在该新目录，independent-summary.json SHA `2e04f4c2e0b20336ea0d8e042f766bf3c7852ec8bafb0afe1e5c6f08f4516106`。后续只提出严格guard下预导入并保留预期probe拒绝的环境适配建议，由主Agent审查，未自行编写或执行。业务通过0，历史容量根因仍UNPROVEN。

### 最终独立PG工程门禁：通过，不计业务

经主Agent审查fc8f wrapper/c7db清单并明确批准后，在全新目录`capacity_pg_import_00c06a78b5e04c17ab4ee4c79c11db3c`执行原8合同。结果8passed/0skip/0error/0fail/2.22s、child0/wrapper0。精确urllib3导入probe实际1次且始终阻断，expected单独记录；正式TEST阶段unexpected0。真实PG并发序号、PK冲突事务回滚仍None、owner200/foreign-admin404及原4拒绝来源合同均通过。未改断言/候选/准入预算；9sourcepins、全部候选资产和2633依赖源码前后同。

本次独立库`owner_agent_capacity_qa_61f2f051f41245c698427dae129725d0_test`成功后精确rows全0、用量/操作/team/账号0、连接收尾0，正常drop并确认不存在。两批失败库/原件仍保留，不回填它们结论。完整报告见本新目录findings.md；receipt SHA `7dacb6b62f66bc40f2cacc9858a07b4af952365194fe39212ca08014d05bf19a`，network SHA `ad950d95a1efca1e6cf78b215ba0d0d2ee41c405a66be561d81efff50d4a6f97`。仅诊断增量可交主Agent合并门禁，非W05/W06业务通过，新增业务0/公共13/20保持，历史容量根因UNPROVEN。

### 正式普通导入门禁：17unit+8PG通过

主Agent合入393两产品（UTC/折行）及portable正式两测试后，本角色独立重算AST：产品仅UTC归一化后与393完整AST相同；unit完整AST同393；PG除private migration wrapper外每函数同359，migration=原fixture别名。API实际cwd四文件Ruff通过。

新`capacity_active_ec7a599d74d94499b3da65c7e7e0ee06`普通pytest正式路径执行25passed/0skip/0error/0fail/2.50s、child0/外层0；ordinary-imports.json确认两产品来自正式API文件，无私有conftest/实现副本。14相关pins前后同。exact IPv6 import probe1且实际阻断，TEST阶段unexpected0。新库`owner_agent_capacity_qa_c210206c135a476d9f2517db187e2cb9_test`row/usage/operation/team/账号0、session0后已正常drop确认不存在，旧失败库不变。首次包装器残留旧目录前缀在建库前停止，修正为同等强度的新目录保护后才执行，断言未改；细节如实记入findings。

receipt SHA `22f34dbde38c6f4c20e6b2bf56cbce292f3a38a01e275369872211a332319d4c`；最终wrapper SHA `d500ae1c016c4ea9ca1ea7ed0ae3d381d673eef6829d45c030f265e2f1f61e46`。本轮仅诊断增量工程门禁闭合，可交主Agent固定提交；业务仍13/20，W05/W06新增业务0、历史容量原因UNPROVEN。
