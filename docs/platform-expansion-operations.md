# 平台扩展运维设计、部署与交接

2026-11-02。适用工作区 `D:\project\my_agent\deepagents`、`onwer_agent`。配合[产品需求](platform-expansion-requirements.md)的EXP-010/011/060，保留既有数据、加密密钥和未提交改动，不提交推送。内置大盘由平台API/Ops采样实现；本文件交付可选时序监控服务和脚本，不把部署配置文件存在当作真实采集验收。

## 服务选择与资源

默认使用中文内置监测页，无新增服务。可选外部栈只包含Prometheus和Grafana：Prometheus保留7天且512MB时序上限、容器内存384MiB/0.5CPU；Grafana内存512MiB/0.5CPU，临时目录64MiB计入内存限额。总内存硬限制896MiB，不为本机引入完整Langfuse、Loki、Tempo、Alertmanager；原始用户对话和文件不进入外部监测栈。

这不是吞吐承诺。当前Coding预留5GiB、Browser预留3GiB，实际可用内存不足时已有队列保持等待，不能为了监控放宽配额。启动脚本发现主机可用内存不足2GiB即拒绝，启动后执行任务仍以平台实时准入为准；需要大任务时可停止可选栈，内置监测不受影响。不停止其他应用、不自动调整Windows/WSL资源。

## 固定契约与数据边界

- 平台现有API仅监听回环8100；新增路径 `/api/v1/internal/observability/metrics` 由独立Bearer保护，不复用管理员Cookie、模型Key或用户API Key。
- token文件为默认实例 `services/api/.local/monitoring/metrics.token`；Prometheus通过只读secret文件和 `authorization.credentials_file` 读取。token不是compose环境变量、启动参数或日志内容。
- Docker访问使用 `host.docker.internal:host-gateway` 显式映射。本机128MiB只读探针实测：未映射时DNS失败，映射后 `/health/ready` 返回200；无需新exporter进程或扩大API监听。
- 指标限定低基数：`owner_agent_service_online{kind}`，`owner_agent_queue_runs{mode,status}`，`owner_agent_queue_oldest_seconds{mode}`，主机CPU/内存/磁盘，`owner_agent_model_tokens_sum{direction}`，`owner_agent_model_calls_sum{result}`，`owner_agent_cost_estimate_sum{currency}`，`owner_agent_cost_unknown_calls`，`owner_agent_sample_timestamp_seconds`，`owner_agent_sample_available{signal}`。
- kind只含api/ask/work/ops；mode为ask/work/workflow；币种CNY/USD分列。没有用户ID、任务名、模型名、端点URL、提示词、文件路径和凭证标签。DB可回算总和全部是gauge，清理后可能下降，禁止用rate假装严格counter。
- 时间戳是持久样本时间；CPU等未知值省略series，来源可用性另报告；未知费用单列，不能填零或合并币种。

## 布局与告警

Grafana自动导入UID `owner-agent-platform`，14个中文面板：采集、样本年龄、四后台、队列、等待时间、CPU、内存、磁盘、输入输出Token、调用结果、已知费用、未知费用、来源可用性和触发告警。时间范围与30秒刷新可调整，缺失值展示“无数据”。

Prometheus加载7条规则：抓取失败/缺失、样本过期、后台离线、观测来源不可用、低内存、低磁盘、长队列。抓取失败和未知不会显示健康；`rules.test.yml`提供离线规则行为用例。规则触发记录可在Grafana/Prometheus查询，默认不配置外发接收器，因此不能声称短信或企业消息已经送达。平台内置持久告警确认/恢复及通过已有通知通道发送由EXP-011负责，避免双份重复发送。

## 文件与操作

- `infra/monitoring/compose.yml`：独立项目/网络，两个持久卷、只读配置、无Docker socket，容器只读根文件系统、去除capabilities、禁止新权限。
- `prometheus.yml`、`rules.yml`、`rules.test.yml`：固定采集路径、30秒周期、200样本上限与规则。
- `provisioning/`、`dashboards/platform.json`：只读自动导入数据源与中文大盘。
- `scripts/start-monitoring.ps1 [-Preflight]`、`check-monitoring.ps1`、`stop-monitoring.ps1`：本机部署、实际采集/看板/规则检查、保留数据停止。

先由开发部署新增metrics接口并确认数据库采样正常，再在项目根运行：

```powershell
.\scripts\start-monitoring.ps1 -Preflight
.\scripts\start-monitoring.ps1
.\scripts\check-monitoring.ps1
.\scripts\stop-monitoring.ps1
```

Preflight不生成密钥或启动容器；真实启动仅在文件不存在时生成独立随机token和Grafana密码，文件ACL限当前Windows账号与SYSTEM，已有文件保留。Grafana只映射 `127.0.0.1:3300`，禁止匿名登录与自行注册，账号admin，密码在 `.local/monitoring/grafana.admin`，脚本不输出值。Prometheus不发布主机端口，只在专用Docker网络供Grafana访问；不开放匿名公网管理端。

首次密码只用于初始化已有Grafana卷；不能仅改密码文件假称已轮换数据库账号。若需要轮换，应在Grafana管理界面完成并同步本机受保护文件，不删除卷强制重置。停止脚本不带 `down -v`，不删除时序、看板、用户或平台数据。不默认对外发布监测，远程访问需要额外TLS/认证入口，禁止直接改为0.0.0.0公开管理页。

## 多机与新需求依赖

本轮单机profile不能冒充高可用。真正多机需先拆除平台本地路径假设：共享PostgreSQL/加密主密钥分发、私有附件/成果对象存储、向量索引单写服务、Work执行节点归属与资源配额、数据库心跳围栏、入口TLS与会话、备份恢复协调。不能让两个API同时打开同一嵌入式向量目录，不能把本机 `.local` 复制到多个实例后称为HA。

实施顺序建议：①本机监测与失效提示/告警，②单机实际容量基线，③隔离环境验证共享存储/向量单写和两个入口实例，④远端执行节点及故障切换，再做多机负载验收。每步需独立证据，未运行的拓扑和故障分支登记待验。现有真实数据库/本机任务不作为主动破坏性压测对象。

账户扩展可复用平台数据库；OIDC需用户实际身份提供方，邮件/短信需投递服务；协议本机替身可先验证，不默认部署第三方身份系统。MCP stdio必须复用受控执行环境而不能直接宿主运行未知命令，OAuth需要真实提供方回调配置。媒体原生协议、视频向量及额外费用依据冻结的提供方兼容矩阵实施，不用任意配置URL替代适配。上述依赖由产品/开发文档逐项记录，监控脚本不擅自新增它们。

## 验证记录与交付状态

当前已完成配置、看板、规则和三脚本。PowerShell解析和Preflight通过；host-gateway只读探针成功。官方固定版本为Prometheus v3.15.1、Grafana13.3.0，已核验发布页。Docker registry manifest宿主直连遇连接重置，其后通过Docker现有镜像源成功下载这两个固定镜像；未改镜像源。Compose语法通过，离线原生promtool规则行为用例通过，涵盖缺失/离线/低内存/来源不可用/样本过期。尚未真实启动外部栈，实际采集与看板验收不能提前写通过；等待root完成API后安排资源窗口。后续实际结果补记于本节。

## 官方依据（核验2026-11-02）

- [Prometheus配置和credentials_file](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [Prometheus告警规则测试](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)
- [Grafana容器及文件secret配置](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/)
- [Grafana数据源/看板provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Prometheus v3.15.1](https://github.com/prometheus/prometheus/releases/tag/v3.15.1)、[Grafana13.3.0](https://github.com/grafana/grafana/releases/tag/v13.3.0)

检查脚本除验证实际target=up、看板UID和规则组，还实测匿名metrics拒绝401/403及匿名Grafana看板拒绝401。输出表示监控管道状态，不把“已采集”当作所有业务健康；业务离线、样本过期或未知费用须查看大盘与告警。

### 监控凭证实例隔离修正

metrics token必须位于Settings.data_dir/monitoring/metrics.token，默认services/api/.local/monitoring/metrics.token，不能让隔离恢复库或测试实例共用根目录token。外部监控脚本只部署默认本机实例；自定义PLATFORM_DATA_DIR需专属secret和采集配置。Grafana密码仍位于根.local/monitoring/grafana.admin。若旧根token存在，脚本不覆盖：新旧摘要不同时拒绝；只有旧文件时要求先核对默认实例、停止可选监控后显式复制到新路径、收紧ACL，再启动验证。不会为迁移打印token。当前尚未实际生成生产监控token，未执行覆盖迁移。

## EXP-060：主动/备用边界工程与容量探针

新增 `scripts/verify_platform_topology.py` 与入口 `scripts/test-monitoring-topology.ps1`。入口只允许onwer_agent；从本机127.0.0.1:55432配置派生随机 `owner_agent_topology_<12hex>_test` 库，脚本用该库完成迁移，独立证据目录在 `.local/team-workspace/platform-expansion/operations/<测试库名>`。不会传生产DSN到argv，不使用用户原模型、运行或向量目录。结束仅清理自建进程树和严格命名的测试库，文件证据保留。

```powershell
.\scripts\test-monitoring-topology.ps1
```

演练中三个阶段共用一个本轮 `shared-data`（vault、vectors及services），复用现有ServiceHost类；只有probe内部使用0/1/2不同manifest槽位，避免kernel host锁替代真实数据库writer互斥。实际app.server及Qdrant未改动，公开ServiceHost CLI仍保持API槽位0限制。备用在710014被主实例持有时必须启动失败并退避；主host崩溃后才能获得数据库锁并接管，探针全程观察持锁pid最多1。额外主动尝试打开同一Qdrant目录，必须因锁而拒绝第二writer。维护者同时持710014和710017时备用也必须退避，释放后才恢复；数据库marker必须保持。

**这些是同机逻辑故障与围栏演练，不是物理高可用。** 默认生产入口仍是单API+守护恢复，不直接把probe槽位开放到生产。可选生产主动/备用方案必须保证同一稳定入口、共享受保护存储、统一停止/维护全部槽位、失锁实例停止、API只读/写入边界，以及主备监测；现有公开脚本只管理API槽位0，不能启动另一个非受管API就宣称HA。多机时还需解决共享向量单写与成果存储、节点故障和入口切换，本机模拟不能代替真实多机验收。

容量报告使用独立API的health端点，固定并发1/4、每级目标5秒且最多2000次请求、2秒单请求超时、共享连接池，不对生产接口压测。此前24次的小样本记录作为历史保留，最新容量补充以文末为准。字段：工作负载、并发、请求/错误数、总时长、实测RPS、p50/p95、同机CPU数、内存总量/可用、可用磁盘、逻辑接管秒数、最大API锁所有者、Qdrant第二writer是否被拒、维护接管是否被阻止、模型调用0和清理结果。未知CPU保持null。它只说明健康查询与接管的观测，不能换算为模型并发、业务吞吐或GPU能力。

首轮真实自验已通过（61b41701bfce命名空间，测试库已删除）：最大writer1，Qdrant第二writer被拒、备用拒绝与接管、维护拒绝及恢复、marker保持全true；崩溃接管14.375秒。首轮每请求新建HTTP客户端使容量结果包含初始化开销，因此没有当作服务器能力；已改连接池并再次自验，最终报告待本节补记。原测试日志保留 `operations/topology-run.log`，不掩盖修正过程。

## 媒体镜像运维复核

只读复核 `scripts/build-media-image.ps1` 和 `infra/sandbox-images/media/Dockerfile`：固定Python基础镜像digest、专用uid10001、只安装ffmpeg/证书；构建后取固定imageID，受限无网络只读容器做ffmpeg版本自检，只有成功才写 `.local/media-decoder-image.json`，失败不更新批准项。运行端必须使用批准imageID，不能仅靠可变dev标签。未发现新增宿主解码、特权容器或Docker socket暴露；不代替媒体运行端的参数/文件边界审查。

解码单次512MiB另计，不能把128MiB版本自检当作业务解码预算。编码/浏览器5+3GiB与监控896MiB占用存在时，媒体应按平台准入顺序执行，不提高默认配额或挤占其他用户任务。镜像安装包和转码器是额外供应链，固定批准ID和离线运行必须保留，升级后重建/重审/重验。未启动新常驻媒体服务；真实外部向量和视频质量仍按对应接入项验证。

### EXP-060最终自验结果

连接池修正后完整重新运行exit0，命名空间 `owner_agent_topology_8caab16f23d2_test` 已删除，进程清理错误0。最大710014 writer1；备用在主实例运行时拒绝、共享Qdrant第二writer拒绝、崩溃后接管3.906秒、maintenance持710014+710017时备用拒绝、释放后恢复、DB marker保留均通过。无外部模型调用，生产服务未启停。证据 `operations/topology-final.log` 与该随机目录report.json。

健康端点连接池探针：并发1/24次/错误0，0.176秒，136.70请求每秒，p50=6.63ms、p95=7.82ms；并发4/24次/错误0，0.070秒，345.16请求每秒，p50=9.75ms、p95=17.29ms。样本很小、仅本机health，不能宣称业务、长任务或模型的承载量。主机观测20逻辑CPU、内存约31.84GiB/可用约7.54GiB，CPU本轮首采未知null。Ruff及PowerShell语法检查通过，待独立审后交QA复验。

### 运维代码首审修复（待复审）

独立首审R-OPS-001确认pg_locks按全cluster计数会把其他测试库/生产库持有的同号710014混入；现仅按当前数据库OID计数，不停止其他实例来迎合测试。R-OPS-002确认正常停止超时后可能留进程却删测试库；现超时后强停本次Popen创建的确定进程树并wait，仍无法确认停止则保留测试库、报告passed=false/cleanup_errors，禁止继续删库。不会按进程名扫描或停止其他应用。

修复后新随机4ee019f11ad0测试库完整拓扑开发自验exit0，writer最多1、Qdrant单写、备用拒绝、崩溃接管2.719秒、维护拒绝/释放恢复、marker和清理均通过；已删除本次库。并发1/4各24次health错误0，仅为该健康工作负载。另真实父子进程故意忽略停止35秒，实际进入超时树清理后均停止，证据development/ops-reviewfix-cleanup.log及stop-probe目录，不以mock冒充进程清理验证。两项修复仍待独立复审，不能交QA。

正式部署脚本补充批准媒体镜像检查：已有合法sha256批准ID且镜像仍在时保留，否则使用既有受限构建脚本创建依赖。批准文件格式异常直接失败，不覆盖旧记录。新增监测详情的077及实例metadata由正常迁移和新进程注册，无需额外常驻服务。

### EXP-060容量报告字段补充

容量新版每级目标5秒，最多2000请求；达到上限或时长即停止安排新请求，最多4个在途请求、每请求2秒超时，报告实际持续时间、目标时长、请求上限是否触达。成功200、拒绝429、其它HTTP或传输错误分别实际计数；RPS同时列全部尝试与成功速率，延迟覆盖所有尝试，不把429算成成功。health没有业务任务队列，明确business_queue=not_applicable_health_endpoint，不据此声称业务无排队或验证了执行配额。

负载开始前真实查询隔离库users、model_configs、conversations、task_runs、work_queue_runs、workflow_runs、knowledge_bases、documents数量；数据规模只代表本轮空测试语料，不能外推真实知识库/模型任务吞吐。

先核对本轮ServiceHost manifest，并从该实例独立日志取得Uvicorn记录的server PID，再只读验证其父进程链属于本次Popen创建的树，才取得稳定查询handle。不能把manifest.child_pid可能指向的venv启动器当作实际API worker。Windows使用GetProcessMemoryInfo每100ms读取WorkingSetSize和PeakWorkingSetSize，报告采样次数、实测最大working set和进程生命周期peak；生命周期峰值包含启动阶段，不能写成纯压测增量。只读计数，不读内存内容；查询不可用时保留null/available=false。非Windows暂不测API进程内存，不编造值。

补充首轮在查询内存之前因venv启动器的Popen PID与manifest host_pid不同而被严格断言中止；它未读其它进程，原安全清理执行，日志保留operations/topology-capacity-first-ancestry-failure.log。已改为最多6层的已记录子进程父链核验（CIM仅查询该链，不按名字扫描/停止），保留原DB范围和停止超时树清理/失败保留库修复，正在新随机库复验。原R-OPS-001/002已由根完成独立复审与QA，当前只对新增容量字段再审。

内存身份验证额外记录：Windows启动器可多一层，单看manifest child得到的约4.6MB实际是启动器，未作为API内存验收；尝试以唯一子链推断也因额外子进程而安全拒绝，未读取未知进程。最终采用本轮独立Uvicorn日志中的server PID，加最多6层CIM父链校验归属到本次Popen，再读取稳定handle。保留launcher-only及child-chain-rejected日志，不修改生产探针，不按名称遍历或停止其他进程。

### 新版容量完整自验结果（待独立审）

新随机库5702003e1f7b完整运行exit0并删除测试库，原安全cleanup无错误，writer最多1、共享Qdrant第二writer拒绝、主备/维护/marker全部通过，接管3.375秒。8类表实际规模均0，仅空库加marker；模型/任务吞吐与GPU容量未测试，health业务队列明确不适用。

- 并发1：实测5.026秒，789请求/789成功，429拒绝0、错误0；RPS 156.99，p50 6.22ms/p95 7.42ms；触及2000上限=False。API真实进程内存51次采样，最大working set 272883712字节、进程生命周期peak 276021248字节，available=true。
- 并发4：实测4.627秒，2000请求/2000成功，429拒绝0、错误0；RPS 432.29，p50 8.8ms/p95 10.27ms；触及2000上限=True。API真实进程内存47次采样，最大working set 273154048字节、进程生命周期peak 276021248字节，available=true。

资源边界为每级5秒或2000请求先到即止、最大4在途、2秒请求超时；无生产服务启停/监控栈启动/外部模型调用。CPU首采未知null，不编造；API内存是已记录Uvicorn进程的稳定只读handle实测。证据operations/topology-capacity-final.log及owner_agent_topology_5702003e1f7b_test/report.json。Ruff和差异检查通过，新增容量补充待独立审与QA，不覆盖已审过的DB范围/cleanup修复。

## 最新生产部署、监控与恢复状态（2026-11-02交接补记）

以下覆盖前文按阶段保留的“待审/未启动”历史，不删除首轮失败证据：根已完成本轮生产071～078迁移及完整部署，`.local/platform-deployment.json`记录deployed/complete、时间2026-11-02T09:59:40.2575144Z（北京时间17:59:40），备份20261102T095835Z-74897b2c。`.local/platform-health.json`显示API ready、Web production-ready、四后台owned-alive、执行环境认证/双镜像present、迁移current。后续新补丁部署另单列，不把这条旧完整发布冒充已加载006。

Prometheus/Grafana已由根在正式资源窗口实际启动并完成采集、规则和鉴权检查。`operations/grafana-ui-verification.json`记录真实登录通过、中文“知行 · 系统监测大盘”14/14面板渲染、JavaScript错误0、页面zh-Hans、credentials_logged=false；不再只是配置静态存在。监控没有开放匿名公网管理，凭证仍不输出到文档或日志。

`operations/restore-verify-final.log`记录本轮备份隔离恢复records_match=true、files_verified=39、credentials_decryptable=3、referenced_files_verified=0。这里引用文件计数实际为0，不能宣传为覆盖当前并不存在的成果文件。隔离恢复不覆盖生产数据。

用户已确认10:02～10:05UTC删除测试工作流和修改知识库bindings是本人操作，必须保留当前状态；不得依据更早测试基线复原这些资源。BUG-EXP006只是后端内部回调地址修复，代码独立审已通过，QA非默认8139站点复测尚在交接，生产是否加载以新的受控后端发布结果为准。

### BUG-EXP006后端补丁部署准备

复用已审 `scripts/deploy-platform.ps1`，新增最小 `-BackendOnly` 参数；拒绝与InstallDependencies联用。原准入锁握手/活动操作拒绝、停四后台、真实心跳过期、当前数据离线备份和manifest校验、迁移及保留管理员、依赖顺序启动四后台、完整健康检查均不省略，仅跳过Web生产构建/重启；不覆盖.env、不恢复任何旧数据、不调用真实模型。成功/失败报告增加scope，后端模式web=unchanged。未修改PG/镜像/服务生命周期实现。

预检入口（已执行，不启停）：

```powershell
pwsh -NoProfile -File .\scripts\deploy-platform.ps1 -BackendOnly -Preflight
```

待独立审、QA006通过和根放行后，精确执行入口：

```powershell
pwsh -NoProfile -File .\scripts\deploy-platform.ps1 -BackendOnly
```

静态与只读行为自验已通过：PowerShell语法；BackendOnly+InstallDependencies即使带Preflight也拒绝；BackendOnly+Preflight通过；前后Web PID、发布报告摘要和.env摘要不变。证据operations/backend-only-selfcheck.json、backend-only-conflict.log、backend-only-preflight.log。本次尚未实际后端部署，不能宣称生产已加载006。

若发布准入发现真实活动请求则按现有脚本拒绝并保留任务，不强制绕过；若迁移/备份失败则停止并按阶段核对，不自动恢复旧库或重放未知操作。遗留idle pwsh父进程44632不属于这次服务管理对象，不结束其tree。监控栈和Web进程不因后端补丁而重启。最后仍需实际健康、采集恢复及当前用户状态核对。

## 后端路由补丁发布补记

2026-11-02T11:03:42Z，原失败真实页面QA通过后，主协调实际运行已独立审查的deploy-platform.ps1 -BackendOnly；scope=backend-only、phase=complete、web=unchanged。Web没有重建/重启，当前数据先生成20261102T110312Z-643d72fa备份并校验，再恢复四个后台加载内部路由修复。随后独立check-platform与check-monitoring均退出0：迁移最新、四后台就绪、执行环境鉴权正常、授权抓取up、Grafana需登录。

operations/patch-after-preservation.json核对补丁前后21类现有记录及model.key，全部保持；用户确认删除工作流和修改知识配置的当前状态不回滚。006实际8139站点自动任务、Ask/Work知识、已发布流程调用/轮询均已复测通过；整站其余页面由QA继续独立执行。日志operations/deploy-backend-patch.log；较早完整部署时间与备份仍保留供追溯。


### BUG-EXP006实际后端部署与007边界补充

较前文准备阶段，根随后已完成BackendOnly实际发布：`.local/platform-deployment.json`现为deployed/complete、scope=backend-only、web=unchanged，时间2026-11-02T11:03:42.6497705Z（北京时间19:03:42），当前数据备份20261102T110312Z-643d72fa。完整健康报告仍为四后台owned-alive、API ready、Web production-ready、执行环境认证、镜像present及迁移current；没有据旧快照恢复用户删除或绑定。

QA进一步发现BUG-EXP007固定PowerShell控制器与独立API profile的隔离边界，已由本运维作者补统一持久部署profile guard：所有snapshot/command/control/stop/start在副作用前拒绝非默认配置，含Settings与继承环境；失败job落FAILED/ADMISSION/SERVICE_CONTROL_PROFILE_MISMATCH且maintenance=false，不停其它站点。18项冻结自验通过（10.20秒），Ruff/TS/备份组件ESLint通过，补中文reason说明。详见[007修复与证据](managed-service-profile-guard.md)。

007此时仅开发自验完成，尚待独立审、QA及后续发布，不能以11:03:42Z那次006部署声称007已加载。默认生产在线备份真实正向验证也须经该guard审查后由根放行；不会复制backup模块绕过profile约束，也不会仅为成功而在QA8139使用生产控制脚本。

## 最终完整发布与原生备份结果

2026-11-02T11:39:19Z完整deploy-platform实际成功，scope=full/web=rebuilt，备份20261102T113819Z-9373feae；007 guard及中文提示均已加载。原生备份页面1通过49.9秒：唯一新job产出20261102T114245Z-875e1c0c，记录一致、39文件/3份加密凭据验证通过，源码working-copy归档和四后台恢复通过；未修改定时设置、未恢复覆盖在线库。原生准备执行器Windows循环前提首轮拒绝及独立复审修正证据保留。

发布和原生备份后另行check-platform/check-monitoring均退出0，四后台/API/Web/执行环境/镜像/迁移正常、授权scrape up、Grafana需登录。21类当前用户记录及model.key同格式摘要全部保持；用户确认的工作流删除和知识配置保持。最终健康文件operations/final-platform-health.json、final-monitor-health.json；数据final-release-preservation.json。QA自身实例、3112/8138、随机库/目录/凭据及原生容器已精确清理，证据保留，不停用户生产资源。
