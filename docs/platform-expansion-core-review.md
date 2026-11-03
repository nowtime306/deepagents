# M1核心模块独立代码审查

2026-11-02。作者主协调，独立审查expansion_review_tools。范围：monitoring/monitoring_api、071迁移、大盘和告警UI；quality_gate/076迁移、Agent发布hook和质量UI；模型兼容矩阵、模型设置能力标签及catalog提示；Ops采集调用和监测权限映射，以及对应自验。未修改业务源码、启停生产或调用真实外部服务。

**首轮结论：不通过，以下5项问题修复并独立复审后才可交QA。** 原12项自验独立重跑通过，但未覆盖下面两个已实际复现的权限/并发缺口、UI权限不一致、部署凭证隔离和资源边界问题。首审报告已冻结供作者修复，新增结论由后续复审追加，不改写首轮证据。

## R-CORE-001 / P1：旧系统规则的管理权限可通过改scope绕过

- 位置：`services/api/app/monitoring_api.py:134`～`:154`，尤其`:136`先按body.scope授权，读取previous后未校验旧scope。
- authorize_rule只检查提交后的scope。旧system规则的owner被撤去管理员/monitoring.manage后，提交scope=personal、metric=failure_rate即可修改原系统规则；active事件约束只在已有FIRING时起作用，不能替代权限校验。规则列表can_edit=false也没有保护API。
- 隔离实际复现：管理员创建system/service_offline/work规则→撤去is_admin（保留普通member）→PUT相同id，body为personal/failure_rate、threshold=.3、enabled=false、revision=1→**返回200，原规则成为personal且停用**。预期拒绝旧system规则的无权限修改；UI隐藏按钮不能作为后端授权。
- 建议：在锁定旧规则后同时对旧scope和新scope授权；任一为system均要求monitoring.manage或管理员。补权限撤销后的system→personal、system启停及原个人规则正常编辑回归。

## R-CORE-002 / P1：质量门禁检查与实际发布提交分离，来源变化后仍能发布

- 位置：`services/api/app/agent_versions.py:70`～`:88`；`services/api/app/quality_gate.py:181`～`:194`。
- publication_guard在Agent版本插入事务之外读取policy/实验/人工标注/来源，随后发布事务只重新校验agents.revision。知识库检索配置、文档/模型修订、policy或标注在这两个阶段之间变化，原成功报告仍被保存到新版本，不复核质量证据。知识库变化也不会增加agent.revision。
- 隔离实际复现：准备固定来源、50题user题集和完整正确人工标注，quality/check通过；在publication_guard真正执行并返回后、进入版本插入前暂停发布协程；另一个正常API请求把该知识库keyword_min_coverage改为.4，确认quality/check已不通过；再放行原发布请求→**返回201**。本次暂停仅通过临时内存包装器建立可重复调度点，没有改业务源码或真实模型响应。
- 建议：使发布质量检查、证据固定和版本插入共享可验证的提交边界，并与知识库/规则/标注写入协调锁或修订代际；不能仅把相同的多次独立读取挪进一个无协调的事务后就宣称消除竞态。版本中固定实际使用的来源和标注/规则修订，提交前无法证明一致时409。补来源修订、人工标注从正确改错误、启用/更新policy三种并发场景；旧有效报告不能被用于检查后已失效的版本发布。

## R-CORE-003 / P2：只读系统告警界面仍提供无权限的确认操作

- 位置：`apps/web/src/features/ask/monitoring-dashboard.tsx:11`、`:80`～`:86`；后端`monitoring_api.py:173`起的acknowledge。
- canManage仅控制新增/编辑规则；事件确认按钮只检查acknowledged_at和busy。monitoring.read-only运维用户可看到他人的system事件，因此仍看到可点击“确认告警”，点击会被后端以无权访问拒绝。该Alert类型/API结果没有can_ack，UI不能区分本人personal事件与他人的只读system事件。新增/编辑只读处理不能覆盖事件操作。
- 建议：事件返回按owner/scope/当前权限计算的can_ack，前端据此展示确认或只读状态，后端继续独立授权。不要直接全局禁掉普通账号对本人personal事件的合法确认。补只读系统事件、可管理系统事件、本人personal事件三种页面/接口对照。本项为确定的静态分支检查，未把它冒称独立Chrome验收结果。

## R-CORE-004 / P2：监测凭证按源码目录共用，非默认实例和恢复副本未隔离

- 位置：`services/api/app/monitoring_api.py:24`、metrics函数；部署契约`docs/platform-expansion-development.md:20`及start/check-monitoring脚本和compose挂载。
- TOKEN_FILE按源码仓库根目录固定，完全不参考request.app.state.settings.data_dir或实例配置。同一代码创建两个不同data_dir/数据库的应用时，都接受这一个采集token；隔离恢复副本和测试实例即使没有配置自己的监测凭证，也会继承源码目录中的生产采集凭证。现有测试monkeypatch全局TOKEN_FILE无法验证实例隔离。
- 建议：采集凭证按实例data_dir解析，或提供明确的实例级secret路径；非默认目录无凭证时503且不得回退生产token。运维脚本/挂载/文档同步，默认单机兼容需要明确迁移而非静默继承。补同进程两个app不同data_dir/token、恢复副本缺token以及各自token不能交叉通过的测试。
- 本项为固定路径和调用方的静态确认；未读取实际token内容或向生产出口试探。

## R-CORE-005 / P2：个人规则无限创建放大共享Ops循环和无界列表

- 位置：`monitoring_api.py`的rules/add_rule；`monitoring.py:tick`的全规则FOR UPDATE与逐条metric_value；`operations_worker.py:serve`在备份调度前等待safe_tick。
- 任意普通账号可持续新增personal规则，无数量/启用数上限；规则列表一次读取全部结果。每轮tick锁定并遍历所有启用/活跃规则，个人budget_ratio规则还逐条执行预算和用量查询；整个采样在单个长事务内、共享Ops备份协调循环之前执行。一个账号可持续扩大列表响应、数据库锁持有时间和每轮采样工作量，拖延其他用户告警及备份；仅列表前端分页不能限制后台成本。
- 建议：最小实现是按账号/系统规则设置明确的有限数量和启用数，新增时用数据库锁或约束保证并发不能越限；规则列表提供稳定键集分页且UI可加载后续页。若选择批处理采样，须明确每条规则采集时间/缺口而不能把未处理当恢复。补并发上限、跨账号计数隔离、分页无重复/漏项和达到上限后Ops仍可推进测试。
- 本项为已确定的无界代码路径和共享执行依赖；未对真实环境进行大量创建或破坏性负载试验。

## 独立执行证据

新建随机`owner_agent_review_core_<8hex>_test`数据库和独立`services/api/.local/test-review-core-<8hex>`目录，PYTHONNOUSERSITE=1。独立执行test_monitoring.py（5）、test_quality_gate_api.py（2）、unit test_quality_gate.py（4）及原test_agent_versions.py（1）：**12 passed in 9.54s，无跳过**。之后在同次独立测试环境中使用实际API复现R-CORE-001及R-CORE-002，结果分别为200和201。所有题集、答案和标注均为明确的本机受控审查fixture，不代表真实业务检索验收。测试结束精确删除本次创建的随机测试数据库；未读取生产正文或输出连接凭证。

## 已核验边界与后续QA

- 指标采样只读取聚合元数据和主机容量，不读取对话/文件正文；费用Decimal在持久snapshot中转为字符串，未知费用与缺失资源未直接填0。导出器有独立token，不把普通登录权限当抓取凭证；labels限定为固定低基数值。
- 告警持续时间、单个FIRING唯一约束、确认不恢复、unknown不假恢复、冷却和7天sample保留均有真实DB回归通过。事件使用first_seen_at/id稳定键集分页，非offset。新增/编辑规则revision冲突和活跃告警不能改判定口径的保护存在，但不能补偿R-CORE-001旧scope缺失授权。
- 质量门禁区分人工引用/拒答与检索命中；缺少完整标注、缺少无答案样本、合成题、结果题号不符及顺序执行的来源漂移均不能通过。此次失败集中在发布时的并发提交边界，不建议删除这些已有检查。
- 模型四态按有限工程协议声明，明确真实供应商未联调，视频仅DashScope兼容输入等条件与当前调用入口一致；未发现把所有十类配置枚举一律宣称可执行。独立语音/图片理解页面继续隐藏，保留鉴权API提示。
- 本次不重复作者Chrome/前端构建证据，不等于全量PRD完成。服务live/ready分别展示、运行趋势下钻等EXP-010完整产品验收，以及最后main/身份/媒体/备份/HA集成不属于本次已冻结核心子集，仍需最终集成审查和QA明确验证，不能用本报告代替。

作者修复、自验后需重新冻结提交独立复审；结论通过前不得交测试角色验收。

## 第二轮独立复审（2026-11-02）

审查人expansion_review_identity，对上述原五项及冻结修复独立复审。**R-CORE-001～005在已冻结核心范围通过，可交QA；新功能需求和最终集成另行审查。**

- 001：edit_rule锁定旧规则后同时核对旧system权限，新scope仍先授权；降权owner不能通过转personal停用旧系统规则，独立真实API回归403。
- 002：publication_guard改为读取发布事务自身connection；按固定顺序锁知识库、policy、文档、引用模型和最新实验，锁冲突409；policy写锁KB、人工标注写锁experiment，与门禁形成共同边界。版本保存policy/source/review revisions。独立运行的受控并发用例证实发布判定后KB/experiment/document不能被并发写者抢锁；合法发布201，之后来源变化新发布409。没有把仅仅移动读取误称事务一致性修复。
- 003：事件返回按实际权限计算can_ack，页面按can_ack控制确认按钮，后端仍独立校验；只读系统事件不能确认，个人事件路径保留。
- 004：metrics凭证按实例settings.data_dir/monitoring/metrics.token读取，不再源码全局回退；独立API测试覆盖缺失503/错误403/正确200，不读取真实生产token。
- 005：创建使用全局710039及owner行锁计数，单账号50、单机500上限，不依赖前端；created_at/id键集分页，UI可加载更多规则。独立并发49→两个新增得到201/409，账号规则保持50条；后台tick工作量有明确总界。

独立全新随机owner_agent_core_review_*_test、独立TEST_DATA_DIR执行 **18项通过16.88秒**，无跳过；finally只删除本轮库。日志`.local/team-workspace/platform-expansion/review/core/independent-core-review.log`，含monitoring、quality_gate单元/接口、agent_versions和workspace_backup。未修改业务源码、启动生产或真实外部调用。实际浏览器全量回归不在此结论中。

产品边界：EXP010尚欠独立live/ready、运行趋势和错误下钻；EXP060尚欠实例role/version/node/faultdomain展示。主协调已明确将另增模块，不应将本次核心五项通过等同上述PRD全满足。模型真实供应商待接入边界不变。最终main/backup/maintenance/KB删除集成详见platform-expansion-integration-review.md；集成有阻塞问题时禁止整站交QA/部署。
