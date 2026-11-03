# RDC 独立QA执行清单（准备版，尚未开始）

日期：2026-11-03。对应根任务指派：独立验收非本人作者的runtime/index/root trace-route/backend范围，覆盖RDC-01～04、06、08～10、13及14的后端部分。状态：PREPARED / NOT EXECUTED。

本文件补充完整产品测试计划 `resource-reference-test-plan.md`，不是测试结果。此轮只静态读取测试源码、AST清点和计算摘要；没有执行runner、pytest、测试收集、数据库创建、真实沙箱或任何页面。

## 1. 开始执行前必须全部满足

- 根任务提供最终冻结40位提交SHA，并确认当前源码对应该提交。
- 所有阻断问题经独立审查关闭，包括083旧运行迁移专项、backup兼容专项；作者自测通过不替代这两项审查。
- 根任务明确发出“开始独立QA”信号。没有信号，即便代码看起来完成也不执行。
- 冻结后重新生成测试/后端源码manifest，根任务确认摘要；运行前runner对摘要再次比对。源码变化即停止，不能拿旧证据覆盖新代码。
- 本人不对自己编写的history/repository/notification实现作独立通过结论。它们由另一位独立验收者负责；此处共享链路的回归结果不能被归入RDC-05/11/12独立通过。

## 2. 已准备的runner与清单

位置：`services/api/.local/team-workspace/rdc/independent-qa/`。

- `run_runtime_index_qa.py`：仅显式--run且全部gate满足后执行。
- `execution-manifest.json`：62个已有/待review测试文件、242个顶层test函数的源码清单及摘要。此数字没有展开parametrize，不是pytest实际发现数，更不是通过数。
- `start-gate.template.json`：所有开始/审查布尔值均为false，freeze_sha为空。当前不可用于开始执行。

runner默认不接触数据库。仅解析源码或显示未开始信息；执行模式先验证根任务开始信号、冻结SHA、083/backup/全部阻断审查状态、作用范围、测试和后端源码摘要，再读取本机数据库credential source。SHA由根任务提供，runner不执行Git；另对app/*.py、migrations/*.sql及选中测试摘要验证，不能声称它独立证明了整个Git提交状态。

执行时每组创建独立随机 `owner_agent_rdc_indqa_<group>_<随机>_test`，只用postgres管理库创建/回收该名字，不连接生产业务库；TEST_DATABASE_URL/DATABASE_URL与数据目录均明确指向本组。stdout/stderr在父进程内脱敏后才写证据。每组finally验证前缀/后缀后DROP，仅清理自己创建的数据库。

每组单独生成pytest.log/result.json，最后汇总summary.json。skip继续列为未验证，不能当通过。真实供应商和页面始终不执行；RUN_LOCAL_SANDBOX在子进程环境中移除，不能通过继承环境意外开启本地沙箱。

## 3. 执行分组与独立结论边界

### acceptance：1文件、4个新增源码函数（待根任务review，未执行）

test_rdc_acceptance：父子均发布后成员模型维护仍实际受控委派，并经HTTP trace断言三个不同模型的role/真实revision和固定父子业务版本；实际AskWorker.tick队列路由刷新；WorkEngine.execute在首次model_factory解析后以受控CapacityUnavailable停止，trace只显示resolved、没有伪造调用；cold cache确认后新phase经HTTP展示且同host不同port能区分，暖阶段维持确认目标。

这四项补第4节的缺项1/2/4及恢复阶段trace；源码尚待根任务review，当前没有任何执行结果。cold cache模拟明确不是完整进程重启，不能因此关闭缺项3。

### runtime：25文件、92个源码函数

集成：test_api、test_agents、test_agent_bindings、test_agent_versions、test_agent_teams、test_automations、test_resource_decoupling、test_model_snapshot_atomicity、test_model_types、test_model_retry_policy、test_usage、test_work_tasks、test_work_worker、test_work_execution、test_work_budget、test_work_inputs、test_work_pause。

单元：test_resource_runtime、test_embedding_compatibility、test_resource_tools、test_evaluation_metrics、test_workflow_compiler、test_workflow_schema、test_browser_skill_work、test_browser_skill_trace。

主要支持：原发布助手维护后创建/执行、主/成员归因、显式版本、普通更新不打断、能力未知拒绝、模型原子读取、调用预算/取消/租约回归。带RUN_LOCAL_SANDBOX门槛的用例默认skip，不宣称真实Work沙箱完成。

### workflows：14文件、60个源码函数

test_workflows、test_workflow_nodes、test_workflow_composition、test_workflow_loops、test_workflow_dify、test_workflow_forms、test_workflow_tasks、test_workflow_task_failures、test_workflow_version_control、test_workflow_version_independent、test_ask_workflows、test_work_workflows、test_external_workflows、test_work_resources。

主要支持：旧版设为当前、草稿/发布版本独立、CAS和不可变历史、显式子流程/API授权版本不漂移、多入口同语义、节点/Schema错误、固定代码与审批、UNKNOWN不重放。模拟外部API调用指应用自己的HTTP接口，不是外部供应商联调。

### index：11文件、45个源码函数

test_knowledge_usage、test_memory_vectors、test_multimodal、test_knowledge_evaluation、test_quality_gate_api、test_rag_evaluation、test_retrieval_evidence、test_rdc_retrieval_policy、test_memory_governance、test_capabilities_api、test_media_review_regressions。

主要支持：文本/记忆/图片兼容、同维不同身份、受控地址迁移证据、查询维度改变的明确降级、检索策略固定、旧实验保留与严格门禁、进行中实验来源变化、视频回包前撤权/取消与普通revision维护。media-review中原有费用回归作为共享调用链补充证据，不扩展为本次所有计费产品验收。

### tools：8文件、22个源码函数

test_rdc_tool_snapshots、test_mcp_wire、test_mcp_writes、test_tool_expansion、test_tool_backpressure、test_tool_lifecycle、test_tool_review_regressions、test_browser_skill_api。

主要支持：MCP热/冷目标与凭据、调用前live接口/授权、操作语义摘要、Skill旧冻结内容、审批轨迹不可替换。Browser Skill API和Runtime为替身/种子数据测试，不启动真实浏览器。stdio实际容器门槛未开启时列skip。

### trace_recovery：2文件、10个源码函数

test_resource_trace、test_resource_recovery。

主要支持：运行实际配置/当前差异、未知旧数据不伪造、队列开始时路由、恢复预览摘要/CAS/阶段、取消/跨owner、旧审批和未知操作不可复用。root的trace用例复用了test_model_routing中的setup/consent等既有helper；未运行本人修改过的model-routing首例作为独立证据。

其中capability-only save涉及本人作者历史实现的共享依赖，独立结论限于root运行追溯的关联结果，不据此关闭RDC-05历史产品验收。

### legacy：1文件、9个源码函数

test_rdc_migration_upgrade。

仅在083与backup独立审查通过、最终冻结后执行。支持真实旧081数据升级、业务定义/凭据/审批/checkpoint保留、向量未知状态、旧待审批保持、失败升级事务回滚、旧备份manifest校验。

其中旧通知待投递反例只作为迁移兼容回归记录，不能由本人将其作为RDC-12独立验收通过。RDC-05/11/12仍须另位验收者。

## 4. 当前源码覆盖缺项/需补证据

以下是准备阶段的源码覆盖观察，不是已发现运行失败，也没有通过执行来确认缺陷。根任务应在开始信号前决定补测试、另设专项或明确列为未覆盖；不能等242个源码函数都绿后默认这些已经覆盖。

1. **RDC-02团队发布后成员模型普通维护的端到端成对反例**：现有团队用例覆盖版本、撤销、并发预算、非聊天模型，但未在已识别源码中找到明确的“父子都已发布后只改成员模型地址/key/参数、旧父版本实际委派成功，并从运行trace分别验证父/成员真实修订”的完整用例。需补受控模型与trace断言。
2. **RDC-04持久Ask/Work队列的路由刷新矩阵**：root的queued-route用例明确拦住inline Runtime.start再启动；不能自动代表AskWorker和WorkEngine消费者。需明确覆盖这两个实际消费者在入队后、首次调用前改变路由的反例。
3. **RDC-04跨进程恢复完成链路**：resource_runtime单元测试覆盖冷缓存凭据条件，resource_recovery集成覆盖真实checkpoint和新阶段封存；尚不等于进程退出/新进程恢复后由真实消费者完成后续步骤且已完成写操作仍恰好一次。真实沙箱专项未开启时，至少用进程级受控执行器补证据。
4. **RDC-06多成员/多模型运行trace闭环**：团队台账归因与普通trace分别有断言，准备清单中未确认有一条完整的trace HTTP查询逐项断言父/成员/嵌套/恢复阶段资源的用例。需要避免只证明model_usage存在就宣称详情完整。
5. **RDC-08视频多帧的同一运行配置固定**：现有media-review回包前检查覆盖单帧/普通revision；实际视频解码与多帧循环是另一层。新run采用新配置、同job多帧维护期间不换向量身份/目标，仍需受控process级或获准本地镜像专项验证。
6. **RDC-14实际页面与交互**：本runner只验证后端；既有5个新增UI用例仅源码/发现检查，不运行。窄屏、键盘焦点、错误定位、历史→原运行返回、编辑保留和只读入口均不得计通过。3100/3131页面先前被拒，不能换工具/端口绕过。
7. **真实供应商效果**：替身不能证明外部模型能力、向量输出等价、实际MCP/ACP协议部署或真实浏览器登录环境。保持暂缓，另列授权和验收。

## 5. 本地Sandbox独立专项候选（当前关闭）

RUN_LOCAL_SANDBOX默认未开启；准备阶段未探测或启动服务，不读取manager.key。可候选：test_work_recovery_real、test_work_backend_real、test_work_execution中的真实worker、test_agent_teams中的Work团队、test_memory_vectors中的Work语义召回、test_workflow_tasks中的固定代码流程、test_tool_expansion中的stdio/ACP、test_expansion_video_native中的获准媒体镜像。

是否开启需根任务另明确；先核对本机服务、受控镜像摘要、工作区隔离、预算和清理范围。即使开启也不授权真实供应商或此前被拒的页面访问。本主runner不提供偷偷开启该开关的选项。

## 6. 获准开始后的操作顺序（现在不执行）

1. 记录根任务开始信号、冻结SHA、083/backup及全部阻断复审结论。
2. 冻结代码后仅重新生成manifest，填写独立start-gate文件：root_start_signal_received/all_blocking_reviews_closed/migration_083_review_passed/backup_review_passed必须均为true；scope为runtime-index-root-backend；freeze_sha及两个manifest摘要须与冻结一致。模板保持false不变，不用模板伪装授权。
3. 在services/api工作目录，用既有.venv Python运行runner，参数为 `--run --gate <根任务准备的gate路径> --credentials <本机数据库credential-source路径>`。可用--group逐组执行；未选择则按acceptance/runtime/workflows/index/tools/trace_recovery/legacy顺序，每组独立DB和数据目录。
4. 查看每组日志、失败、skip原因和目标调用证据；遇到失败先确定实现/用例/环境问题，修复后重新冻结、复审并重跑受影响范围。禁止为通过而跳过关键断言。
5. 按RDC编号写独立QA结论，明确本人作者范围排除、缺项、外部服务/页面/沙箱未验收及数据库回收结果。任何passed_with_unverified_skips都不等于整组产品验收通过。

当前状态仍为准备完成、等待根任务开始信号。没有QA通过数或新的部署结论。


## 补充构建隔离守卫

根任务发现默认apps/web/.next与3100已运行服务共用后，明确禁止再用默认.next做开发构建验证。主runner不执行任何前端构建或页面操作，并在子进程显式设置独立PLATFORM_WEB_DIST_DIR。后续如另行授权构建，必须使用独立dist目录或独立source，禁止复制新UI到3100；不能通过拉取此前被拒页面来验证恢复。
