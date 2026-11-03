# 知行平台版本、分支与提交记录

每个版本单独记录；平台版本与用户工作流发布号互不替代。所有远端推送使用正常快进，不改写历史。

## zhixing-v0.1.0：现有已交付系统基线

- 日期：2026-11-03（北京时间）。
- 分支：onwer_agent，跟踪origin/onwer_agent。
- 远端：https://github.com/nowtime306/deepagents.git。
- 提交：[2818e6f647093a1e8ad1695139f9fdaf4ace8ab7](https://github.com/nowtime306/deepagents/commit/2818e6f647093a1e8ad1695139f9fdaf4ace8ab7)。
- 标签：zhixing-v0.1.0，注释标签固定到上述提交，分支与标签已推送。
- 内容：此前知行平台源码首次纳入Git，740个文件，保留现有实现和历史开发文档；真实.env、数据、密钥、依赖与构建产物不纳入。
- 验证：[习惯元技能专项验收](../habit-skill-acceptance.md)、[工作流增强验收](../workflow-dify-acceptance.md)及原系统验收记录。最近本机构建0VM-F40sjLHOkgIlT5ZSe，数据/密钥保留按该部署记录核验。
- 后续工作流版本控制、BrowserSkill与产品优化不冒充本基线已实现内容，将分别记录提交、审查和验收。

## 待验收开发快照：工作流版本管理

- 分支：`onwer_agent`。
- 实现提交：[49c0e0fb4fbb6ba131470fb8bcf31dd8ff086ed6](https://github.com/nowtime306/deepagents/commit/49c0e0fb4fbb6ba131470fb8bcf31dd8ff086ed6)。包含080、历史预览/恢复备份/复制/当前发布号，以及软件Release证据工具。
- 产品研究、Demo和体验记录：[42ecce5fb7de613777ecf159bc6d515b78c6ca23](https://github.com/nowtime306/deepagents/commit/42ecce5fb7de613777ecf159bc6d515b78c6ca23)。上述提交已正常推送。
- 独立验收：Release工具21项、版本API20项、兼容35项通过，另2项跳过。新页面验收受保存权限阻挡；独立站已构建，不代表UI通过。见[QA记录](../workflow-version-qa.md)。
- 无新验收标签，未部署，平台正式基线仍为`zhixing-v0.1.0`。

## 待验收开发快照：浏览器录制技能与体验修复

- 独立分支：`onwer_agent_browser_skill`，工作树`D:\project\my_agent\worktrees\browser-skill`，起点2818e6f。
- 实现提交：[8deb11b5e42ebf3c1ac9e9935464b8564488aa6a](https://github.com/nowtime306/deepagents/commit/8deb11b5e42ebf3c1ac9e9935464b8564488aa6a)。包含081、官方BrowserSkill适配、录制→参数→私有Skill→任务单次授权→整路径审批，及UX01/02显示修复。
- 集成提交：[148b90922e8fdc5d5f650124c39d72adc6e5632d](https://github.com/nowtime306/deepagents/commit/148b90922e8fdc5d5f650124c39d72adc6e5632d)，合并上述工作流开发快照；保留实测上游协议与研究记录。集成后需另验，不以两个分支各自通过冒称组合通过。
- 独立验收：BrowserSkill后端58项、真实无页面运维6项通过；前端构建和独立静态审查通过。见该分支`docs/browser-skill-qa.md`及代码审查报告。真实外部模型继续跳过。
- 本分支仅开发快照，未打正式验收标签、未应用生产081、未部署。

## 最终开发集成及验证补充

- 测试隔离修复：[f41550ca9a531403122b8f968ad2d9b003377890](https://github.com/nowtime306/deepagents/commit/f41550ca9a531403122b8f968ad2d9b003377890)，仅清理测试显式登记的运行，未改生产队列规则。复审后组合134通过、2跳过，后续真实Coding补测2通过。
- 组合报告及安装验证：[671c67266e4fe337ca1073157da50c7a8c027af8](https://github.com/nowtime306/deepagents/commit/671c67266e4fe337ca1073157da50c7a8c027af8)。`onwer_agent_browser_skill`已推送至该提交并保留追溯。
- Windows发布工具：[4b818f326aaa3ba8cd001418d4d1a00b036d5bf4](https://github.com/nowtime306/deepagents/commit/4b818f326aaa3ba8cd001418d4d1a00b036d5bf4)，混合CRLF/LF场景，双摘要保持原始验收字节与归档一致性；独立审查及31项通过。
- 合并至用户指定主开发分支：[49b23fb35b5472a8bbf83b9fec314622a5ad9e7b](https://github.com/nowtime306/deepagents/commit/49b23fb35b5472a8bbf83b9fec314622a5ad9e7b)，`onwer_agent`现包含两组实现。相对已验组合671c的业务Git内容差异仅为上述发布脚本及其单元测试，已单独验收；其它应用、前端、后端、迁移内容相同。
- 应用页面仍受浏览器保存权限拒绝，未正式部署或新打验收标签；原基线标签不覆盖。开发提交及报告可审阅，实际部署后另追加BUILD_ID与当前数据备份记录。

## 待页面验收开发快照：资源引用解耦与变更追溯

- 分支：`onwer_agent`，远端仍为 `origin/onwer_agent`，不改写历史。
- 实现提交：`833a4ac212b2103d05e3a73e352d27af74d52e07`，资源当前配置与业务发布版本解耦、运行阶段快照、历史与索引兼容、工具/仓库/通知边界及迁移 082～084。
- 审查修复提交：`fcdc1dd1ef8022dfe80a4d20f4ebd972578b0a0b`，历史列表/详情关联、饱和查询、历史类别及工具 Schema 强类型约束；独立复审关闭后冻结进行完整相关回归。
- 测试提交：`1dc9bb412ee415107a7db107431334bc68c44ad8`，真实恢复测试用公开图状态读取完整消息；不改变产品或弱化消息唯一性、检查点和单次执行断言，独立复审后实测通过。
- 上述三项提交已正常推送至 `origin/onwer_agent`，远端快进 `048169073..1dc9bb412`。这是开发快照同步，不是部署或正式发布。
- 验证：独立后端 401 passed/21 skipped、66 passed/2 skipped；单元 282 passed；根执行者真实 Coding 专项 6 passed，另一执行者真实补测 18 passed。原跳过基线保留，补测另列。组间有重叠，不合并成总数；补充分组、审查与边界见 [验收汇总](../resource-reference-acceptance.md)。
- 实际新页面尚未验收，生产未应用 082～084、未发布 RDC。没有新正式标签。
- 已发布 Web 恢复记录：开发误写默认 `.next` 后，从 `zhixing-v0.1.0` 的 `2818e6f647093a1e8ad1695139f9fdaf4ace8ab7` 在独立目录重建恢复，现 BUILD_ID `Z9Xjn2GY4sh3N_OwxqLbU`、进程64396；原基线二进制 ID `0VM-F40sjLHOkgIlT5ZSe` 不再作为当前构建声明。744个非缓存文件摘要及进程监听已核验，数据库/API未因此迁移或重启，见 [事件记录](../resource-reference-build-isolation.md)。

## RDC 后续测试提交

- 分支 `onwer_agent`；测试提交 `efd2db683`，新增三类配置投影的HTTP边界回归。无产品、迁移或依赖修改，根角色先复审，独立测试角色在随机库执行，9项新增及6项原接口回归通过。
- 原备份工具另完成独立容器内真实导出/恢复、错密钥与文件篡改专项；使用私有测试定位脚本，未使用生产源或完整生产源码包。见 [后续验收](../resource-reference-followup-qa.md)。
- 本次只是测试与验收记录提交，没有新发布标签或部署，不与历史通过数混加；用户未跟踪的 DeepEval 文档保持原状。

## 开发快照：DeepEval Agent评测

- 分支：`onwer_agent`。来源方案已按本轮用户请求纳入版本，保留原文；此前学习笔记目录不在本轮改动范围。
- 平台隔离证明提交 `ae69f50b657c2943119270d6c9489cd9cfd9cddd`：AE需求、开发和测试文档，默认关闭的隔离接口、环境/owner/权限核对、工作流用量与专用Sandbox连接注入。独立API32通过/1跳过，专用连接另批9通过（含权限回归），不相加。
- 执行器核心提交 `9df446cdf`：独立环境/锁、三模式HTTP工程适配、journal、事实/指标和报告。经修复复审后的UTF8冻结工程39项通过，不是A20执行。
- 严格契约修复 `ca4da393c`：版本与数量拒绝布尔值、注册参数封闭校验、未实现检查在写入前阻断。独立冻结复测54项通过/0跳过，取代此前39项为最新工程范围，不累加。平台 `read_marker` 后续仅补 `Path` 参数/返回注解，Ruff通过，不冒用先前API字节摘要。
- A20真实固定profile、批次隔离编排和运维脚本仍在开发与审查；B/C/D按需求门槛继续。见 [阶段验收](../agent-evaluation-acceptance.md)。开发提交不是正式发布，当前没有DeepEval上线标签。
- 文档及类型注解快照 `2c9a75dbb07291b16f5e239a5da4e27aa93a38e5` 与此前三项实现已正常推送；没有部署。该快照另含B100设计，不代表100任务已开发或执行。
- 首A02候选源码在独立审查R01/PF1/PF2及O1～O7关闭后冻结：客户端工程66通过（含原54）、profile工程18＋独立探针2通过、运维批次27通过及目标守卫4通过。批次不累加为Agent成功数。此记录写入时真实A02仍待执行；下一条按实际候选提交ID及队列证据补记，不预填成功。
- 首A02源码 `69a52abfd`，仅EOF空行修整 `3f1f680f5a43653be81400f50b6b7402adad4060`，均已正常推送。真实单例两轮队列返回代码PASS，但独立核验发现事实快照及全程审计留存缺口R02/R03，验收为NEEDS_REVIEW，不能作为A20或发布通过。自有测试库与API已回收，原报告未改。见 [阶段验收](../agent-evaluation-acceptance.md)。
- 后续提交：`4e2d6fb30` 区分执行与验收的文档记录；`0b846f72ab189b95eda60901e5573e6e871d0287` 父批次中文报告；`533417e396e93282324d1381d904e52b4ff14eee` 事实封存/逐CLI审计/最后收据与消费者、独立回收门禁。分支仍 `onwer_agent`，以上均已正常推送。
- 候选533417e的全新A02受控工程验收通过：1case/1repeat，31项只读重算与回收收据一致；不同组工程结果分别保留，不合计为Agent任务数。仅A20中的1项，不是正式发布，没有新标签或生产部署。首次旧候选的待核验结论保留，详见 [阶段验收](../agent-evaluation-acceptance.md)。
- A02 验收记录提交 `c4d83a243832562b132d1affe8d38ec8eec7aedf`，随后实现候选 `f8ca28d2e3ec4dff7952e8a2f4ce6805514c9bdd` 已正常推送至 `origin/onwer_agent`。独立工程 204 项及 profile 29 项通过，真实数据库观察投影另 1 场景/230 断言，不累加为任务。
- f8ca28d 三例各一次实际执行并独立复算验收：W01 原任务 FAIL（缺文件负控制正确检出）、F02/F04 PASS，父门禁仍 FAIL/退出1；真实 Coding 沙箱及三个自有环境均精确回收。累计 4/20 案例限定工程验收，不代表全任务成功或整计划完成。源码、分支、收据及边界见 [阶段验收](../agent-evaluation-acceptance.md)，没有新标签或生产部署。
- 三例验收记录提交 `780a376520b9cd26badcc3c5c31547f0b0a1b5cb` 已推送。下一增量的 W03/W04 观察与封存模块提交 `935698d37f6bf25a36ce59296bfccae0d568e721`，独审关闭后独立 53 项通过；共享接线及实际任务仍待后续候选，未新增 READY 或部署。完整分支仍为 `onwer_agent`。
- 消费者证明客户端及剩余需求提交 `6f826cfcd1be9e2f4754c5025aed2286d100d9b2`，独立 67 项通过；零调用原生聚合修复 `f96d1c49210ec0f625f35eb9255d32b903bc7978`，独立 6 项通过。连同 `935698d37` 已正常推送，未部署。后续共享 A01/W03/W04 与 Java 模块各自独审/工程 QA 及真实保护探针记录在专项报告，实际三例尚待下一固定候选。
