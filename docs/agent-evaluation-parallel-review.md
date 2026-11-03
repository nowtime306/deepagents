# F06 并行评测审查与验证

日期：2026-11-04。依据 `agent-evaluation-parallel-plan.md`，范围为新增固定证据处理器、两组客户端测试、最小分派/零调用上限/只读观察器接线、能力摘要及共享预算 API 契约测试。本页分别记录工程门禁与随后单次正式样本，不宣称真实模型质量或上线。

## 独立代码审查

原十文件冻结 `.local/agent-evaluations/f06-freeze-0ee4f391455847de94eb5f0463498635/manifest.json` SHA256 `32e0c6b830e4bc5eb3f7da58db40b7ceccf1278c51d557edd0c660d7f66707de` 经非作者检查，发现并实际以纯探针复现两项 P2：

- F06-R01：只改引用的 kind、不改封存文件和摘要，仍可重算 PASS/允许 CLEAN。修复为引用与文件内部 kind 严格一致；原反例现在拒绝。
- F06-R02：非 OSError 的 journal 保存失败未设置失败标记。修复为所有保存异常均先设置失败标记再抛出，下一次 execute 在任何 HTTP 前阻断；原 ValueError 探针已确认0请求。

两项修复只修改处理器及其回归测试，其他八项字节不变。新冻结 `.local/agent-evaluations/f06-review-fix-freeze-4325e8a509464596a8b019106a10932c/manifest.json` SHA256 `2990a66ee454dfc1f995beee15ed82b8119e9b8ec62a3144a61cc8e51c624210` 经原探针复审关闭后，才进入独立 QA。

## 独立工程 QA

非作者实际执行，分组不相加为业务任务：

- 客户端24个指定文件：353通过、0失败/跳过，25.45秒。包含旧351/328，不能累加。新覆盖固定四根封存、分支合法交错、缺分支/错输入输出/提前汇合、篡改引用、保存失败、未知提交不重放及脏目标在主运行前阻断。
- API环境中的七个精确 profile 文件：71通过、0失败/跳过，5.38秒。包含旧69；新增 F06 跨根/重建的硬零调用与本人作用域只读计数，不把缺账本或失败观察补成零。
- 全新隔离 PostgreSQL/正式 HTTP 契约：1项通过、0失败/跳过，7.11秒。三个父节点加九个子节点共享根预算：12时实际12个 started/24个事件并成功，11时实际11个 started/22个事件、422 `WORKFLOW_NODE_BUDGET_EXCEEDED`、持久 FAILED 且无根结束完成；实际模型/Ask/Work/沙箱均0。四个工程根不是正式 F06 样本的四根，不混算事件数或案例数。

253项源码、测试、配置及十项冻结前后/最终摘要匹配。独立 QA 使用清理后的环境、私有 HOME、锁定依赖，无供应商凭据；Python 网络限制只证明该进程的观察范围，不声称操作系统或 Worker 全部出网验证。私有 PG runner 最初字段名映射错误发生在创建/连接数据库前，原错误和脚本保留；只改私有映射后才运行新随机库，没有改产品断言或复用旧诊断库。

新库 `owner_agent_f06_budget_qa_83bc681cbbdf46a8b792ad2b_test` 在0连接后精确删除，另核库不存在/会话0。证据 `services/evaluation/.local/f06-independent-qa/run_a7e7278a24c7/`；`review.md` SHA256 `3d6d4b606e944dd4be44b6c434a1e85b90debf9e422935c8b37106bf4c35eb5e`；完成收据 `9b0b11bad76952087ab09a789ba85105a25adb65069f8f3eb50bf91946dc7e08`。

## 固定候选前的样本计划

运维已准备单F06/一次重复脚本，保守预算300秒/20调用/20000 Token/2沙箱预约；预期实际模型与沙箱均0。固定新提交后才创建新的目标，正式运行包括两个子流程初始化、父流程初始化和一次已发布运行：初始化36个事件、主运行24个，共60个，逐分支核验声明顺序和全部完成后汇合。仍需封存审计/ready、只读独立重算和精确回收；代码有处理器或工程353通过不能替代这一步。没有新服务、依赖或生产迁移，不变更被拒浏览器权限或既有业务数据。

## 候选8a15c8d：正式单样本限定验收

固定提交 `8a15c8d78f022e191f53fbd6e96a1e9bb065d4dc` 正常推送后，新批次 `.local/agent-evaluations/suite-f06-8a15c8d-9df3c2e4/` 真实执行 F06 一次。三次初始化和一次已发布运行均 SUCCEEDED，节点事件分别6/6/24/24。两子版本及父 v1 定义/摘要固定，lower/upper/lower 每条路径的输入、转换和结束均齐全，所有分支结束后才汇合，输出按声明顺序正确。四根原生六项计数均显式为已知0，provider/effects完整零观察；保守预约2沙箱与实际0分开记录。此处是正式同步 Workflow HTTP，不称为任务队列或 Worker 验证。

四次CLI审计及最后当前索引收据、JSON/JUnit/Markdown和父子结果一致。非作者用同次封存原件独立重算事实、引用摘要、版本与归属，没有重跑业务或重新导出报告。316项来源、十文件冻结、suite HEAD及目标清单均匹配。新库 `owner_agent_evaluation_eb739b88a25e4b95adc014198ef3dd74_test` 已精确删除且会话0，PID59724退出并有同环境 graceful 收据，namespace `51a2e6b2-6728-44d2-a6e1-dbe99ac0d2b4` 管理器/Docker末次库存0，正式cleanup/stop/guard/drop均退出0。旧4627诊断库仍保持原状态，非 CLEAN。

执行完成收据 SHA256 `575f1f9d83fa2b88602660a52a2e9b75179265eee2e3d15703bbfe006be81a33`。独立证据 `services/evaluation/.local/f06-real-independent-review/actual_96187176ee70/`，事实核验 `7513fa1b5dd38887b8753cb467fe74dff1135defb58dcd282a9927a26d7089b3`，完成收据 `9600ef8f9fae51cd10c652e7219ab45914590138c480622a7e9fab6bd92ac289`。

本次仅新增1个不同案例、1次重复，累计8/20限定案例验收；不与前面的PG工程四根/58条事件混算，不把任务重复或分支当新案例。真实供应商、完整原生轨迹、OS/worker出网、UI及生产部署仍未验收；其余阶段按原门禁继续。
