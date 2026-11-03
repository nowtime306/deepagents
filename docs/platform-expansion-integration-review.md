# 平台扩展集成独立代码审查

2026-11-02，审查expansion_review_identity；业务作者主协调与对应模块作者。范围为本轮冻结的main身份/媒体生命周期、vault探测、权限映射、config、maintenance、backup扩展表/恢复隔离、KB删除保护和deployment_gate，以及对应页面接线。未修改业务源码、启动生产或调用真实外部服务。

## 当前结论

**本次已冻结集成子集通过，可用于后续QA准备；不构成整站交付/部署放行。** R-INT001路径问题由媒体作者在复现期间修复，已独立验证拒绝危险路径。媒体作者其他问题需其独立审查者继续复审。随后新增077实例metadata、monitoring_details及其main/fence/前端挂载不属于本报告通过范围，必须另审。EXP010/060产品缺口未完成前不能按完整产品验收通过。

身份模块R-ID001～007第三轮通过，核心R-CORE001～005第二轮通过，分别见对应独立报告；不能用本集成报告取代那两份问题历史。

## R-INT001 / P2：知识库删除仅检查叶目录，漏掉祖先junction

- 首次静态位置：`services/api/app/capability_api.py:207`由video_directory得到job目录后，只检查该目录与其后代是否link/junction。原`media_retrieval_video.directory`仅拼接data_dir/knowledge-videos/UUID，没有检查祖先。
- 触发：knowledge-videos本身是Windows junction，其下UUID目录是普通目录；叶目录及后代均不是junction，原删除循环可以沿祖先重定向处理实例目录外文件。需要本机文件路径被替换的前提，不是声称普通上传文件名可远程制造junction。
- 处理：已即时告知主协调，由媒体作者新增`private_path`，检查路径及全部祖先的symlink/junction，并核对resolve containment；KB删除继续复用同一directory helper。
- 真实复验：在本轮独立TEST_DATA_DIR创建alternate instance和受控outside目录，只给alternate/knowledge-videos建立junction，写入本轮marker；实际DELETE知识库返回409、`MEDIA_PATH_UNSAFE`，outside marker保留。清理只移除本轮junction本身，没有递归删除junction目标。
- 证据边界：作者在实际API探针执行前已提交修复，因此没有实测旧版本200或误删，不伪造该证据。第一次探针是PowerShell参数传递错误，未进入产品路径；第二次仍断言旧错误行为而实际409，证明代码已变化；第三次改为正确预期后通过。前两次日志分别保留integration-harness-first.log、integration-repair-raced-probe.log。本项当前关闭。

## 集成核对

- main：新身份三router、个人安全与管理员入口接线；MFA202在创建session之前返回；独立密码验证挑战成功前无完整会话。OIDC GET callback自行共享GATE维护保护；中文重新登录清父/子挑战。全部identity密文与outbox、MCP OAuth client/flow/grant列纳入缺密钥保护。
- 生命周期：identity投递在迁移/vault后启动、pool关闭前停止；media worker通过实例设置显式启动，默认直接构造的测试Settings不启用；环境生产入口读取MEDIA_WORKER_ENABLED，恢复配置明确false。媒体worker内部执行/资源安全由媒体模块独立审查覆盖，本报告不复述为全部通过。
- 权限：监测全局读及规则管理分开、事件操作后端独立校验；quality走知识库权限与owner；image-query检索走knowledge.search；tool-oauth走tools.use；独立媒体导航保持隐藏。页面入口只是辅助，不能取代服务端权限。
- 维护与部署：identity sending小写、视频holds_memory/RUNNING/UNKNOWN进入busy判定；optional表在旧schema升级前允许不存在。deployment_gate持共享准入的排他锁，活动操作拒绝部署，保留持久PENDING不当作已执行；失锁拒绝，不自动重新取得锁后重放。
- 备份：新增表计数可兼容迁移前缺表，manifest已声明表恢复时必须存在；新增身份/OAuth密文实际执行vault解密验证。knowledge-videos进入data目录集合。保留恢复副本时关闭企业来源/渠道/告警规则，清临时身份/OAuth流程、撤销全部session/API credential，视频在途任务取消并清原container/image/create标识，独立namespace及恢复配置不启动media/work。凭据保留与是否允许外部活动分开，没有重新加密覆盖原密钥。
- 指标采集token：独立新增API用例在同一测试app切换两个实例data_dir，实例A token正确200；实例B未配置503，配置B后A token403/B token200，证明没有回退到源码或另一个实例凭证。不读取真实token。

## 独立验证及限制

先在全新随机owner_agent_core_review_*_test、独立TEST_DATA_DIR运行核心18项：18通过16.88秒。随后加入部署门闸、维护和上述两个独立集成探针，最终**37项通过16.37秒，无跳过**；运行脚本finally只删除本轮随机库，未访问生产数据。日志：`.local/team-workspace/platform-expansion/review/core/independent-integration-review.log`；独立用例同目录`test_independent_integration_review.py`。身份另轮36项通过57.90秒，报告独立记录。

本轮未做生产pg_dump/pg_restore或真实外部身份/邮件/短信/模型测试；backup恢复隔离用随机库事务内受控数据测试，实际停启/恢复演练需QA及运维后续执行。没有将静态接线核对称作完整浏览器验收，没有保证零bug，也没有把尚待开发的EXP010/060新模块混入通过范围。

## 监测详情新增模块独立审查（2026-11-02）

本次另行审查冻结的077_instance_metadata、monitoring_details、service_fence.heartbeat元数据附加写入、main新增router、backup新增表名、monitoring-details中文页面/dashboard挂载/CSS、Next distDir可选配置及对应测试。**此新增范围审查通过，可交QA；未发现本轮可确认的新增阻塞缺陷。** 原模块问题历史不改写，不能将“未发现”表述为零bug或整站完成。

### 功能与安全核对

- instances只允许monitoring.read/管理员。存活来自同节点进程存在观测，远端或不可探测明确null；就绪来自15秒心跳与该服务实例实际所需advisory锁，不伪称HTTP探测。锁查询限定current_database、advisory/classid/objsubid/granted、backend PID与锁模式；同号锁在其他DB不能误报ready。
- 元数据由服务专用连接心跳写入，kind作为角色，node/故障域/release显式显示；故障域与release分别为环境声明标签及默认dev值，不能当成已验证物理高可用或Git版本证据。旧无metadata记录以null/未知呈现；077是独立新增表，backup可选计数兼容迁移前schema，manifest声明后仍要求存在。
- run-trend按真实创建时间在1h/24h/7d范围分桶，成功/失败是当前记录状态，页面准确说明空桶语义。独立窗口用例插入当前、2小时前、8天前受控记录，实际三种窗口增量为1/2/2，未把过窗记录计入。没有把CPU历史当运行次数。
- errors只展示50条，限制窗口并按created_at/id稳定排序；错误字段不回传正文/owner，非代码格式归UNCLASSIFIED。他人详情链接始终关闭，本人也需原chat/work/workflows.read权限；原详情接口仍独立鉴权。Ask/Work复用RunDiagnostics，workflow原事件API只在页面呈现node/event/time，没有渲染data正文。
- 页面加载失败清旧数据显示错误/重试，不把未采集当正常；实例live/ready/stale独立展示，重复刷新由卸载标志防止陈旧响应回写。生产默认distDir仍.next，仅明确配置候选时变更；未替换生产3100服务。

### 独立执行证据

全新随机owner_agent_core_review_*_test及独立TEST_DATA_DIR：**22项通过20.30秒，无跳过**，包含原core18、作者details2及本审查新增2项。跨库锁用例另外创建独立owner_agent_monitor_lock_*_test，真实持有相同advisory锁/外库PID，确认不能让本库实例ready。finally仅删除本轮两类随机库。日志`.local/team-workspace/platform-expansion/review/core/independent-monitoring-details-review.log`；新增独立用例`test_independent_monitoring_details.py`。

真实Chrome访问已运行的3101候选、所有API响应替身、独立输出目录：**2项通过2.2秒**。覆盖新存活/准入区分、运行趋势桶、workflow下钻空事件、他人详情关闭、确认告警与恢复区别、移动宽度和采集失败重试。日志`independent-monitoring-details-browser.log`，产物`browser-details-artifacts`。这不等同真实API端到端页面验收；后端独立真实数据库用例与该UI替身验证分别记录，未冒称真实外部服务联调。

本轮未启停/部署生产、未做外部真实模型或物理跨主机故障演练。EXP010/060新增展示逻辑可以交独立QA按完整产品用例验收；物理故障域/真实外部接入仍需资源接入后另验。其他模块重开的缺陷不因本次详情通过而自动关闭。

## BUG-EXP005窗口修复独立审查（2026-11-02，待修复）

冻结范围：monitoring.window_totals、monitoring_api.dashboard的summary.window、078 created_at索引、首屏窗口卡片和测试；同时复核两项全量失败的测试隔离修复。

**本轮暂不通过，不交QA005复测：分窗功能已验证，但新增R-INT002金额序列化精度问题必须修复。**

### 已验证正确部分

- from含、to不含，使用UTC边界；Token与额外费用按model_usage原调用created_at归属，extra_cost_usage迟到日期不会把费用移入错误窗口。
- costs按原币种分组、不汇率合并；unknown_cost_calls按call_id distinct，多个未知项目仍是一次未知调用；空窗口可确认0，有调用未报告保留null/未知次数。
- 独立多币种探针包含同一call的CNY Token、CNY image、USD audio及另一call的三项未知；确认CNY=3、USD=4且distinct未知1，按币种unknown也各1。
- 首屏切换24h实际请求与Token/费用卡片对应；原Prometheus与累计快照契约不改，078只增created_at索引。
- test_identity_security仍验证管理403、未开通503、无效重置401，只是明确建立未配置前提并finally恢复先前enabled来源/渠道；独立另造已启用provider，调用该用例后仍enabled=true。test_service_fence只将旧固定dbname守卫改为解析dbname并严格owner_agent_*_test，生产owner_agent不能通过；失锁后永久fence、健康pool不准入等业务断言完整保留。

### R-INT002 / P2：新窗口金额经默认Decimal→float编码改变账本数值

- 位置：`services/api/app/monitoring.py:75` window_totals返回数据库Decimal；`services/api/app/monitoring_api.py:52`直接返回window，未用既有collect的Decimal字符串编码；中文卡片直接呈现estimated_cost。
- 触发与真实独立证据：新随机测试库插入受控model_usage USD `1234567890123.123456789`，实际GET `/api/v1/operations/monitoring?hours=1`返回window.costs为`1234567890123.1235`。金额在schema numeric(24,9)合法范围内，API将精确十进制转换为二进制浮点，精度已损失；这不是汇率或显示位数策略。
- 影响：新窗口与既有精确账本/累计值可能不同，且前端已声明该字段为字符串；不能把统计页面估算标识当成允许改变已存估算金额的理由。
- 要求：沿用collect的`jsonable_encoder(custom_encoder={Decimal:str})`或等价的金额字符串契约保留原精度；真实API精度回归应比较解析后的十进制与原账本，而非只测内部SQL Decimal。
- 探针：review/core/test_window_review.py::test_window_api_preserves_exact_ledger_decimal；日志window005-decimal-probe.log，**1失败8.25秒**。此用例断言正确行为，修复后应直接通过，不降低精度断言。

### 独立执行记录

新随机owner_agent_core_review_*_test与独立TEST_DATA_DIR，本轮功能/隔离/失锁定向 **23项通过23.35秒**，无跳过；另真实Chrome3101候选、全API替身 **2项通过2.3秒**，24h呈现2,400/240及CNY24。日志independent-window005-review.log及independent-window005-browser.log，独立产物browser-window005-artifacts。此前自写多币种fixture误用audio单位而非audio_second、未知quantity未配null，首轮21pass/1fixture失败尚未进入查询；已修正并保留window005-review-fixture-first.log，不记作产品缺陷。

以上通过不能覆盖R-INT002失败。所有库均finally精确删除，无生产/真实外部调用、无业务代码修改、无提交推送。根作者已接收002，修复自验冻结后独立复审，再交QA。

## BUG-EXP005 / R-INT002修复复审（2026-11-02）

**本修复范围通过，R-INT002关闭；可交QA对BUG-EXP005复测。** 原初审失败证据保留，不能用本节覆盖其他模块未关闭问题或替代完整系统回归。

根作者在dashboard.window使用与collect相同的jsonable_encoder及Decimal→字符串编码，未改窗口归属、币种分组、未知去重或累计指标算法。前端已接受字符串，Token数值和费用均保留可用类型。新增作者用例实际走GET，对窗口原USD总额加受控大额9位小数后比较完整字符串，finally清理自身call；没有只在SQL内部断言精度。

独立使用又一个新随机owner_agent_core_review_*_test和独立TEST_DATA_DIR，保持首审精度探针`Decimal(str(API amount)) == 原exact`不变：**25项通过22.34秒，无跳过**。包括原23项、作者新增真实API精度用例和原独立精度探针；账本`1234567890123.123456789`现从真实API完整返回，未再变为`1234567890123.1235`。库由finally仅删除本轮随机目标。日志`.local/team-workspace/platform-expansion/review/core/independent-window005-rereview.log`，原失败window005-decimal-probe.log仍保留。

此次修复未改UI，沿用同冻结UI独立Chrome2项2.3秒通过证据，不为凑数重复执行。主协调的全量565pass/2fail仅是历史开发回归；本轮两项测试隔离修复已独立验证，但不能据此宣称全量回归最终全通过。没有部署生产/应用生产迁移或调用真实外部服务。

## 最后前端补丁独立审查（2026-11-02）

范围：IdentityAdmin客户端认证来源搜索/状态组合过滤与空态，monitor-cards强金额文本换行，候选构建目录Git/ESLint忽略，以及workspace_backup测试三处断行。**该冻结前端范围通过，可交QA定向补验并重算前端manifest；未发现本轮可确认新增阻塞问题。** API、权限、迁移、生产服务均未修改或部署。

### 代码核对

- 搜索仅对名称/Issuer本地trim和不区分大小写匹配，和all/enabled/disabled状态条件相交；两个onChange只setState，没有后台保存/写请求。它不修改items、来源enabled或管理员权限，原编辑/启停仍使用原服务端鉴权接口。空态区分无来源与筛选无结果，新增/编辑入口保持。
- 筛选是组件局部状态，没有写localStorage/配置。实际刷新后搜索为空、状态all，原列表完整恢复。编辑窗口沿用原ResourceDialog和Issuer不可变约束，关闭未提交别名后列表/再次编辑仍是原名称。
- CSS只给金额strong增加overflow-wrap:anywhere，未Number/parseFloat/截断金额字符串；合法numeric最大金额USD 999999999999999.999999999在390px仍完整可见，文档宽度未超出视口，精度不变。
- .next-*/仅忽略候选生成目录；ESLint相应.next-*没有扩大到src。用项目既有TypeScript6预载器实际调用ESLint.isPathIgnored：identity源码false，.next-review-generated/types/routes.d.ts为true；git check-ignore确认该候选缓存被忽略。首次直接加载ESLint未启用已有TS6预载器而被TS7兼容保护拒绝，不记成产品失败或声称全ESLint已跑完。
- test_workspace_backup只把长表达式/查询和复合断言断行，原恢复凭据保留、视频取消/标识清除、schema可选与manifest缺表失败断言仍在，没有删除或放宽；后端冻结回归由并行测试继续，本次不重复执行全后端。

### 独立页面验证

独立Chrome访问3101候选，所有API请求替身，不访问生产3100或真实账号；独立输出目录review/ui-final。**3项通过3.0秒**：作者两项页面用例独立执行，另外新增审查用例覆盖trim/case+启用/停用组合、空态、Issuer禁改、未提交编辑关闭/重开、刷新不保留筛选，以及全过程API非GET/HEAD/OPTIONS写请求计数严格为0。

日志`.local/team-workspace/platform-expansion/review/ui-final/independent-ui-final.log`，独立测试同目录independent-ui-final.spec.ts。新增探针首轮使用Issuer精确标签而标签含帮助文字，定位失败，未进入对应断言；改为匹配真实可访问标签后通过，首轮review-selector-first.log保留，不作为产品缺陷。未修改业务代码、未提交推送。

此通过只对上述冻结前端补丁负责，不替代整站QA、真实外部接入或最终部署审批/检查；其他模块已有结论与未完成验证继续独立记录。

## 服务守护测试就绪helper修复独立审查（2026-11-02）

范围仅`tests/integration_tests/test_service_host.py`中active_backend、Work在线计数及新增ghost回归；无产品代码、超时或真实服务部署变更。**当前helper修复范围审查通过，可交QA对Ops失败定向复测。Work第二次killall后60秒未出现新实例的原失败继续待诊断，本结论不关闭它。**

### 是否掩盖真实服务问题

原helper仅看5秒内登记心跳，登记行可以在失锁/退出或清理失败后短暂存在，不能仅据此判断后台已就绪。新active_backend同时要求fresh、当前数据库、backend PID、granted advisory锁及准确模式：API710014独占、Ops710033独占、Ask/Work710017共享。逐一核对实际服务入口ServiceFence持锁与注册路径相符。Work在线计数也要求对应backend持有granted ShareLock，不能把刚注册但未获准入的行当第二Worker。

这增加启动/重启的正向要求，没有删除真实断连场景：单条专用连接终止、该测试库全部连接终止、不同实例ID、重复host退出1且原实例不被替换、至少2次重启、Work双Worker、stop后host/extra退出0及manifest stopped都仍保留。等待仍120×0.5秒，显式stop等待35秒，未延长60/35秒阈值或删除强断连断言。ServiceHost.stop_child自身等待/有限强停既有子进程的实现也未改；“无准入锁”只代表不就绪，不能单靠它证明任何遗留进程都已退出。

新增ghost用例在实际新鲜Ops登记上明确无710033→notready、持锁→ready、解锁仍fresh→notready。该测试证明心跳与准入定义差别，不是把失败测试改为固定返回false。

### 独立执行与证据边界

新随机owner_agent_host_review_*_test、独立TEST_DATA_DIR/basetemp：Ops完整守护双次断连/唯一host/显式stop＋ghost **2项通过17.12秒，无跳过**。Ops用例内部另建owner_agent_service_*_test且finally仅删除本轮目标；独立runner也finally删除自身测试库。日志`.local/team-workspace/platform-expansion/review/service-host/independent-ops-ghost-review.log`。本轮stop后的非秘密manifest检查显示state=stopped、restarts=2；当前本轮host PID60084和最终child PID48400均不存在。未输出manifest认证token、DSN密码或用户内容，未操作生产服务。

第一次独立执行ghost通过但Ops尚在setup时，因本审查--basetemp父目录未先创建而报FileNotFoundError；修正审查runner后全新库重跑，首轮ops-ghost-harness-first.log保留，该setup错误不算产品失败。

**不能把本轮通过倒推为原589通过/2失败那次Ops一定没有真实故障。** 原QA事后相关PID已不存在，但失败库已删除，无法回溯当时advisory锁、登记与进程退出时序；现有证据支持helper定义确有不足，并证明当前修复后受控Ops场景通过，不足以证明原失败唯一根因。需QA按原场景复测并保存实时锁/PID证据。Work首次单库32.39秒通过同样不能关闭另一次第二killall真实超时；它保持单独诊断和门禁，不借此helper复审替代。

## Work reaper真实取消缺陷修复独立审查（2026-11-02）

范围：`app/work_server.py.reconcile`待取消检查、`test_work_reaper_cancellation.py`四项确定性回归及`docs/work-worker-cancellation-fix.md`根因证据；不改业务、数据库、配额、执行/重放语义或验收超时。**此冻结修复范围独立审查通过，可交QA复测原Work P1；仍需QA原场景和第二轮全量回归，不能直接把589通过/2失败改成全绿。** 前节Work待诊断状态由本节的新证据推进，不改写原失败历史。

### 根因与修复核对

独立读取脱敏await-proof.json，44个快照全部显示主协程等待旧work_server.py:83收尾，reaper已有cancelling=1却在reconcile的sleep。该证据支持本次确有待取消任务继续轮询的真实挂起，不再仅以“单次重跑通过”归类flaky。作者记录的第8轮压力复现及修复后8轮通过202.16秒为开发证据，本审查未冒称独立重复该8轮。

冻结修复只在当前reaper任务已有cancelling计数时传播CancelledError：inventory正常返回后检查，防止进入新的recovery；普通异常分支检查，防止取消被转换后只记日志并继续；recovery正常返回后检查，防止再sleep/下一轮inventory。CancelledError仍是BaseException，不会被当前except Exception吞掉。非取消正常循环与普通故障继续原30秒间隔，未改变恢复任务的选择或幂等性。

四项作者用例按inventory/recovery×吞取消返回/转换普通异常覆盖。用asyncio.wait只观察首个cancel后0.2秒结束，没有用wait_for再次cancel掩盖原问题；严格断言cancelled、inventory只一次，inventory取消时recovery次数0。额外独立两项用例证明未取消的成功循环和普通异常仍进入30秒sleep、任务保持运行，普通异常不执行recovery，随后取消正常睡眠可退出；没有将所有reaper无条件终止。

### 独立执行与清理

全新owner_agent_work_review_*_test、专属TEST_DATA_DIR/basetemp，显式RUN_LOCAL_SANDBOX=1仅使用已授权本机管理服务：正式未经诊断wrapper的原Work单backend断连、全部会话断连、唯一host、至少2次重启、双Worker及显式停止，加4项确定性与2项独立正常循环回归，**7项通过33.16秒，无跳过**。原60秒启动/重启等待和35秒stop时限未改，未延长或删弱原断言。

用例内部另建owner_agent_service_*_test，测试finally精确删除自身DB，runner finally删除本轮外层随机库。结束后独立读本轮两个非秘密manifest：slot0 state stopped/restarts2，slot1 stopped/restarts0；两组host及最终child PID事后均不存在。未停止共享管理服务或生产服务，未输出凭证/用户正文。

日志：`.local/team-workspace/platform-expansion/review/work-reaper/independent-work-reaper-review.log`；独立正常/普通异常用例同目录test_reaper_normal_review.py；运行脚本run_work_reaper_review.py。没有改原确定性断言，没有真实外部模型/业务调用或生产迁移。

本修复处理依赖返回/转换异常后仍有pending cancellation的已确认问题，不宣称能强制终止任意永不返回的第三方I/O，也不替代原服务守护有限进程退出边界。QA需据本节冻结源码复测原P1，并继续原定第二全量回归后再更新整站结论。
