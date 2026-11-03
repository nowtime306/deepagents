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
- 本分支仅开发快照，未打正式验收标签、未应用生产081、未部署。推送及集成后结果在后续记录追加。
