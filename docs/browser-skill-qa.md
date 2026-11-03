# BrowserSkill 独立非界面验收

2026-11-03，独立测试角色。依据产品 BSK-01～09、browser-skill-tests.md 与最终后端合同。测试只使用隔离随机 `_test` 数据库、虚构账号/模型和自有空浏览器 profile；没有访问3131/3100，没有操作个人浏览器或调用外部模型。应用页面验收及正式上线仍待完成。

## 代码审查前置

后端最初P1取消清理互斥问题经过作者修复、独立复审后才开始本批测试。20个已审文件逐项SHA与 `.local/team-workspace/browser-skill/review/backend-reviewed-source.json` 相符；没有修改生产业务代码。

运维10文件、前端7文件与 `.local/browser-skill-ops-qa-e3e15ac979b64890bbc2894029f55ec4/` 中对应freeze清单逐项校验。静态review已通过不等于页面测试通过。

## 实际执行：API、数据库和协议反例

本批七个模块 **58 passed in 15.50s**，执行前后后台/测试源码未变。日志与源摘要：`.local/team-workspace/browser-skill/qa/results/owner_agent_browser_skill_qa_2f966fea2b_test.{log,json}`。使用独立 `owner_agent_browser_skill_qa_2f966fea2b_test` 库和私有数据目录，runner退出码0并在finally删除该随机库；未迁移或恢复生产库。

包含现有API、生命周期、单次授权、Work guard、Trace导入及部署准入，以及本测试角色新增的8项可复验反例，永久文件为 `services/api/tests/integration_tests/test_browser_skill_independent.py`：

- IQ01：客户端在参数编辑中附带替换steps被422拒绝，原revision不变；发布不会改变通用助手绑定，也不创建任务运行。
- IQ02：删除正式Skill后安全录制仍可保留，但版本表消失，旧skill_id不能再重放，host CLI调用为零。
- IQ03～06：StartWork真实创建双授权运行，guard先可通过；逐一使真实DB租约过期、取消运行、撤销管理员、删除冻结快照中的同意字段，下一次guard都拒绝。不是只测启动表单或内存flag。
- IQ07：故意把allow_local_browser=true放进自动派发输入快照，调用真实enqueue并读取数据库，新运行列和快照都false，输入原快照不被改写。
- IQ08：真实跨owner录制存在时，limit1分页只返回本人，下一页为空，不泄露另一owner ID。

其余通过覆盖：草稿CAS/重命名、发布幂等与安全编码元数据、敏感和不支持动作阻发布、原始输入/VOM/ref不写安全草稿；取消/异常停止未完成时保持busy，重复取消单次stop，新录制与Work均拒绝，未知清理/重启保持隔离；每次运行缺省不同意，coding/非管理员拒绝；无审批零浏览器调用、未知点击不重试、fresh语义目标唯一匹配；活跃录制阻止部署。

所有浏览器/CLI操作在上述58项内均为协议替身，数据库是真实隔离PostgreSQL。未运行 `test_browser_skill_real_approval.py`，不能将作者先前真实扩展组合结果冒称独立QA的页面实测。真实工具动作与参数重放由作者专项保留证据，另需当前界面验收。

## 运维独立运行

独立执行最终源码的 `scripts/test-browser-skill-operations.ps1` 完成，退出0，6项检查全部通过：官方0.4.1/protocol1.8唯一连接、重复启动同实例、错误PID停止拒绝、正常关闭释放端口/删除连接、外来端口冲突拒绝、CDP建立前故障只清理自己创建的进程且不发布ready。使用新空profile和私有home，instance `d2a712cd`，没有打开网页；当前测试专用进程已退出、端口已释放。

独立证据：`.local/browser-skill-ops-qa-3c141b0792e14868af6d0955844826c5/{qa-result.json,qa-transcript.log}`。执行前后重新校验20后端、10Ops及7前端冻结文件，全部一致；独立汇总 `.local/team-workspace/browser-skill/qa/qa-reviewed-source.json`。没有复用作者运行目录作为本次结果，也未递归删除任何测试目录。

## 页面与发布门禁

用户文字允许3131后，根角色再次尝试仍被浏览器保存的权限明确拒绝；不能换浏览器、代理或工具获取同页。知识库UX-01/02、新录制列表/参数/发布、Work显式同意/完整路径审批、390px和无控制台异常等实际页面验收全部保持pending。构建、静态review、上述58项均不替代这一门禁。尚未批准正式部署/打验收标签。

真实第三方网站/账号、外部模型效果及本机profile多用户配对未测；本轮不宣称支持所有站点或整系统无bug。
