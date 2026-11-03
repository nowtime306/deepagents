# 知行平台扩展交付与验收记录

2026-11-02，分支onwer_agent。**本批本机工程、独立审查、验收与部署完成，当前已知阻塞缺陷闭环。** 完整后端596通过；后续两个补丁按各自冻结范围另行审查和定向复测，不冒称较早596是最终补丁版本的再次全量。默认页面101通过/2隐藏跳过，另3项原生串行流程及中文备份提示通过。真实外部效果和生产多机边界仍按文末清单待办，不作“绝无Bug”或全面HA保证。保留全部未提交改动，不提交推送。

## 需求与团队交付

- [统一系统需求1.1](system-requirements.md)保留上一SYS批次事实并纳入[15项扩展需求](platform-expansion-requirements.md)，不再把已授权的SSO/MCP等按旧SYS-081排除。
- 产品：官方GitHub/文档调研、中文[交互Demo](../apps/web/public/demos/platform-expansion.html)和[逐项覆盖核对](platform-expansion-coverage.md)。Demo数据为样例，不连接真实接口。
- 开发：[阶段排期与实施](platform-expansion-development.md)、身份/工具/媒体费用专题开发记录，071～078新增迁移与中文入口；原六类数据及密钥保留。
- 测试：[67条扩展用例及执行记录](platform-expansion-test-cases.md)与[缺陷清单](platform-expansion-bugs.md)，原SYS用例继续回归。
- 运维：[部署与监测说明](platform-expansion-operations.md)、主系统部署/监测启停检查、媒体镜像、同机单写主备与有界容量探针。

团队分别使用.local/team-workspace/platform-expansion中的职责目录，源码按文件所有权交接。开发自验→独立审查→修复复审通过→QA，测试发现新Bug也按同样流程重新交回开发；没有用作者自验代替独立审查。详见[审查总账](platform-expansion-code-review.md)及各专题报告。

## 已实现并有模块证据的工程范围

- 系统监测：持久采样与保留、窗口运行/Token/分币种费用、独立累计区、实例存活/准入就绪、真实运行趋势与归属受限错误详情、规则/事件/冷却和确认不恢复；独立凭证导出与Prometheus/Grafana配置。
- 身份：标准OIDC显式绑定、TOTP/一次恢复码、验证联系方式和异步找回渠道；登录202挑战与重新开始、来源列表新增/编辑/搜索/状态筛选，密钥留空保留。
- 工具：旧SSE、受管stdio/ACP、按账号MCP OAuth与刷新/撤销；审批/参数固定/未知不重放，协议子树与背压清理。
- 知识与模型：图搜图、受限视频抽帧与时间标签、私有向量一致性、有限模型四态矩阵、人工引用/拒答标签和真实50题发布门禁。独立语音合成/图片理解导航仍隐藏。
- 费用与运维：按次/图片/秒额外计量、金额预留/未知核对、不可变价格版本、CSV映射与账期对账；批准媒体镜像、同机单writer/维护围栏及持续health负载、数据规模和真实API内存报告。它们不等于业务容量或物理HA认证。

模块证据分别记录，不将64、60、25等定向数字相加为一次全量。最终前端补丁独立审3项与QA3项通过；候选生产构建通过，TS及全API/tests Ruff通过，完整前端ESLint0错误、38条既有警告。

## 首轮失败与修复轨迹

1. 独立审查实际发现并修复身份issuer/绑定/找回与维护准入、MCP授权竞争与协议边界、监测授权、质量发布竞态、媒体结算/撤权/账期、运维跨库锁/超时清理等问题；全部保留原问题编号与复审结果。
2. QA新增BUG-EXP-004：成功协议退出后残留后代；开发修复后，复审又发现取消写管道可能阻塞，补非阻塞发送与期限。实际生命周期、无关进程保留、Work取消/强制销毁及UNKNOWN不重放复测通过，004关闭。
3. QA新增BUG-EXP-005：累计用量不能替代所选窗口。补窗口统计后，复审发现Decimal金额编码精度损失，按原账本字符串契约修复；实际多窗口/迟到费用/未知/多币种/9位小数及页面复测通过，005关闭。
4. 开发初轮全量565通过/2失败，修正测试全局来源前提和随机库名守卫，原顺序22项复验通过；不拼为567全过。
5. QA完整首轮**589通过、2失败、0跳过，1336.31秒**：Work第二次断连后恢复未完成，Ops停止后就绪判断失败，原记录保留。Ops改为心跳加同DB准入锁并增加ghost回归，未放宽60/35秒或删除强断连、重启和双worker断言；独立2项及QA2项通过，原失败时刻的锁状态无法回溯。
6. Work未按flaky处理：连续压力第8轮捕获44个await快照，证实reaper取消被依赖吞掉/转异常后继续循环，使main等待退出。仅在原循环增加取消状态传播；4个确定性回归、修后连续8轮及正式原场景自验、独立7项与QA原场景通过。详见[修复证据](work-worker-cancellation-fix.md)。

## 完整回归、部署与数据保护

- 完整后端第二轮：新随机_test数据库、独立TEST_DATA_DIR、真实本机执行环境，**596通过、0失败、0跳过，1292.33秒，退出0**。运行前后摘要一致，测试库精确删除；日志qa/full-backend-second.log。这是单轮完整结果，不是首轮589加定向结果。该冻结版本早于BUG-EXP-006内部回调配置增量。
- 页面：默认103条/32文件已完成逐项去重，**101通过、2按要求隐藏跳过**；其中61条mock-only、1条unit、39条实际API/本机协议或局部页面替身，不声称101条全是真实端到端。详见[逐项台账](platform-expansion-page-ledger.md)。真实页面使用独立API8139/Web3112生产构建、独立账号/数据库/密钥目录及本机模型替身。Git与浏览器原生各1项及检查/清理通过；另默认排除的审批准备1项→只重启本站四后台→恢复1项均通过，计数器及完成事件各1、模型0，未重复执行。
- 主系统：已审scripts/deploy-platform.ps1实际执行成功，2026-11-02T09:59:40Z记录deployed/complete；迁移071～078应用。独立check-platform.ps1通过：API准入就绪、Web生产就绪、执行环境鉴权及镜像就绪、API/Ask/Work/Ops四守护存活、迁移最新。首次部署不包含BUG-EXP-006；其后按下节单独发布该后端增量。
- 监控：start-monitoring.ps1与check-monitoring.ps1实际退出0；授权抓取up、规则载入、Grafana强制登录。真实浏览器登录后中文14面板标题全部渲染、JS错误0。看板http://localhost:3300/d/owner-agent-platform；不公开凭据，不开放Prometheus宿主端口。证据operations/grafana-ui-verification.json。
- 恢复：对部署前备份20261102T095835Z-74897b2c执行隔离verify，records_match=true、files_verified=39、credentials_decryptable=3；临时恢复库精确删除。只做隔离恢复验证，没有覆盖在线库。证据operations/restore-verify-final.log。
- 数据：原核心六类ID摘要及model.key在部署后均保留。用户确认北京时间18:02～18:05删除原“测试”工作流、调整知识绑定/重排为本人操作，要求保持当前状态；没有回滚。原快照保留供追溯，并另存platform-expansion-confirmed-before.json与platform-expansion-resources-confirmed-before.json作为后续保护基线，确认记录operations/confirmed-user-state.json。不可用旧快照覆盖用户新操作。

### 内部回调隔离增量

真实隔离站点发现BUG-EXP-006：内部工具原固定8100，隔离租约被生产403拒绝，未成功写入。四条调用链现统一使用受校验的PLATFORM_INTERNAL_API_URL，仅允许明确端口的HTTP本机IP地址，客户端禁止代理/重定向；默认8100兼容。开发40项及另一新随机库独立40项通过，详见[审查](internal-api-routing-review.md)。QA统一本站配置重启8139站点后，原创建自动任务真实页面1项通过10.5秒；实际API及独立Ask/Work执行器的知识检索、Work已发布工作流调用和状态轮询也通过。本站工具序列和资源ID单独留存，internal-routing-real.log记录通过，均只调用本机模型替身；不等同真实供应商已验。

以原失败场景复测为增量放行门槛，已独立审查的BackendOnly脚本实际于2026-11-02T11:03:42Z记录deployed/complete，scope=backend-only、web=unchanged；未重建或重启Web。先为当前数据生成20261102T110312Z-643d72fa备份并校验，再加载修复后的API/Ask/Work/Ops。另行check-platform及check-monitoring均退出0，四后台就绪、迁移最新、授权scrape up、Grafana强制登录。日志operations/deploy-backend-patch.log。

补丁前后以同一明确JSON摘要格式核对21类记录及model.key，全部保持；包括用户当前工作流0、知识绑定/重排、139个分块、3个模型、11会话/97消息、19运行/48用量。证据operations/patch-before-canonical.json及patch-after-preservation.json；只保存摘要，不输出正文/凭据。该核对保护当前状态，不回滚到用户删除前快照。更早legacy摘要诊断的task_runs/model_usage序列化未能匹配，不拿该格式推测结果作数据变化结论；本次更新使用同格式前后直接比对。

### 备份实例边界及最终发布

QA只读追踪发现BUG-EXP-007：内置服务控制脚本/API PID为固定持久部署，而非默认profile没有在控制前明确拒绝；后置owner_agent数据库守卫不足以防止先行控制。未触发有风险job、未误停生产。作者增加固定部署文件、Settings与继承环境一致性及副作用前拒绝；不匹配job明确FAILED/ADMISSION，不留在PENDING或进入维护。开发18项、另一个随机库独立18项、QA新随机库18项分别通过，不相加为一次全量。本站实际POST拒绝、attempt=0、maintenance=false、生产/本站实例与stop/control摘要不变。详见[独立审查](managed-service-profile-review.md)。

最终完整部署于2026-11-02T11:39:19Z完成：新备份20261102T113819Z-9373feae，scope=full、web=rebuilt，中文错误提示随生产构建上线。Ruff全app/tests、最终TypeScript及全前端ESLint均通过；ESLint仍有38条既有警告、0错误。新生产构建额外中文reason页面1项通过1.3秒，所有API显式mock、不真实登录或写入，和真实负向API证据分别保留。

默认排除的第三条原生备份页面也已实际运行：**1通过，49.9秒，退出0**。原spec仅更改两处证据输出路径，150秒poll/180秒总超时及全部业务断言保持。先核固定profile、默认数据路径、owner_agent及idle，再在正式站点排队唯一新job；20261102T114245Z-875e1c0c备份完成，记录一致、39文件及3份加密凭据通过，源码归档存在/working_copy=true/分支onwer_agent/超过100文件，四后台恢复。只在新恢复库验证后删除临时库，没有恢复覆盖在线库，也没改定时备份设置或用户资料。证据qa/production-backup-native.log、production-backup-job.json、production-backup-accepted.png。

执行器首次因Windows默认Proactor不兼容psycopg在前置检查拒绝，未访问浏览器或提交job；按安装库和项目现行运行方式改Selector事件循环，经独立复审后重试才通过。保留production-backup-preflight-first.txt，不删除原失败或放宽业务断言。

原生验收之后另行check-platform与check-monitoring均退出0，API/Web生产就绪、四后台存活、执行环境鉴权/镜像正常、迁移最新、授权抓取up及Grafana需登录。最终发布和备份前后21类记录及model.key摘要**全部保持**，见operations/final-release-preservation.json。用户确认的工作流删除与知识配置保留。QA自己的四后台、3112网页、8138替身、随机测试数据库/目录与生成凭据已按确切归属清理，原生Git/Browser剩余容器0，证据保留；不清理用户生产资源。

## 外部接入边界

[外部接入清单](platform-expansion-external-checklist.md)列明真实模型/IdP/邮件短信/业务MCP与ACP/供应商账单、真实图视频相似度与至少50道业务题、第二主机及故障域。真实外部模型按用户要求继续跳过；工程替身通过不等于真实效果通过。可在本机完成的监控栈部署不能因缺外部模型凭证而跳过。
