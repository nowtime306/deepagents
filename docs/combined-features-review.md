# 工作流版本与 BrowserSkill 合并增量审查

2026-11-03。对象为开发快照 `148b90922e8fdc5d5f650124c39d72adc6e5632d`，由已独立审查的工作流版本提交 `49c0e0fb4`、产品文档提交 `42ecce5fb` 与 BrowserSkill 提交 `8deb11b5e` 合并。

结论：**静态增量审查通过，可交组合回归测试。** 没有新增可证实的阻断问题。这是待页面验收的开发快照，不是正式验收版本，不批准部署或正式版本 tag。

## 代码保留检查

- 以 `2818e6f` 为共同基线，逐个比较每组变更的业务代码、前端、运维脚本和测试文件与各自父版本。除共同修改的 `main.py` 外，工作流组 **12 项**与 BrowserSkill 组 **34 项**均与其父提交内容一致；没有额外引入的业务路径。比较只统一 CRLF/LF，留档同时记录实际文件 SHA256。
- `services/api/app/main.py` 是唯一业务合并点。相对 BrowserSkill 父版本，它精确仅增加 `workflow_versions` 导入及 router 注册，无其它变化。原 `workflow_router` 在新动态版本 router 前注册，`/workflows/options` 的原先匹配优先级保留。
- BrowserSkill API router、lifespan 中的 runtime 初始化和 `recover()`、yield 后的 `close()` 均原样保留；已有 consumer、自动任务、浏览器维护、浏览器服务、向量库、监测与数据库关闭流程没有被合并丢失。Work 服务的独立 runtime 初始化与其已审父版本一致；没有新启动本机 daemon 或修改生产配置。
- 最终 `main.py` SHA256：`6017752967576b8350297018de6f044c9283e7217388354c16288ae51d7f4572`。

## 迁移与文档

迁移 `080_workflow_version_control.sql` 与 `081_browser_skills.sql` 分别保留各自原内容。现有 `manage.migrate` 按文件名排序应用 SQL，故先 080 后 081；二者分别扩展工作流版本和浏览器录制/运行授权，不存在同名迁移或相互覆盖。本次只读检查，没有执行生产迁移。

`docs/browser-skill-development.md` 的冲突解决保留作者实测语义：CLI v3 state 的 page 为 `s1.txt`，实际文件位于 `states/s1.txt`；不传 `--redact-values`，导入后全参数化并清理私有原始 bundle/recovery。开头增加当前独立非界面验收与页面验收待办状态，初研内容明确不替代最终合同。没有恢复已纠正的 redact 行为或虚构录制路径。文末冗余空行整理不影响实现。

## 证据及边界

只读对比脚本及逐文件摘要在 `.local/team-workspace/browser-skill/review/check_combination.py` 与 `combined-reviewed-source.json`。留档包含集成 commit、各组原样保留检查及共同入口摘要，可供组合 QA 冻结核对。

本次没有修改业务代码或操作 Git 状态，没有运行新页面、真实用户浏览器、外部模型，也没有执行部署。既有后端/ops 测试结果属于此前各自冻结版本；组合回归应使用本次集成源码另行执行。生成中的 `apps/web/tsconfig.json` 工作区差异没有纳入本次已提交业务快照，也不在本次批准范围内。

## 组合首轮后的测试隔离修复复核

根协调者独立核对QA提供的两个测试文件增量。首轮132通过、2失败、2跳过；两个预算用例在claim阶段拿到前面新BSK用例留下的PENDING记录。全新数据库单跑预算两项通过，未更改业务预算、全局claim规则或旧断言。

修复只在 `test_browser_skill_opt_in.py` 建立yield fixture，捕获初始owner并由用例显式登记返回的run ID；`test_browser_skill_independent.py`仅在会创建运行的用例复用。所有业务断言结束后，finally仅将这些已知ID、同owner、尚未终态的测试运行取消并释放测试租约。没有全表/全owner清理，不删除历史，不依赖可能已换用户的cookie。根静态复核通过，可重新执行组合测试；最终通过仍须以重跑结果为准。首轮失败日志及原冻结摘要保留。
