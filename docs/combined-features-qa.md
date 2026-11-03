# 工作流版本与 BrowserSkill 组合回归

2026-11-03，独立测试角色。集成开发提交148b90922e8fdc5d5f650124c39d72adc6e5632d，经 combined-features-review.md 静态审查后，才开始本批组合测试。没有访问应用页面、生产数据库或外部模型，不运行真实浏览器审批专项。

## 首批真实结果与归因

WVR两模块、BSK七模块、兼容九模块、Release两单元模块，在单个随机隔离数据库中一次执行：**132 passed，2 failed，2 skipped，66.88秒**。执行前后全部后端/测试源码及47项集成已审文件SHA未改变。数据库finally删除，仅保留隔离日志/数据证据。

两个失败均在test_work_budget调用test_durable_sandboxes.prepare时，WorkQueue.claim按全局队列语义返回了更早的PENDING运行，而非新用例刚创建的ID，尚未执行预算断言。新增BSK授权/自动派发用例会留下测试用的待执行记录，原公共client fixture每case只建新账号，并不清空全局队列。

没有把此结果解释成预算业务故障，也没有跳过失败断言。进一步在全新随机库仅运行原work_budget两例，**2 passed in5.87秒**、冻结未变，确认共享测试队列隔离问题。初次失败证据保留，不将单独两例通过冒充组合通过。

## 测试隔离修复及复审

仅两份测试文件：test_browser_skill_opt_in.py新增work_runs yield fixture，启动case先捕获owner，测试在得到真实创建的run id后显式登记。测试断言全部执行完，finally才将这些已知ID且owner匹配的非终态运行结束，并清理其租约。独立guard及自动派发用例使用同一fixture登记自己的ID。

不结束owner的其它运行，不扫描/清空全局队列，不删除数据，不修改WorkQueue或旧预算断言。根协调者独立读两文件diff审查通过，提交为f41550ca9a531403122b8f968ad2d9b003377890；私有组合review清单保留首批记录，更新HEAD及两测试SHA，其余生产业务源码不变。

## 最终同批组合结果

复审后的同样20模块，在一个全新的随机 `_test` 数据库内一次完整执行：**134 passed，2 skipped，70.17秒**，退出码0。没有拆开模块来规避共享队列问题，没有额外跳过预算case，也没有吞掉原断言。首批两个预算用例在完整组合中已通过。

开始前核对HEAD及47个已审文件，结束时再次核对47项及全量后端/测试文件：`frozen_sources_unchanged=true`。随机库在finally删除，使用虚构账号、协议替身、本轮私有数据目录；生产库和用户密钥未写入。最终证据文件和完整SHA以 `.local/team-workspace/browser-skill/qa/combined-qa-final.json` 为索引；首次失败、单独预算归因和最终组合日志均保留，不能把此前失败日志删除或覆盖为通过。

## 验收范围

两个skip为现有node_debug的code/approval参数化用例，要求RUN_LOCAL_SANDBOX=1的真实Coding环境。上述20模块同批未启用该开关，仍保留2 skipped计数；后续实际专项结果单独记录如下。

前端TypeScript/生产构建由根角色另行执行；页面许可仍明确拒绝，UI验收与上线状态pending。本报告仅批准当前集成源码的非界面回归层，不是正式发布批准。

## 主开发分支留档

源码后合并至 `onwer_agent` 的49b23fb35b5472a8bbf83b9fec314622a5ad9e7b。根以Git逐路径比较确认，相对已验功能分支671c67266，应用/后端/前端/迁移均无额外代码差异；仅发布工具及其单元测试有后续Windows修复，另有31项独立通过记录。没有把文档合并或换行检出变化冒充又一次134项运行，页面门禁继续保留。

## 真实Coding环境补测

根角色独立审查私有run_real_nodes.py后代执行，测试角色读取原始日志、结果JSON并核对最终47项业务/测试摘要。仅执行原有test_durable_node_debug_approval_resume_and_publish_boundary的code与approval两个参数，不修改用例或业务源码，RUN_LOCAL_SANDBOX=1，**2 passed in65.98秒**，退出码0。它们验证真实Coding环境中的单节点执行、审批中断与freshEngine恢复，不是协议替身或浏览器页面测试；邻接模型节点被NoCalls断言保护，没有外部模型调用。

专项前后认证18180全局inventory均为0、has_next_page=false；真实数据库session账本无未释放项，确认清理后才删除隔离owner_agent_real_nodes_qa_1c88d334b7_test库。临时manager.key是本次创建且同摘要才删除，独立确认worktree该文件已不存在；原项目密钥未改写。47项源码在专项前后与当前复核均一致。

原始证据：`.local/team-workspace/browser-skill/qa/results/owner_agent_real_nodes_qa_1c88d334b7_test.{log,json}`，其数据目录保留空session-ledger.json与测试证据，无递归清理。最终索引combined-qa-final.json补充专项引用，不覆盖先前同批日志。准确计数为**组合批134通过、2跳过；跳过的两例后来真实环境专项2通过**，不声称136项在同一批次通过。

此次补测不解除应用界面验收与正式部署门禁；未访问3131/3100或操作个人浏览器，仍未批准上线。
