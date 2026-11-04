# 评测异常报告修复与稳定服务恢复

2026-11-04，分支 `onwer_agent`。本批不代表 DeepEval 全部业务验收完成。

## 已修复并验证

评测命令开始时先将审计索引置为 PENDING，撤销旧 READY。异常后写独立 `partial-report.json`，明确 INCOMPLETE/未可验收，保留本次已受理任务、未知用量及审计引用，不覆盖旧正式 JSON/XML/Markdown。已经持久化的 FAIL 在后续异常中继续保持 FAIL。公开报告读取器将缺失材料统一拒绝为不完整发布，既有调用方的 ValueError 合同保留。

私有 v1 审查发现 FAIL 降级反例，v2 修复后独立 8 项边界及两组真实 CLI/Journal 合同通过；每组仅一次受控请求受理，未重放。合入后完整 client 回归 502 通过、零跳过。补齐导入排序后非 import AST 保持一致，initializer 摘要同步更新，最终来源再次 502 通过、零跳过，原件 `team-active-client-e505d89e909d45278e3b678742df779c`。失败原件保留，没有修改原断言使其通过。

独立审查记录：`.local/team-workspace/agent-evaluation/qa-common/partial_v2_review_6537efeb3857` 和 `qa-common/public-reader-review`。完整回归原件：`.local/agent-evaluations/team-active-client-122b0733cf4d4941a5ac30a02b399441`。

## 业务验收仍未完成

真实 W05 在候选 `71780807df30f95a5c6eb7f6dc6ad5b7b60e5018` 成功受理，但 300 秒内持续 WORK_CAPACITY_WAIT，最终按原停止规则取消，零模型调用、零沙箱。它不是成员超时验收通过。原数据库、配额预留及报告保持，独立只读核验确认精确进程退出、连接归零、该命名空间资源归零。旧正式报告未反映这次运行的缺陷触发上述诊断修复，未回填旧报告。

A20 不同业务案例仍为 13/20。容量拒绝原因诊断、F05/F07、其余案例以及 B100/C/D 继续推进，工程测试不计作业务任务。评测执行入口仍关闭。真实外部模型与 judge 联调继续跳过；浏览器工具的页面访问限制仍未解决，不把 HTTP/构建当真实页面验收。

## 稳定服务恢复范围

为解决生产长期临时只读，本次恢复原已发布 `2818e6f647093a1e8ad1695139f9fdaf4ace8ab7` 核心，使用同一受校验工厂验证后部署；不以未完成的新版评测作为恢复既有登录、资源管理与 Ask 对话的前提。工作流/Work/自动任务及其他未恢复执行入口明确 503，避免受理后永久排队。

隔离验证实际通过：38 项真实 HTTP/进程内执行门禁；全新 PostgreSQL 登录、知识库/Work 草稿 CRUD、Ask 排队和 SSE；原独立 AskServer 子进程通过一次 loopback 模型 SSE、输入 10/输出 3 的原生账本与合作停止验证。非付费供应商联调，非生产测试数据。两套新库均在确认连接及进程收尾后按精确身份清理；私有测试数据保留。

现有 3100 实际服务构建为 HyGL，319 个 JS/CSS 全部 HTTP 200 且与精确产物逐摘要一致，保持该产物。早先仅共享 chunk 相同不足以判断 Z9X 为实际构建，已更正并保留两份核验记录。

2026-11-04，稳定核心恢复完成。API 实际 PID 53996、独立 Ask PID 54904，生产地址 `http://localhost:3100`。原 Web、8100/3131 测试服务及 18180 管理服务保持。Work、工作流、自动任务、外发和多模态执行继续明确暂停；新 DeepEval consumer 未发布。

新备份 `services/api/.local/backups/20261104T102148Z-0baa23af`：39 份文件校验、独立恢复记录数量匹配、3 份加密凭据可解密。生产没有恢复或迁移。API 合作停止、外层锁释放后才调用原生备份，再先启 Ask、后启 API。原用户数据七表数量、九表 ID 集合及主密钥摘要保持；不夸大为逐字段比较。

运维最终收据 `.local/agent-evaluations/stable-core-start-75d4d82dc27044b788a83c65ebd81099/final-receipt.json`，SHA256 `299d5018b4c05383685912a7f2518fc96e64426d539e4775be25e9bb23604687`。root 再独立验证登录与四项列表全部 200、API/管理服务健康、唯一新鲜后台心跳及七表数量和密钥一致，收据 `stable-core-independent-932f9f7cea55497f9c93a887868c3fbc/receipt.json`。这些是实际 HTTP 和数据库验证，非浏览器页面验收；未真实发问或修改用户资源。

诊断代码提交 `1694f92840a122d58a7f88fba63b29bfb99c8a73` 已推送并独立核对远端 `origin/onwer_agent`。它与生产恢复所用的原基线是不同来源，不冒充新版全量部署或创建通过验收的新版标签。
