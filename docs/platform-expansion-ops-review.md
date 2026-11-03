# 运维脚本独立代码审查

2026-11-02。作者原运维角色；独立审查expansion_review_tools。范围：verify_platform_topology.py、test-monitoring-topology.ps1、监控start/check/stop脚本、infra/monitoring全部配置/看板/规则、build-media-image.ps1和媒体Dockerfile及运维设计。仅新增本报告，未修改源码，未启动或停止生产服务、监控栈、原任务或真实模型请求。

**当前结论：R-OPS-001/002修复后独立复审通过，可交QA。** 以下保留首审不通过和修复证据；当前通过范围只限脚本/配置，不代表监控栈已部署、全系统集成或物理HA验收。

## R-OPS-001 / P2：跨数据库的相同围栏锁被误计为多个API writer

- 位置：`scripts/verify_platform_topology.py`的wait_for函数，查询pg_locks的710014所有者。
- pg_locks是整个PostgreSQL实例的锁视图；当前只筛classid/objid/objsubid和mode，未筛database。同cluster生产API或其他测试库合法持有自己的710014锁时，也计入本次测试writer总数，导致count>1误报，或者单测尚无writer却被算成1。不能通过停止生产来让隔离探针通过。
- 独立实际复现：创建两个新随机`owner_agent_ops_review_<8hex>_test`库，每库只持一把710014会话锁。原SQL返回**2**，增加`database=(SELECT oid FROM pg_database WHERE datname=current_database())`后本测试库返回**1**。两个库都精确删除，未读取/关闭其他数据库会话。
- 修复：按当前测试库OID限定权威锁观测，保留本库实际持锁pid计数和最多1断言。补另一个随机库同时持同键锁的独立回归，验证不会混入生产/别的测试锁。

## R-OPS-002 / P2：优雅停止超时后仍删库，遗留自建进程树

- 位置：同文件Supervisor.stop及main.finally清理。
- 正常stop只写shared/services/api.stop并wait35秒。若host/子进程挂起，TimeoutExpired被finally记录为cleanup_errors，但随后仍DROP DATABASE WITH FORCE；没有对这个仍存活的已记录Popen树进行强停和wait核验。ServiceHost可能继续重启/保持文件锁，测试库已被删而测试进程仍在，不能称完整清理。
- 修复：优雅停止失败时，只对本轮记录且仍存活的进程及其确定子树做强停、等待并验证退出，再清理本轮测试库。若仍不能证明停止，应保留明确的失败证据/归属信息，不能输出完整清理成功；禁止按宽泛名称扫描杀其他服务。对子进程创建失败、非正常退出和重复stop保持幂等。
- 验证建议：自建可控忽略stop文件的测试进程树，确认强停分支被执行、父子均已消失，且无关哨兵进程保留。本项为确定的异常控制流静态核验，本轮未故意制造真实API挂死或操作生产PID。

## 已独立执行的检查

- 五份PowerShell脚本通过Parser解析；监控Compose执行config --quiet通过，未up/start、未生成任何监控凭证。
- 在本地已有Prometheus镜像运行原生promtool离线规则测试：**SUCCESS**。容器使用--pull=never、无网络、只读根和只读配置、128MiB/0.25CPU/32MiB临时目录，无Docker socket。首次审查容器未给只读根提供/tmp导致测试工具无法创建临时TSDB，已补有限tmpfs后重跑通过；此为审查命令准备问题，不计业务Bug、不隐去失败过程。
- 看板JSON解析通过：UID owner-agent-platform，14面板、30秒刷新，全部查询为约定聚合指标/ALERTS，未发现正文、账号/任务ID或凭证label；累计账本gauge未使用rate伪装counter。
- R-OPS-001用独立两随机库完成真实SQL复现。未运行已知会误计跨库writer的完整topology探针；修复后应独立重跑完整启动、Qdrant第二writer拒绝、崩溃接管、维护阻止接管及失败清理，而非沿用旧8caab16f23d2报告。

## 核验边界和交接

- probe生成严格随机_test库名和独立shared-data目录，DSN不放argv、不打印凭证；只以本轮Popen对象操作进程，未见扫描生产PID的正常路径。R-OPS-002补齐前不能把正常路径安全推论到异常清理安全。
- 主备共用测试vault/vector目录，三个测试manifest槽位用于避开宿主host锁而检验真实710014数据库互斥；额外Qdrant第二writer检查存在。维护用710014+710017阻止接管的设计不主动放开生产槽位；app.server/service_fence/077等主协调新接线不在本报告通过范围，需最终独立审查。
- 容量只测health，固定1/4并发、24请求、2秒超时、连接池，文档和输出明确不代表模型/任务/GPU业务吞吐，未发现夸大物理HA或生产容量。
- token现在按默认实例services/api/.local/monitoring/metrics.token挂载；start脚本读取实例data_dir，自定义目录明确拒绝猜测。旧根token存在时只比较摘要，不同拒绝、仅旧文件时要求显式迁移，不覆盖旧值。新secret为CreateNew并收紧ACL；Grafana首次密码与已有卷轮换边界明确。后端按实例解析token的实际行为属于core修复/集成复审，不用脚本检查代替。
- 外部监控默认仅Grafana回环3300，Prometheus不发布主机端口，数据卷保留；stop不带-v。容器限制、只读配置、独立secret文件存在，未发现socket/宿主特权挂载。尚未真实启动可选栈，因此实际scrape/auth/Grafana卷权限和看板导入仍需QA资源窗口验收。
- 媒体Dockerfile固定基础digest、uid10001与ffmpeg依赖；构建后受限自检成功才登记固定imageID，不以可变dev标签执行。未重新构建镜像，未验证外部apt供应链。生产解码运行端--pull=never等修复由媒体模块复审负责，不以镜像构建脚本替代运行端审查。

## 修复后独立复审

001关闭：wait_for按current_database的OID过滤pg_locks，跨库同键不再计入本次writer。002关闭：Supervisor.stop正常等待35秒超时后，只对仍存活的记录Popen树强停、wait15秒；非Windows以独立process session/group处理。main清理失败则保留本次测试库，passed=false、test_database_removed=false，报告保留错误而非假成功。

独立实际重跑完整topology脚本成功，随机命名空间`owner_agent_topology_87d7dc4de502_test`：writer最多1，共享Qdrant第二writer拒绝、备用拒绝/接管、维护锁阻止接管及释放恢复、marker保留全部true；接管5.187秒；24次health并发1/4均0错误，未调用模型。清理errors为空、测试库已精确删除。原始报告在本命名空间report.json保留，health小样本不作为业务容量声明。

另独立执行verify_ops_cleanup.py真实35秒忽略stop文件父子进程探针，timeout_tree_fallback、recorded_descendant_stopped均true，进程仅脚本自建，无生产PID操作。该探针不覆盖内核/权限使强停本身失败的实际环境；该失败分支依据源码明确保留DB和失败报告。

增量只读审查deploy-platform.ps1媒体批准检查：既有批准JSON须为合法sha256，实际本地image inspect确认存在；缺失时调用现有受限构建/自检脚本，不把dev标签当批准；非法批准记录拒绝且不覆盖。此处未执行正式部署、构建或生产重启，其他部署准入/备份/main/077接线仍待最终整体审查。

## 容量字段补充独立复审：通过，可交QA隔离复验

2026-11-02，限定verify_platform_topology.py的capacity、ProcessMemory、api_child归属检查、8表规模与报告字段及运维文档；原001/002和已审部署代码结论保持，不借本次复审重开生产部署。

静态核验：只从本轮独立Uvicorn日志取得server PID，在Windows沿该PID最多6层父进程查询核验属于记录Popen树，不按名称扫描其他进程。内存读计数使用稳定查询handle，with closing确保关闭，不读内存正文、不终止该handle所指进程；非Windows或读取失败保留unknown。生命周期peak与本轮最大观测working set分开，未把启动峰值称负载新增。请求线程最多1/4并发，互斥分配最多2000次，5秒后停止新增、等待在途请求结束；采样由主线程每100ms进行，无额外常驻采样线程。HTTP错误纳入计数，资源上下文统一关闭并join请求future。

独立实际重跑完整脚本exit0，命名空间`owner_agent_topology_e95e0b33f21e_test`。原writer最多1、备用拒绝、共享Qdrant第二writer拒绝、维护阻止接管及释放恢复、marker保留均true，接管3.641秒；cleanup_errors为空，本次库精确删除。8表规模全部0，明确空隔离库加marker，未调用模型或启动Work任务。

- 并发1：5.029秒，767次/767成功，429与错误均0，51次内存样本；API最大观测working set272756736字节，生命周期peak276250624字节。
- 并发4：5.068秒，1860次/1860成功，429与错误均0，51次内存样本；API最大观测working set273063936字节，生命周期peak276250624字节。
- 两级本次均未到2000请求上限；P95分别9.18ms/14.34ms，只是health小样本，不是业务吞吐或GPU/多机容量认证。业务队列字段not_applicable，CPU仍null，没有填造。

另以本审查者内存HTTP替身和计数对象独立调用capacity：200/429/503/ReadTimeout循环，4并发达到2000上限，精确统计500成功、500拒绝、1000错误，unknown内存为null，计数对象close已执行。此为上限/分类/未知/清理的确定性验证，不混作实际API负载。未故意制造PID复用或强停失败等破坏场景；其安全边界依赖现有归属校验及此前001/002证据。

未发现本次容量补充新增阻塞问题，允许QA按同样随机资源独立复跑。生产服务/监控栈仍未因本次审查启停，不将本机逻辑主备验收扩大成真实物理高可用。
