# 首期受控评测 CLI 正式源码集成计划

日期：2026-11-05；状态：R4独审通过并完成正式源码集成，完整CLI复验与正式发布待验。

## 交付边界

将已验证候选作为版本化源码交付，不能将私有脚本或工程测试数称为正式上线。沿用首期 CLI、独立测试实例、固定 suite/target、Journal、父预算、网络审计与证据导出；不开放平台 UI v2 执行、B100、外部裁判或任意模块加载。F07 v2限定场景已实际验收，但仍须完成正式源码集成后的CLI复测，不能随代码文件存在而授予平台执行准入。

原 a20-fixed.json、controlled-a20-v1 和旧失败记录保持。默认 profile-version=1，原 A05/W07/W08/F07 v1 未实现声明不变。新增显式 --profile-version {1,2}，沿 suite→target→fixed_run 持久传递，v2 使用独立 catalog、rules_id、逐定义摘要和固定四 case 注册表；未指定时不替换成 v2。禁止跨版本复用旧 Journal、套用旧预算或返还已消耗额度。

## 已审来源与文件映射

- A05 v2 R2：manifest d5271bbc8835d04a98f35fa3639ece6eb92b29653598e4ebdd07b0a6bb8c3259。a05_handler、a05_protocol→evaluation_runner/profiles_v2/a05；evaluation_a05、a05_receiver→profile_support/profiles_v2/a05。现有 evaluation_target 仅合并 A05 开关/实例限额/真实 Worker 工厂/封存接线，不覆盖整个旧候选文件。AskWorker 的 mcp_client_factory 可选实例注入单独最小 diff，默认行为不变，补受影响 Ask 验证。
- W07 v2 R3：manifest ce506f8ef7a361c7504f6b84c91ba18b837d53e3ec91bf18b192bc577dec79f7。w07_handler→evaluation_runner/profiles_v2/w07；model/service/receiver/recovery/entry→profile_support/profiles_v2/w07。保留真实批准、UNKNOWN/NEEDS_REVIEW、同根只读恢复、原生 HTTP 工厂、真实空串清凭据修复。
- W08 v2 R3：manifest 6aff455cbaf5762a2d46098bba4f00acf52418d89fd01c43f595ccaea6f7a9b0。handler/validation/contract→evaluation_runner/profiles_v2/w08；model/service/checkpoint/fixture_program/entry→profile_support/profiles_v2/w08。保留固定物理素材、最后 effect 截止门、真实 latest Delta 重建与只读连接、HTTP 最新成果下载校验。
- F07 v2 R5：manifest feb45e92307bdda8467800f091cb12de0cf3d479606b586e5bf8a6c1960f1d85，限定真实场景已验收。handler/contract→evaluation_runner/profiles_v2/f07；target→profile_support/profiles_v2/f07。五个产品 overlay 为 workflow_api、workflow_compiler、workflow_definition、workflow_pure_timeout、workflow_runtime；按审定最小 diff 合入，不能复制整个 app 目录或放宽租约/超时/usage UNKNOWN。
- 四份独立定义置于 evaluation_runner/profiles_v2/definitions，随 wheel 打包；测试分别进入 evaluation/tests 与 profile_support/tests 的 v2 专项目录，不复制私人连接、运维 runtime、密钥、失败数据库或绝对盘符到 canonical 包。

逐文件源 SHA、目标 SHA、确定性 import 调整和未合并项保存在私有 stage manifest；旧实际证据与候选来源另存 provenance，不作为生产运行时路径。纯契约放在 runner 包，API 测试启动器显式加入已有 evaluation/src；包导入不安装或启动 DeepEval。

## 必须接通的正式调用链

1. fixed_run：默认旧路径；显式 v2 才选择四 profile、定义与处理器。describe/readiness/init/run/cleanup/report 均校完整版本身份。复用既有 Journal、network/fixed_audit 和 Result，不另造编排器。
2. 私有处理器尚不完全符合现有 evidence_module 的 verified/evaluate/cleanup_ready 接口。补薄适配层，离线重读 binding/原始证据重算事实；不能仅把 task_outcome=PASS 导出。W07 必须区别防重放控制通过与业务写入仍 NEEDS_REVIEW；UNKNOWN 不消除。清理仅对白名单完整语义转为 CLEAN，不接受任意 CLEAN 前缀。
3. evaluation-suite.py：describe、子命令、版本化输入/摘要与预算描述一致；仍串行且最多一个活目标。v2 各 case 单独实例，实际根数/provider 上限与正式 API declared 预算分别保留。
4. evaluation-target.py/ps1：版本与 case 固定工厂选择、受管进程身份与停止/回收继续沿现有机制；不接受用户模块路径。suite-capabilities 仅在完整差异审查后更新 initializer/hash。target manifest 绑定版本和受测定义，不改生产 3131。
5. platform_driver：当前只接受原 v1 注册集；显式拒绝 v2，避免 UI/API 请求绕过首期 CLI 准入。后续平台接入另验，不在此次顺带开启。

## 配额与事实语义

- A05：5 calls/75 Token、2 Ask roots、120 秒、0 sandbox。
- W07：1 root、provider 1 call/15、正式 declared 5/1000、300 秒、最多2串行 Coding；审批真实消费，UNKNOWN 不重放。
- W08：1 root、provider 6 calls/90、正式 declared 6/1000、300 秒、1 Coding；同一初始化 anchor 不延长。
- F07：4 roots、provider 1 call/15 上界、120 秒、0 sandbox；真实 usage UNKNOWN 不能伪报已知0，发布测试根与companion均计入。

所有实际失败预算继续消耗，复验必须另分配批次。首期报告分别展示工程检查数、独立业务案例和待验项；没有裁判/真实模型效果验证时明确标注受控工程验证。

## 实施和兼容门槛

1. 私有 stage 做 SHA 校验、canonical import/纯数据装包、固定 registry、默认 v1/显式 v2 与未知版本拒绝测试；无联网、生成、PG 或 sandbox。
2. 完成 CLI/证据薄适配及 target/suite 参数贯通；复跑原有 v1 测试，验证旧 digest、未实现声明、历史 report/Journal 和平台 driver 不变。新增同 case 跨版本/改定义/改 source hash/任意模块路径反例。
3. A05 hook 与 F07 五产品 diff 单独审查和原产品回归；所有 canonical 代码脱离 D 盘/.local 私路径，临时 checkout 可导入，隔离环境仍拒绝生产地址和数据库。
4. 独审后由主根批准正式 copy；记录源→目标映射、Git commit/branch、测试及发布版本。迁移后的源码重新构建固定测试环境并验证 CLI describe/preflight/init/run/cleanup/offline report 和停止/清理。未经此步骤不称已交付正式 CLI。
5. 仅在剩余实际专项通过、代码打包独审/回归完成后发布正式环境；产品与体验回归按主需求继续。当前文档先存储，不代表服务已更新。
## 正式集成与候选冻结记录

R3按已审清单逐项核对84个原文件/新文件摘要后精确集成，原文件副本另存私有备份。R4仅修本次迁入代码的导入顺序、未使用变量、等价条件、测试格式及循环读者的本轮参数绑定，并同步四个原始字节pin；initializer及两份capabilities摘要未变。20项精确差异经非作者审查通过，R1、R2、R3原件保持。

正式普通导入验证：完整评测631项、原协议221项、新API20项通过。协议测试首次调用缺少原测试辅助模块路径，在收集阶段退出；改用原API pytest配置及正确测试路径后221项通过，未修改产品代码或忽略规则。R4直接受影响56项另由非作者复核通过，四个API工厂无lifespan构造通过；这些检查不计作业务案例。

正常包配置的Ruff检查已通过，不用禁用规则或noqa掩盖本次问题。R4实际重新构建wheel，包内58个Python/JSON文件与正式源码逐字节一致，隔离ZIP直接导入验证四个默认v1仍未实现、显式v2规则正确。新包SHA256为 `619cce7a5dcba9084f9fc0244955cdba26667a4dd5e7a3c868ed0bf23bfa097f`，旧包保留，未拿旧wheel冒充本次产物。

平台版本号准备为0.2.0，不升级上游SDK或混改其他包版本。提交固定后须以新的独立目标分别验证四个完整CLI阶段；本节尚未启动这些新批，也没有宣告正式3131部署或平台评测执行开放。
