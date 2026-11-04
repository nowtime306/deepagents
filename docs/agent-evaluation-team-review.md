# 团队任务评测：开发与验证记录

2026-11-04，分支 `onwer_agent`。W05/W06 v4 已完成独立代码审查、工程测试和数据库合同验证，并合入开发源码；**尚未通过真实 Work 任务验收，未部署，正式评测入口仍 BLOCKED**。A20 不同案例覆盖仍为 13/20。

## 本次行为

- Work 主智能体和团队成员在模型、工具入口检查同一持久任务的租约、取消状态和原生用量；未知或未结算用量不退款，预算达限后不执行后继工具。Ask 保持原分支。
- 成员通过内部知识库请求时传递团队调用 ID；服务端从数据库重建授权，同时校验冻结配置和当前父团队、子成员知识库绑定。调用方不能提交模型身份替代这些校验。
- 测试目标记录实际成员读取、检索及拒绝写入的审计；仅返回同所有者、同运行的安全投影，不返回提示词、租约或密钥。
- 正式题集升为 v2，只替换 W05/W06 两个新定义，其余 18 题保持原样。旧定义保留在历史提交及拒绝回归 fixture；initializer 摘要同步更新。Gate 驱动只合入已审的 descriptor 预算和 catalog 路径增量，基础设施错误不能升级为 PASS。

W05 保留成员真实读取后超时：两次已知调用合计 30 Token，第三次未知，原任务和成员状态必须待核对。W06 保留先读再检索：运行六次调用合计 16000 Token，初始化向量调用另一次 7 Token；工具入口必须拒绝后续 `/budget-after.txt` 写入。控制检查通过也不能把原始 NEEDS_REVIEW、未知用量或保留资源改写成成功/清理完成。

## 审查与工程验证

私有 v4 冻结 `728c94ad1ab8b7e8c4169c970651a218726e750019645b4a3316340fc8d14d03`，非作者审查收据 `049569e7c7b3bc249a1e24d2d8e258c8bdd38c9b1f80b13fe02dfc21a66713cb`。原先两个能误判通过的反例（逐调用 Token 转移、初始化/运行 call ID 复用）已拒绝；原反例与失败记录保留。

工程测试迁移冻结 `b9f8677dbbfa0578ab280990307695ba449a13ccfa653c2042f1190c3bd2930f`，保留原断言、异常预期和参数化。正式目录两文件 122 项，加现有 profile 文件 9 项，共 131 项普通 active 导入实测通过、零跳过、13.00 秒。原件 `.local/agent-evaluations/team-active-portable131-4d19a11df3294cc18d621a37d7b54585`，JUnit 摘要 `5d3acd3cca158f3c961bf6db134f58476dd5c50ff7e15472486076c2bddb6953`。网络审计记录 56 次标准库认证 self-pair，另一次 bind 被拒；不是零网络尝试或操作系统级全轨迹。DB、SDK、HTTP 替身范围保持，不能算真实任务。

合入后完整客户端 493、profile 221、三份相关 API 单元文件 27 项分别通过，均零跳过。原件分别为 `team-active-client-9adc2ba78a024a25850b4a6740f55fa0`、`team-active-profile-ff3b6170f9544ed98d4fbd6b5cd32277`、`team-active-unit-27a13c8b20b74aaaabb9c90dafa4e87b`，位于 `.local/agent-evaluations/`，源码前后摘要相同。分组存在覆盖交叉，不累加为业务任务数。

首次回归启动漏加测试辅助导入根，另在受限环境发生实际本机连接超时；日志保留。补齐固定包/测试路径、使用明确 API pytest 配置后，原断言不变，在主机环境重新执行通过。首次 131 命令的空 basetemp 参数也在收集前拒绝；原记录保留，修正参数后才产生上述有效 JUnit。没有禁用断言或过滤新警告。

按实际包配置检查新增模块和测试，修正新增代码的导入分组与一行换行；独立 AST 比较确认除 import 外完全相同，团队运行文件完整 AST 相同，原冻结不改。API 新文件和 evaluation 新模块 Ruff 通过。此前真实父绑定 A02 的 47895eda 来源及证据不回填成此新源码的执行结果。

## 真实 PostgreSQL 与内部请求合同

先独立执行私有候选七项真实 PG/ASGI 授权及预算拒绝，再三项整条 observer SQL/JSON 投影、跨账号和命名空间拒绝，均通过。它们插入明确合成的 Work 元数据，不运行 consumer；检索使用本地效果 witness，observer 仅替换 environment proof 以到达实际 SQL，原生用量表为空。因此不证明真实 TCP、环境鉴权、向量服务或 Work 任务用量。

随后将同十项迁移至正式目录，冻结 `c83fc6a48ec89bea35bb6e21119f75f7e33d82bfba7c8b420a4f75ede081cd92`。两份测试全部 32 条 assert、参数化及异常预期保留，生产模块改普通导入，不复制私有生产源码。再建独立随机数据库，普通 API pytest 配置、无 PYTHONPATH 覆盖实测 **10 通过、零跳过**。

正式复验原件 `.local/agent-evaluations/team-active-pg10-d9d18f321ff84e9ca6b977ed6061aab8`。同七加三项的重复验证不累计为二十项。三批各自记录十二张相关表归零、会话零，并只删除该批精确新库；旧失败诊断库、生产数据库、model.key、用户后来修改以及既有服务均未动。

## 仍未完成

真实新候选的 Work 队列、成员运行、沙箱、向量检索、原生账本和逐 CLI 审计仍须逐案执行及非作者事实复算。UNKNOWN/NEEDS_REVIEW 必须保留；正式 cleanup 不会为了删库伪造 CLEAN。全 case 300 秒和全局 600 创建上限不放宽。

F05 新重基包仍在私有审查，F07 完整 receiver 未接线，A05/W07/W08 及 B100/C/D 未完成。Windows 报告 Markdown 原子替换偶发 WinError5 仍 OPEN；已捕获精确失败并验证自有句柄可产生同码，但原拒绝者未知，不能称根因已修。失败后 ready 缺失，门禁不会误绿。

浏览器工具对独立测试站仍受既有拒绝规则限制，未绕过，也没有真实页面验收。生产保持原临时只读恢复部署；本次没有迁移、服务替换或正式发布标签。


## 首次实际 W05：初始化拒绝及最小修复

固定候选 `5ae6303faef4ef128c9d8b1367fa98fc83e8889a` 的一次标准 W05 suite 原件为 `.local/agent-evaluations/w05-5ae6303f-44db6ebc2fc64e279abd414c54d8d7ae`。实际退出 1，INIT_REVIEW/RETAINED，未派发任务。共享 fixture 对同 case/prefix 的父子智能体使用同名，真实 API 第二次创建明确返回 409 AGENT_NAME_CONFLICT；不能将它当预期成员超时控制已通过。

非作者只读收据 `ops-development/w05-init-independent-readonly.json` 摘要 `df8b1535434988a5e4d29cf1f327c5973da8e72a3a34451499c531428b159449`：九张执行/用量/资源表为零、会话零、精确目标 PID 已不存在、本 namespace manager 全分页库存零。三模型（含基线）、一智能体及一发布版本保留在原诊断库；18份证据与三报告封印不变。实际只有 preflight COMPLETE / init ERROR / cleanup COMPLETE 三条审计，没有伪造 run 审计。342份旧源码在修复前核齐；后续新源码变化不回填原来源。

最小修复冻结 `21ac68f6d6313d87191accb5fe87564782d46cff1f6064b7e7fb5e83d9f573cc`：只在生成名称加入已有 model_id，意图键、发布引用、真实 API 唯一约束和已确认请求缓存不变，不使用随机重试。严格按 owner/name 判重的 MockTransport 先重现旧版第二次 POST 409，再验证父子发布、同 owner 重名仍拒绝、不同 owner 同名允许及已确认调用不重发。root 非作者审查和私有三项通过；正式导入新增三项连同完整客户端 **496 项通过、零跳过**，原件 `team-active-client-0d9d444f0a5e4720a57fa91216d63990`。新增测试只整理一处导入空行，完整 AST 不变；Ruff 通过。

旧批预留 10 调用/20000 Token/2 sandbox 不退款、不改 CLEAN；保留原库与原失败。新代码的后续复验须单独固定候选和新环境、继续单 case 停止点，不能重放旧环境。尚未有 W05/W06 业务通过或生产发布。
