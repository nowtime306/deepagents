# Agent 评测开发实施计划

基线：`onwer_agent / 178c38ec6`。需求以 `agent-evaluation-requirements.md` 的 AE-01～24 和原接入方案为准，测试依据 `agent-evaluation-test-plan.md`。先完成 A 的可执行证据，再验收 B/C/D，不以代码或页面存在宣称业务验收完成。

## 职责与顺序

- 独立执行器：`services/evaluation/`，Python 3.12 独立环境、锁定 DeepEval 4.3.2（须实测兼容），三模式正式 HTTP 适配、20 个不同任务、声明式事实验证、受控指标与 JSON/JUnit/Markdown 报告。开发者不修改 DeepEval 核心，不更改生产 API 依赖。
- 平台接入：首先提供受限隔离环境证明接口，不新增任意 SQL/宿主路径/脚本观察后门。fixture 初始化/观察/清理由固定 profile 驱动现有业务 API，缺乏可信证据的任务标为未验证，不能通过。
- 运维：独立安装、运行和工程测试脚本，专用凭据仅通过子进程环境传入，白名单环境不继承供应商密钥或 Confident 登录。遥测关闭和实际 Python 出网审计分别留证，不称为 OS 级完全离线。
- 测试：开发自验后独立代码审查，修复复审后再执行 QA；工程替身、真实平台业务、真实供应商和页面分别报告。当前浏览器拒绝仍为界面门禁，不能换工具绕过。

## A 接口契约

CLI：`python -m evaluation_runner preflight|run --target-manifest ... --dataset ... --output ...`；`report` 仅 output，`cleanup` 使用 target-manifest/output。退出码 0=有效 PASS，1=FAIL/NEEDS_REVIEW，2=契约错误，3=环境错误，130=取消；取消仍落 partial 报告及清理状态。

目标 manifest 的 schema_version=1，含 environment_id、api_base_url、platform_commit、owner_id、required_migrations、fixture_profile(id/version)。凭据不进入 manifest 或报告；目标仅显式本机回环端口，不跟随外网重定向。

`GET /api/v1/agent-evaluations/environment` 使用正式账号认证及 evaluation.read 权限。只有服务器显式配置 EVALUATION_INSTANCE_ID、EVALUATION_PLATFORM_COMMIT，数据库名和独立数据目录名均以 `_test` 结尾，并且私有 evaluation-instance.json 的 ID/源版本/库名/沙箱 namespace/执行人匹配，才返回环境证明。默认关闭；普通生产实例不因此开放评测入口。不返回连接串、数据路径、主密钥或模型凭据。此证明验证受管实例声明及隔离标记，不宣称能证明任意宿主配置绝对安全。

现有 Ask/Work 创建接口没有跨全部入口的幂等提交查询。执行器须先持久化本地 journal；未知提交进入 NEEDS_REVIEW，禁止自动重发。明确已得到运行 ID 后，重启仅查询和继续观察，不能重复提交。后续通用幂等能力另按实际入口补齐，不以本期 journal 冒充服务端恰好一次。

事实断言、任务实际状态、评分状态、清理结果和 gate 判定分别保存。负例测试符合预期不改写原业务失败状态。必需事实失败优先 FAIL；证据缺失、评分异常、必测跳过或清理未知均不能 PASS。重评分与重新执行分别记录。

## 后续里程碑与验收门槛

1. A：依赖实际导入/指标/报告验证，无付费；20 个业务任务通过正式 HTTP 和独立事实观察，Work 并发1；轨迹缺失时不评分、不造数据。未知环境前提单列，不将工程测试数当 Agent 成功率。
2. B：A 验收后实现持久批次队列、版本化集合、三权限及 owner 边界、中文列表/新增/详情、100 个不同任务和三次独立重复。DeepEval 仍在独立进程，不在用户对话路径执行。
3. C：固定比较条件、基线候选报告、JSON/JUnit CI 门禁及故障注入，人工复核保留理由；未测关键项不自动放行。
4. D：授权且脱敏的失败样本形成候选用例，受限数据审核后隔离执行；不重放生产写任务，不隐含引入新的观测平台。

不承诺安装即验收或整系统无缺陷；各阶段依赖、未测项和真实调用预算按证据更新。生产数据、密钥、既有未提交资料和已发布业务版本保持原状；Git 按验收快照单独记录提交，部署另走原发布门禁。
