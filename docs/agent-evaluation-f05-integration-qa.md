# F05 固定代码工作流集成与验收记录

日期：2026-11-04。开发分支：`onwer_agent`，本批基于 `3091666487cca2ef73b1c87f3b00eed6e2c4aff7`。

## 目标与实现

固定代码工作流评测需要经过审批执行、拒绝审批反例、共享期限和可追溯事实，而不是仅验证节点返回成功。本批合入 runner、目标保护、固定流程观察及预算报价，默认公开执行入口继续关闭。原模型工厂、父批次逐效果授权和既有失败诊断保持。

- F05 固定定义升级为 v2，其他十九例定义不变。审批、三个固定根及完整流程参数分别校验；缺失工作流关联、外账号或错误角色不能回退到普通执行配置。
- 单独的 F05 配额为最多十次创建、并发一；普通配置仍为二次创建。报价从不可变定义计算，既有创建预约数总上限六百保留；这不是已分配的预算，实际运行必须先取得相应批次预算，不自动补额。
- 初始化和运行使用同一份经认证的三百秒期限。提供方、工具、创建环境和提交四个效果边界共同检查；取消、过期、认证材料变化或同一目标重启后拒绝继续执行。
- 父监督只操作持有的直接子进程句柄。超时后的停止请求与实际退出、远程回收分开记录，未知效果不重放；已知 FAIL 不因后续诊断失败降级。
- 观察接口检查原账号实时权限、资源所有者、固定模型和流程；不输出密钥或租约令牌。runner/profile 两处认证协议文件逐字节一致。

## 审查与测试迁移

v2/v4 产品增量分别先通过独立审查及工程验证再合入；最终集成只读核验为 PASS，收据 `services/evaluation/.local/workflow-effects-development/f05-final-merge-review-eb3263bff0fe/receipt.json`，SHA256 `ae16505a1b41115713766f2b816b5ab6fb0d4344b3c441c62adbb28820c8abcb`。

正式测试迁移先经过另一角色静态审查，保留十四份原测试的三百四十二条断言及参数化；十六项映射包括小型测试 helper 和测试路径配置。收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/portable-review-20261104-180d/review-receipt.json`，SHA256 `44423938d15db689b38560714c5fb22bd3361845b8aaae7f4f2e5b5637521288`。正式测试普通导入实际产品模块，不安装私有产品副本，不使用候选加载器。该断言数量是静态核对数，不是通过案例数。

迁移候选最初的 Ruff 检查范围较窄；root 使用 API 包完整配置发现九份测试/配置文件共五十处导入、行长及未使用循环变量问题。仅修正本批测试格式与导入、一个未读取绑定 `index` 更名 `_index`，未新增忽略规则或依赖。API 子集二百一十八条断言 AST、全部字符串值及参数化保留，产品文件未变。修复收据 `services/evaluation/.local/workflow-effects-development/f05-api-lint-fix-v1/receipt.json`，SHA256 `f090c070db2dbef94dfe0eb39a96bb0ba49165a83a8be4023253b537f77a85cb`；完整 API Ruff 已复核通过，随后另行独立复测，旧结果和旧较窄检查日志不覆盖。

非作者复审和原 132 项普通导入复测再次通过，零失败/错误/跳过，产品/测试前后摘要一致。收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/lint-review-retest-20261104-0d19/independent-receipt.json`，SHA256 `e6910e0c0f233853f5f252900954b7f5c3e1ca898ad3cb564c94cb7a061231ca`。同一角色另核前述测试数字、实际 JUnit 和文档引用，不将重跑累计成新通过数。

格式修复后的正式 PG7 也在全新库复验通过，零失败/错误/跳过；收据 `.local/team-workspace/agent-evaluation/qa-work-extra/f05_pg_formal_bfe7464c45d44334a1dd5fbf79d3178c/receipt.json`，SHA256 `a690ad28ea904a4f4a6df7ac4bc90cc90ad6ad6095edabaa885c63d102345c79`。本批精确新库 `owner_agent_evaluation_b662419269334aaab9b423a060291b07_test` 经十五表零/会话零后精确回收。生产三个服务仍 200，原进程身份和 HyGL 产物未变；没有新真实任务、模型或 SDK 请求。

## 正式源码实际验证

下列分组均零失败、零错误、零跳过，源码前后摘要一致。分组存在覆盖重叠，不合计为业务任务完成数。

- 客户端完整回归：575 项。收据 `.local/agent-evaluations/team-active-client-cce5193fc5c64e9caa172fd6f3daab74/receipt.json`，SHA256 `cf8148bc16c0d1e68c6528edc2e412af08e25a26b9a637739ad53c7830971ed4`。
- 原执行配置回归：221 项。收据 `.local/agent-evaluations/team-active-profile-b09655e8e8954e15839b2ab951cb1dee/receipt.json`，SHA256 `d7eeaad578c470fc9dd8b99b05d0bfd9fee8d62783c101e9e77a07a04eec7e5b`。
- 评测 API 单元测试：122 项。收据 `.local/agent-evaluations/team-active-api-evaluation-43a879d1f9054bf9a4ba881315282b05/receipt.json`，SHA256 `64398acd551f4e4655229d437f273a86dafbbdbad9b1f94a6497b7e50cd3ffef`。
- 非作者独立 API 新增工程组：132 项。收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/ordinary-api-f05-20261104-693b/independent-receipt.json`，SHA256 `918d050036e20c03a11785df945917c0a26f4a4c0400eb0535f427b5dc7825ef`。三十四个产品模块来源均为正式源码；没有模型、SDK 或真实目标执行。
- 普通导入的真实 PostgreSQL/ASGI 合同：7 项。收据 `.local/team-workspace/agent-evaluation/qa-work-extra/f05_pg_formal_57cdde1bda76448fa27990c64ae695fb/receipt.json`，SHA256 `38d933da1fbbdce0d1ba7f513e8b6d7a6d855d2e6abf7e8fe1a0c911b9e65f66`。账号、关联 SQL、实时 RBAC、三个根上限及取消实际入库核对；工作状态、资源预留和发布测试标记明确使用合成见证，未运行真实节点或远程创建。成功新库 `owner_agent_evaluation_ae9aa5458f3a4a62b2d3fc37b021d291_test` 经十五表零、会话零后精确删除；旧失败库保留。
- 前端实际 TypeScript 检查通过；独立源码目录的生产构建通过，产物 `Uh42uwaj3WMQ4pK5b9ri7` 尚未服务或部署。收据 `.local/frontend-f05-production-build-ops-v1/build-receipt.json`，SHA256 `0b580b6135f20d0f426c13696060e5a2e43014bd3858cd76d069d7785dc6f18b`。原 Web 源码和活动 HyGL 产物全部文件摘要不变。
- API 按包配置的 Ruff、evaluation 按包默认配置的 Ruff 及 Git 空白检查通过。三处 profile 仅整理 imports，递归非 import AST 验证不变；初始化器实际 SHA 为 `8f3a4f7c4c35f8b89ae43569e38bb9e238cd218da6a847e69b3e63c89849fbf3`，能力文件同步。

受限进程首轮客户端为十八项通过、一项 setup ERROR，原因是自身临时目录认证文件 `os.link` 被 Windows 拒绝；原收据 `team-active-client-e0456b1b22584762b313bcf57b50a7e8` 保留。另两组受限异步测试无进展后停止，日志分别保存在 `team-active-profile-0c9cf26c24734b3d8e576f6f49103cb0`、`team-active-api-evaluation-37650d975ca246338e7ad6b7e2c94a28`。随后在获准主机环境用同一源码和原断言复测通过，没有删除断言或改产品来绕过失败。另一个旧 PG 首败使用了不存在的详情接口；仅修正测试为正式列表接口按 ID 查找，原失败库及日志保留，新批复测通过。

## 生产与尚未通过事项

生产仍运行原 `2818e6f647093a1e8ad1695139f9fdaf4ace8ab7`/schema79 稳定核心：<http://localhost:3100>。本批构建和测试前后首页、API ready、管理服务均 200；API 和独立 Ask 进程及原端口归属一致，未迁移生产或覆盖用户数据/密钥。

登录、模型/智能体/知识库等既有管理与 Ask/SSE 已恢复使用；Work、工作流、自动任务执行及新版 DeepEval 消费者仍暂停。F05 本轮新增实际业务完成数为零，A20 独特案例覆盖仍为十三/二十。F05 真实审批/执行/回收、F07、A05、W05～W08及其他原计划门禁不能以这些工程测试替代。

物理符号链接原 WinError1314、旧报告原子替换 WinError5 持有者调查及真实页面操作仍未闭合。Chrome 官方工具最新也明确拒绝生产 3100；不切换浏览器、原始 CDP 或间接通道绕过站点限制。当前构建通过不代表 UI 验收通过。

此前两个 W05 批次预留和失败现场保留，未返还后另起任务；实际新增批次预算授权仍待明确，旧候选 309 的未执行计划因本批源码变化已失效。下一次真实测试须重新固定提交和清单，取得原约束允许的预算，再经独立验收后开放相关执行入口。没有把本批开发提交标为全量生产发布。
