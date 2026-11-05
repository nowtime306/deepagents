# 本轮独立代码审查记录（2026-11-05）

角色：独立非作者审查。当前为环境换位候选的阶段审查，尚未批准实际停止、切换或测试；没有执行作者脚本的停止/部署分支，不改变生产配置或数据。

## 冻结与准备部分

检查对象：`.local/environment-port-switch-20261105/` 中 `freeze.py`、`prepare-test.py`、`stop-owned.py`，以及 `latest.json` 指向的 `frozen-96d70434385840519012d602ccc8dfd6`。

- 独立只读逐项复核冻结的351项业务源码和727项Web资产，摘要均匹配原固定清单；未从工作区新增源码冒充原发布源码。
- 派生生产宿主仅改变冻结ROOT、原正式DATA和.env绝对路径、允许的正式Web Origin至 `http://127.0.0.1:3131`；数据库限定、schema85、原key、受限模式及全部执行关闭边界保持。宿主摘要与freeze.json相符。
- `prepare-test.py` 使用全新随机 `_test` 库、账号、私有目录和密钥，不复用或重置正式库。迁移只作用于新库；服务尚未启动，不能据此判定G0完成。
- 仅AST解析和字节核验通过；未执行新的模型、任务、沙箱或登录操作。此结果不算业务任务验收或部署成功。

## 停止脚本初审发现与复审状态

1. **P1，停止前准入竞态**：初读版本在idle快照后仍允许HTTP新增写入/队列claim，再写api.stop可能中断刚接受的运行。要求复用现有710035维护准入锁，排除在途写入后置独占维护身份，重新核活动，停止期间保持拒绝新写入；恢复须CAS核自身身份并保留原maintenance字段。作者正在修复，等待固定SHA后复审，不提前关闭。
2. **P2，Ask退出证据不足**：初读版本仅等待service_instances心跳行归零，未等待原Ask进程句柄退出。服务注销与进程完全退出不同，不能证明旧维护锁已释放。要求登记真实Ask进程创建时间/命令/原生句柄，合作停止后等待原句柄signalled。作者正在补充，等待复审。
3. **P2，停止标记归档归属**：初读版本归档时未重新核写入的nonce/字节；若其他维护方替换标记，不得归档其请求。要求归档前核本轮专属标记一致。作者正在补充，等待复审。

上述为初读版本的问题，作者文件仍在编辑；不得将初读行号或摘要用于批准后续不同内容。维护active=true可在短事务排他锁释放后持久拒绝新请求/claims，不要求持session排他锁贯穿停止，以免阻塞graceful drain。

旧测试库通知记录的精确状态、attempts、消费者和在途证据尚待主协调者确认；SENDING/UNKNOWN不得绕过。仅对明确未开始且原库保持维护关闭、消费者全部退出的fixture，才可按主协调者限定批准保留；不能修改或删除旧状态使检查通过。

## 尚待交审

- 固定SHA的完整切换及失败恢复脚本、所有旧服务身份与合作停止证据、唯一旧自有测试Web句柄终止例外的边界。
- 同host不同端口的session、MFA challenge、OIDC browser三类Cookie实例隔离候选与工程测试；当前仍是阻断换位的真实缺陷。
- 新测试Web与正式Web代理目标、运行依赖来源、改动清单、原正式数据/密钥守恒与零业务副作用证明。

当前结论：**冻结/新资源准备范围未发现新增阻断项；实际换位仍待修复复审，不批准执行。**

## 固定候选复审

### Cookie-v1：静审通过，可交独立QA

候选目录 `.local/team-workspace/acceptance-resume-20261105/developer/cookie-v1`，六个产品文件以该目录 `manifest.json` 的candidate_sha256固定。最小差异覆盖config/security/main/account_api/identity_api/identity_oidc。

`session_cookie_name`仅kw字段、默认原名、1～64位安全ASCII校验；三类Cookie从实例Settings解析，辅助挑战/浏览器绑定名在默认实例保持原名，测试实例派生新名。主登录/退出/身份读取、改密清理、会话current标识、MFA挑战读取/签发/取消/成功清理、OIDC绑定读写均经过定向调用链核对；未发现新增阻断问题，没有全局常量替换。可以交根使用新隔离库进行独立复验；作者15项及41项不能计为本角色独立QA，也不能算页面验收。

独立名称解决选择与生命周期串扰，不使浏览器Cookie获得端口级传输保密隔离；同host双端仍共享Cookie发送范围。互不信任的服务保密边界不在本方案中冒称完成。

### 停止候选9008：初审三项关闭

固定 `stop-owned.py` SHA256 `9008c8a6de60edabca9d898b56588dd36e8d374dbff0d4c0d8a5fa469f7e93c2`。仅静态复审，没有执行stop分支。

- 710035排他事务排除在途写入，再设置本轮lease_token维护状态；二次idle之后才停止，正式库恢复CAS限定本轮token及四字段原值。
- 持有真实Ask原生句柄，核命令并等待原进程退出，同时核注册行归零；初审P2关闭。
- stop标记使用独占创建并保留本轮字节，归档前复核一致；初审P2关闭。
- 唯一旧测试通知例外精确限定 `91038f83-d221-421b-a475-35a9215ab694`、PENDING/attempts0/无lease/无finished，旧bootstrap摘要固定；其他pending/sending保持拒绝。原旧库在全部旧进程退出后继续保持本轮维护状态，不修改通知。原正式已结束UNKNOWN只留证，不改值。

该结论只批准候选停止逻辑，不批准整体切换；完整启动/失败恢复还须审查、Cookie独立QA通过后才能执行。部分停止中断时维护状态和停止原件保留，恢复必须按登记身份处理，不自动解除未知维护状态。

### 启动候选初审：待修复

`launch-new.py` 初读SHA256 `cae45f72f857bcd4c65033175437aea8c863d7988aab5c1dde4041bb4c2fee90`；`serve-test.py` 初读SHA256 `9d7cbb46abe9200ecb4386ee7a417d68b8cc56af9ad3baf436134571cd6ec117`。

P2：启动正式Ask后仅sleep2秒并检查进程尚存，即启动API，不证明其冷启动检查、注册及心跳就绪。要求有界核定目标正式库唯一新Ask身份与心跳，再启API；API/测试API实际ready和失败恢复也需闭合。新测试Web仅派生两条API目的地址的方案可接受，但仍须实际摘要/代理/登录核验。作者已收到修复要求，整体不批准部署。

## 最终运维候选静审：限定通过

最终文件绑定：

- `stop-owned.py`：`9008c8a6de60edabca9d898b56588dd36e8d374dbff0d4c0d8a5fa469f7e93c2`。
- `launch-new.py`：`b0bdf023d26f08b7fca73c345e3fd93e78c85901e02020e0f3d5ab892ba179d0`；替代cae45/21697旧候选。
- `serve-test.py`：`9d7cbb46abe9200ecb4386ee7a417d68b8cc56af9ad3baf436134571cd6ec117`。
- `verify-new.py`：`5f4fca542c1fff8b77f16632333e5d168ba30a103ecb830b09d3c1d7381ece7f`。

启动P2已关闭：正式Ask核目标库唯一15秒内新鲜心跳、PID为本轮launcher或其直接子进程、精确冻结host命令及serve-ask，再启动正式API；两API和两Web均有界等待真实200，不以启动日志代替就绪。记录原生创建时间，仍需现场核真实监听归属。

额外指出的运行资产核验缺口也已关闭：启动前核727项原Web资产，实际运行副本next.config.mjs、package.json、实际Next bin/package/start-server与原固定来源对应，生产node_modules必须为指向原已验依赖目录的junction；有效配置摘要和解析路径进入启动收据。测试副本仅派生两条API目的地址，原正式文件保持。

验证脚本使用同一真实HTTP Cookie jar核双环境各自账号和测试退出不清正式会话，验证外来Origin拒绝、原正式数据库/namespace/key及模型用量保持，输出无凭据或token。它不声称UI验收；MFA/OIDC生命周期及双向注销等完整边界由另行独立QA完成，不能以本脚本一端退出覆盖全部认证验收。

失败恢复文档 [环境换位运维方案](port-environment-switch-operations.md) 明确：按新服务本轮身份和专属标记合作停，保留失败日志与维护状态；确认退出后，仅用冻结源恢复原正式origin/3100，不回滚数据库、不恢复旧备份、不重新启动封存旧测试库或投递其通知。此为人工恢复方案静审，不是实际故障恢复通过。

**限定批准条件**：根完成Cookie六产品文件独立QA并核测试源完整清单后，可写绑定上述确切SHA的单次准入收据，再执行本轮受控换位。切换前所有原生身份/维护/活动检查仍必须通过，条件变化即停止，不能沿本审查绕过。运维执行后还须根独立现场核验与产品G0验收；本记录本身不代表换位、UI、业务案例或发布已成功。

## 实际Web404后的最小运行清单修复审查

根/运维报告新正式Web启动后首页404，测试Web尚未启动。独审只读核installed Next `filesystem.js`：缺失dist顶层 `app-path-routes-manifest.json` 时读入空映射，随后无法填充appFiles。原F05该文件 `/page` 对应 `/`，与冻结 `server/app-paths-manifest.json` 的 `/page` 对应 `app/page.js` 一致，不支持改动src/app的猜测修复。

以根/运维独立核定 `required-server-files.json` 所列运行文件为最小范围，仅补两项：

- `app-path-routes-manifest.json`：`05b05e01fe26d95be40e9b0872ee40a6893b3598c1e258277e75674121950afa`。
- `react-loadable-manifest.json`：`550dd073de790daed1ec1276057da8508e783737c4caf279c20b836f4d62dd0e`。

更大的六项顶层JSON完整包建议不用于当前恢复，不增加本次修复范围。原727清单及首次失败保持，新增两文件的路径与摘要单独留证。

固定 `.local/environment-port-switch-20261105/recover-web.py` SHA256 `85f5083fd2d95afac8cc2202c3beb90bef055d9150dd5d01067ff98c02324fd2` 静审限定通过：仅核并复制同F05 BUILD_ID原件、原727摘要不变，测试routes保持已审派生SHA；持原生创建时间句柄、当前CIM/preloader/3131监听身份后token合作停止旧404正式Web，新nonce/新日志/新创建时间重启；正式3131验证200后才准启动测试3100。两模式各一次，不重跑一次性launch，不改API、DB或src/app。原身份和失败证据保留。

根可在本轮准入文件绑定该确切脚本SHA后执行，仍须真实健康/身份/接口及产品验收。若此最小恢复不能成功，停止探索并恢复原正式核心；本审查不是修复执行成功或业务测试通过证明。

## A05 receiver-v1 基础候选独立静审

限定结论：未发现阻止交主 Agent 独立 QA 的 P1/P2；只批准基础候选进入隔离工程验证，未执行候选，不修改作者文件。候选 `.local/team-workspace/acceptance-resume-20261105/developer/a05-receiver-v1/manifest.json` SHA256 为 `48279ac2f53bbd93bf3e30704caa38471f4c48aa6aea6ca878d13ff3588b5069`，所列六文件与 changes.diff 全部摘要逐项一致；正式依赖 `services/api/app/mcp_service.py` 为 `806fa6cab65d5e7592f29516151d0e538697f63fab863da448b152fd2e4c3472`。

接收器所有 HTTP 请求在分派 MCP 或证据路由前执行固定 Bearer 恒定时间比较；测试实际 socket 只绑定随机 loopback 端口。四元 UUID 归属固定持久到 SQLite，重复打开必须同 scope；工具只支持固定 lookup 注入文本和 Literal forbidden 合成 marker，无客户端指定命令、文件路径、SQL 或外部 URL。事务锁串行化四效果上限。证据路由只读计数及事件摘要，显式正控制写入可被独立账本观察；错误 token 及错 scope 不增加效果。

测试真实导入正式 MCPService，通过真实发现/HTTP MCP 调用、真实数据库 capability binding 和工具启用状态；无 Work 写拒绝与调用时撤权走产品授权链，未以 mock/未知工具错误代替权限拒绝。直接 session 写仅用于独立正控制，不计作平台授权写业务通过。运行器只建随机新测试库，关闭非必要插件/追踪，最多 120 秒；成功清理前核源摘要、六类业务表为零及本库无残留连接，失败保留新库及证据，不改正式库和固定数据集。

独立 QA 应核实际错误 Bearer 对 MCP 路由也拒绝、跨归属和重开账本的证据不变、真实 socket/连接退出与零业务/模型计数，并自行复验作者四项工程结果。完整 A05 尚需真实 Ask root、动态工具声明及因果事件、撤权时点、固定处理器生命周期和故障留证；本基础候选不登记 readiness，不增加 A20 的 13/20 业务完成数。

### A05 基础候选独立工程 QA 实际结果

2026-11-05 12:16，北京时间：主 Agent 单独授权后，审查角色将摘要验证后的六个冻结文件复制至新的私有 QA 目录 `reviewer/a05-receiver-v1-qa-3e71b96170dd4dd8aa7df8be98ca4cce`，未修改作者原件。按已审运行器新建随机独立测试库、账号、四元 scope 和随机 loopback 服务，实际执行四项真实 MCP 工程测试：**4 passed，0 failed，0 skipped，2.62 秒**。正式 MCPService 未替换或 mock。

运行收据：上述 QA 目录的 `run-869e0a671ba0498a8412d68f381b68ef/receipt.json`，结果 `PASS_ENGINEERING_FOUNDATION_ONLY`。六类业务表计数均 0，模型调用和沙箱均 0。运行器完成接收器退出、连接关闭后精确回收本次新库；审查角色随后再次只读查询 pg_database 和 pg_stat_activity，确认该库不存在且残留连接为 0。

审查角色另以 SQLite 只读连接重新检查保留的四份观测账本：合法读取后无 Work 写拒绝为 lookup=1/write=0；实际撤权为 lookup=1/write=0；独立正控制为 write=1；四效果预算拒绝为 lookup=4/write=0。四份 scope 均不同。正式 app 全部 187 个 Python 模块的运行前后路径集合和 SHA256 完全一致。独立核证保存在 QA 目录 `independent-evidence.json`，源核对保存在 `product-source-before.json` 和 `product-source-after-verification.json`。

此轮依照冻结候选执行原四项，错误令牌动态用例覆盖证据路由；MCP 路由共享同一前置认证已静审，额外动态错误 MCP Bearer/真实 Ask 因果绑定仍交完整 handler 的新候选单独审测。本次不调用生成、不跑业务 suite，不将四项工程测试算成 A05 或 A20 业务完成数。

## 3100 评测管理 API 独立补充 QA

2026-11-05，北京时间；目标仅 `http://127.0.0.1:3100` 隔离测试实例，配置从本轮 latest.json 指向的 test/server-private.json 读取；原管理员只用于登录和创建自有夹具，不修改其密码或权限。只操作自有 owner/reader/cross 账号、专属角色和标记 QA 的合成题集；不操作主 Agent 浏览器题集 `eb2e57b9-3d4c-4bfc-a4d6-5c4b19ee7fbc`。未操作浏览器。

最终工程结果：**10 组断言通过，50 个真实 HTTP 请求；UI 用例 0、业务任务 0、模型调用 0、沙箱 0**。计数是工程 API 覆盖，不增加 A20 完成数。最终证据目录 `.local/team-workspace/acceptance-resume-20261105/reviewer/evaluation-management-501fc8a5a5cd`：`receipt.json`、`http-redacted.json`、`db-final-verification.json`。HTTP 保留方法、路径、脱敏正文、状态与响应，不保存认证 Cookie 或密码。

已通过范围：

- owner/reader 权限标记不同；reader 能读 capabilities/list，创建与预览管理内容返回 403。
- 自有 owner 能预览、保存和读取非可执行草稿；cross/reader 列表不出现该题集，直接读取返回 404；有管理权限的 cross 更新和发布也返回 404。
- 旧 revision 更新返回 409；发布 v1 后使用旧 revision 再发布返回 409；更新草稿至 v2 内容不会改变 v1 定义与摘要，HTTP 和数据库持久快照一致。
- 当前受限版本在执行路由前统一返回 503 `RELEASE_EXECUTION_PAUSED`，reader、cross、owner 均被阻止。执行请求前后独立核 agent_evaluation_runs/samples/events、task_runs、work_queue_runs、workflow_runs、model_usage、operation_executions、work_sandbox_sessions 九表计数全为 0。**该前置门禁使 run 路由自身的 reader/cross 权限逻辑未动态到达，不能据此宣称该逻辑已验收**；元数据权限和归属已按上述接口独立验证。
- 通过正常管理员角色接口撤销自有 owner 角色：既有会话即时 401；重新登录后 capabilities 读取和题集更新均 403。
- 原管理员 id/password_hash/role_id/is_admin/enabled/approval_status 摘要保持；主 Agent 浏览器题集整行摘要保持。

保留失败与清理：首轮 `evaluation-management-aa696b1b6665` 是测试预期错误——执行接口实际先命中全局 503，而脚本预期路由权限 403；第二轮 `evaluation-management-2a4025a5d6fd` 是 QA 脚本内存字段 KeyError，发生在运行拒绝零副作用验证后。两轮均保留原失败证据，未记为产品缺陷；脚本修正后最终轮完整通过。三轮总计 9 个自有账号已用正常账户接口全部停用，再只读核身份、QA 前缀、所属角色及会话 0。3 个自有 QA 题集和 6 个专属角色保留，因无正常题集/账号删除接口；未直接删除产品数据，原夹具归属可追溯。最终只读核九表计数仍全 0，浏览器题集摘要不变。

### 主 Agent 实际 UI 结果与工具阻断（来源：主 Agent 报告）

主 Agent 已在真实浏览器完成：3100 测试账号登录 → 能力中心 Agent 评测 → 空列表 → 新增上述自有题集 → 非法 JSON 校验错误 → 有效定义校验 valid=true/execution=BLOCKED → 保存 revision1 → 发布 v1/revision2，定义摘要 `9ee313bdf1b7cf8f6d1a070135732c9cbe7b47da3f3040c32ee4fdd78a5cb835`。

随后编辑说明并点击取消，出现原生 confirm；工具焦点 API 超时，三种接口均失败。原生恢复被工具明确终止（无法可靠判定当前 URL 执行策略），主 Agent 已停止 UI，未成功取消或保存，窗口可能仍有 confirm。未绕过工具、未关闭用户 Chrome。本轮 API 补测不替代该取消/未保存提示的 UI 验收，相关交互仍待工具恢复后继续。

主 Agent 独立 G0 收据 `.local/acceptance-resume-20261105-root/live-environment-independent.json` 已记录原计数/key保持、351 测试源及四 HTTP 200；该证据由主 Agent 核验，不是本补测重新执行部署或业务验收。

## A05 handler-v2 首轮静审：不通过，待修复后复审

冻结 manifest SHA256 `7c432f85cdfc89405778e0155e0807e37c918d716882cf4b01fe61399bcb4a78`。已逐项核 13 文件摘要无差异，baseline 为 `964f58e647b3e7c6d36a79bc50e25a3c5fd325539b41e1ce3425be7a41f322b8`，changes.diff 为 `b82abb4aa9120e4cd5aedf699eec47df34702aafd27ba6e6b83e8fa21d512207`。本轮只读静审，未运行生成、suite 或候选无模型测试；未消费拟定两根/五调用/75 Token 工程预算，旧 W05 预算未变。

两项阻断已交开发：

1. **P1：scope HTTP 工厂没有接入实际 Ask Worker 的 MCPService。** 候选 `evaluation_a05.py` 的 install 仅覆盖 `app.state.mcp.http_client_factory`；但候选明确运行的 ControlledAskWorker 继承正式 `AskWorker.execute`，正式 `services/api/app/ask_worker.py:71` 每根重新构造独立 `MCPService` 放入 Runtime.capabilities。首个真实生成绑定 receiver root 后，这个新 MCPService 使用默认工厂，缺少 X-A05 四元头，`scoped_application` 返回 403，合法 lookup 无法完成。当前无模型用例直接手动调用 Controller.client_factory，未证明 Worker 的实际构造链。修复应在受控实例真实 Worker 依赖链接入工厂，保留正式授权逻辑，不采用进程全局替换；补不调用生成的实际 Worker 构造与 HTTP 因果测试。
2. **P1：撤权判据与正式 Runtime 的失败留证不兼容。** 正式 `runtime.py:286` 捕获工具异常后调用 `preserve_partial`，在第二次已声明调用没有 ToolMessage 时，`runtime.py:298` 追加原生 result_unknown repair ToolMessage，随后 `record_updates` 持久第 5 条消息及 tool.interrupted（此 repair 的 name 可能为空）。候选 `evaluation_a05.py:304` 拒绝超过 4 条消息，`a05_protocol.py` 又强制四条固定角色和仅三个工具事件，因此真正 FAILED/MCP_TOOL_DENIED 根的完整原生证据无法通过。无模型用例手工拼接的四消息证据遗漏该实际行为。不得删改/压制正式 repair；新合同与判据应完整保留它，依靠原生错误码、撤权记录、receiver 效果、provider/native 已知用量综合区分确定拒绝与未知效果，补对应实际 Runtime 证据形状测试。业务语义调整仍须产品核准，不把 repair 标签静默改成成功。

已核的正向设计：生成前 provider reserve 持久落盘，按实例对 A05 实施 owner/case 两根五调用75 Token、每根三调用45 Token 上限；pending/unknown 阻止新生成。receiver 初始化与撤权在效果前持久 INITIALIZING/PENDING，丢失确认不自动重放；固定处理器提交使用持久 Journal，第一根证据完整并已知后才进入第二根。120 秒取消只请求停止，未知清理保留，不能把请求取消当作回收成功。这些静态检查不能替代真实两根及超时/清理验收。

作者报告的 34 项无模型/mock 工程测试仍仅工程证据，不能计为 A05 业务完成。待新修复候选重新冻结、独审通过、产品核准 v2 非等价语义及主 Agent 明确冻结工程预算后，方可交独立 QA。原 receiver-v1 与旧 v1 catalog 保持。

## A05 handler-v2-r1 复审及独立无生成验证

R1 manifest SHA256 `1dfb0848eef3418aab7681b4897cfe9fa43b32fbbab19cae7614f9d0ee23c61e`，15个声明文件及changes.diff/review-fixes.diff摘要逐项匹配。两个原P1阻断在本候选闭合，可进入独立完整工程的环境准备；不代表真实两根已通过，不授权自动启动生成。

- AskWorker新增可选keyword-only实例级mcp_client_factory及create_mcp，原默认构造保持；execute实际构造Runtime.capabilities时调用该方法。受控target只给自己的ControlledAskWorker传入Controller.client_factory，未全局替换MCPService；实际ResourceRun上下文注入归属头，正式MCP授权代码不变。冻结时唯一产品模块overlay为ask_worker.py，不可整包复制私有app覆盖Cookie修复。
- 撤权观察保留第五条原生result_unknown ToolMessage和tool.interrupted；判据逐项要求原文、flag、调用ID和完整事件，同时核原生FAILED/MCP_TOOL_DENIED、持久撤权因果、独立receiver及精确已知provider/native用量。没有删除保守未知标记或将任意UNKNOWN重标成功。R1定义摘要单独更新，原v2与失败审查保持。

非作者将候选复制至 `.local/team-workspace/acceptance-resume-20261105/reviewer/a05-r1-independent-bfa1721bf75d45a5a568cc78852fb36e`，核对私有187个app Python模块与正式集合一致且仅ask_worker.py不同，再运行已审no-model入口：**36项通过、0失败/错误/跳过，8.883秒**。真实Runtime.preserve_partial/record_updates工程和实际ControlledAskWorker新建MCPService→真实loopback HTTP携scope测试已执行；其他合约/Mock HTTP范围仍按测试源码，不冒充真实PG或两根业务链。

独立收据 `independent-receipt.json`；子运行 `no-model-ee8a896e2a3843cc943aece3752e11ff/receipt.json` SHA256 `4be930e2cd5b05ca4d05d5a87a14cb4715292342b4410c5549c57ca0c9c204e7`。正式187模块运行前后摘要一致。0实际生成、0业务根、0沙箱，未创建测试目标数据库。A20仍13/20。

尚待运维交启动/停止脚本及冻结完整依赖清单，根批准新隔离工程预算后实际执行，再核完整证据与清理。运维目录当时尚无可审脚本，不能将本R1源码审查当成运维脚本或生产发布审查通过。

### R1运维脚本与取消编辑UI候选审查

运维脚本旧708d版本因API环境缺少psutil未执行、不能沿旧摘要批准。最终 `ops/a05-r1/ops.py` SHA256 `aa3c05b8893700c3f5af21f092627c11f4a004c979a35e3f36cc7193da65f595` 及inputs SHA256 `cb926a7a74447bee8347af1e8a7bd58d4466b82633793fbd28b8095741275ddf` 已限定静审通过；337输入逐项摘要匹配，API保留已审Cookie，仅叠加R1 ask_worker。新库/目录/账号/key/namespace/端口均随机隔离，prepare单次；start不包含生成或任务提交，ready起120秒保守停止。最终以Win32原生handle的FILETIME和CIM原CommandLine识别，nonce请求合作退出并持句柄等待，不kill、不重启、不删库。失败保留状态；stopped只证明进程退出，不代表任务或业务资源CLEAN。须根绑定最终摘要及预算后执行。

UI私有候选 `developer/discard-ui-v1/manifest.json` SHA256 `37276b51c2ea26aef2998193eb1b3dc0ffa29599cc25e13765d5581e3d4e6cbd` 声明文件摘要已核，两文件修改定向静审未发现P1/P2：dirty改为与初值比较；原生confirm替换为既有Radix内确认，初始焦点继续编辑，Esc/外点仅退确认保留表单，busy仍拒绝关闭。可交生产构建及隔离页面测试；此处未运行构建、浏览器或提交状态变更，不能标记UI缺陷已验收关闭。

### A05实际目标首次启动失败与独立退出核验

根批准后，运维仅prepare/start的独立目录为 `ops/a05-r1/run-42d5f3ba670943bf83c1b1386d8d8de8`。start因ready.pid与Popen pid不同而拒绝准入：Windows venv wrapper为25784，实际执行python子进程38172；独立CIM核父子关系、同ops.py serve参数和同私有目录。没有删掉该身份断言或绕过失败启动任务，QA run未执行。只读独立新库核8类业务/用量表全0，qa-started不存在；原失败、脚本摘要、库和数据保留。

实际守护随后按原120秒规则自然合作退出。根独立审查角色于14:07:35复核ready/exited真实PID38172与nonce摘要一致，CIM两个进程均不存在，port49832可重新绑定；9表（含ask_worker_instances）全0，其他本库连接0。收据该目录 `reviewer-zero-generation-check.json`、`reviewer-exit-verification.json`。这仅关闭失败环境的运行现场核清，不代表A05执行或业务回收通过；不删除新库、不重启该批、不自动复用其准入。新wrapper身份修复须单独审查与根重新协调。

### 新环境wrapper身份修复复审

新 `ops/a05-r1-v2/ops.py` SHA256 `abcdaed1b4a381f3e2b40f867060e99352a3771cd708e041ae942843ebe862d4` 与原inputs摘要 `cb926a7a74447bee8347af1e8a7bd58d4466b82633793fbd28b8095741275ddf` 复审通过。337输入实际摘要全部一致；新脚本将ready实际PID与wrapper父子链核对，用CommandLineToArgvW精确核script/serve/本新out及CIM可执行文件，再持原生句柄核FILETIME和存活；stop绑定两个身份句柄、分别核创建时间/原命令行，再发nonce合作停止，在总20秒内确认两个句柄退出，exited.pid须为已核actual server。未删除旧失败，不复用旧准入，不强停或自动重启。

独立QA入口 `reviewer/run-a05-r1-batch-v2.py` SHA256 `9747be5a6a3d50584f98572194535ba6d0e46e9dff57119994763c421f9b4a71` 字节级仅OPS路径指向新目录，单次标记、严格预算与未知保留规则不变。新prepare后须check模式预检，根另行批准新环境start/run；本段仍未执行真实生成或计入A20。

### 新环境实际两根验证：事实符合，清理失败，整例不接收

根单独批准并执行新环境 `ops/a05-r1-v2/run-81e3698aaec149feba610a3877531631` 的实际两根工程测试后，cleanup首次close出现HTTP500并持久UNKNOWN，未重发。独立审查/测试角色只读核原始新库、两个receiver SQLite、provider状态与防篡改场景证据：一根SUCCEEDED，另一根FAILED/MCP_TOOL_DENIED；两根各2次KNOWN调用，输入20/输出10，provider各2calls/30synthetic Token、无pending/unknown；receiver各只发生一次合成lookup、write0，scope root逐项对应。原生消息分别4/5，撤权根最后result_unknown repair保留。合计实际消费4次/60synthetic Token，不退款、不重跑完整两根，仍不增加13/20。

实际清理缺陷已定位并交开发：close_scene先置CLOSING并合作停injection listener，随后SQL将 `mcp_servers.token_ciphertext=NULL`，触发该列NOT NULL约束。SQL未提交，原token仍在；injection控制CLOSING、close提交意图UNKNOWN，另一receiver控制READY。不得将500后未知请求改成确认、重发close或改旧库弥补。修复须新候选及真实PG该约束红→绿工程，不以Mock替代。

独立事实收据 `reviewer-independent-facts.json`；守护合作退出后14:14:56另行核 `reviewer-exit-verification.json`：ready/exited实际PID和nonce一致、监听端口可重bind、Worker行0、其他本库连接0，数据和旧未知现场保留。该退出证明不等于业务清理CLEAN，整例状态仍“不接收，清理缺陷待修”；生产与3100管理实例未参与本轮写测试。

### 取消编辑新Web测试部署审查

`ops/ui-discard/deploy.py` SHA256 `f5caccdd8eecec35dfba82b16c8a3897a7ff8b3899519212bc0fd264e0b0b487`、assets清单 SHA256 `18079fd1d83b355b7c04ccb4f26b9ab1942ba97422ec8c120867719351a51f22` 静审通过。独立逐项核1137个新YNGS资产、729个旧F05资产、18个required运行文件及preload/build收据摘要。新dist保留构建时同basename，两个API代理仅8100；旧dist保留可回退。

脚本仅单次合作替换归属明确的测试Web3100：持原生handle核FILETIME、CIM可执行文件/参数及loopback3100监听所属，再发原token合作停止并等退出；新Web另起nonce和日志，不裸PID终止。API/DB/3131不修改，预检正常后才停止旧Web；失败保留原始资产/日志，backout需按新Web同等身份核对后合作恢复旧dist。此为脚本静审和资产核证，实际部署、页面8项交互、数据保持及回退未在本审查执行，需根批准并验证后分别记录。

### UI部署既存目录失败后修复与R2真实PG复测

原UI部署脚本在claim/stop之前调用private_directory(HERE)失败：该项目helper强制新建目录（exist_ok=False），HERE已存在。静审漏核该helper的既存目录语义，此审查遗漏保持记录，未将首次部署记通过。新 `ops/ui-discard-v2/deploy.py` SHA256 `2c6c0e98ff7654e52b6a190cb969621a22f5a1bf4434488cac628f1ce49e6e40`，assets清单保持18079摘要；只将失败的新建helper换成既存目录原生.NET ACL核验，拒绝symlink/junction、必须受保护且只含当前用户/SYSTEM/Admin三个SID的Allow FullControl。非作者实际在API venv导入并调用相同入口验证通过（existing=true/principals3），没有claim或stop；其他部署流程不变，可交根绑定新摘要执行。

A05 R2 manifest SHA256 `d5271bbc8835d04a98f35fa3639ece6eb92b29653598e4ebdd07b0a6bb8c3259` 已静审，实际差异仅close清除SQL NULL→空串、后验None→空串，不改NOT NULL约束或原UNKNOWN。非作者复制到 `reviewer/r2-independent-09a6940cba4b447d8227fd50355fb098`，实际新PG/schema执行旧Controller红例NotNullViolation→新Controller绿例CLOSED、enabled=false、token空且Vault不能解出原Bearer；模型/观察/listener为明确替身，0生成/真实任务/沙箱。**1项PG边界测试通过、0失败/错误/跳过，1.065秒**，六类业务表零、会话零后回收该新库，187正式app源前后不变。独立收据该目录independent-receipt.json。仅关闭该SQL缺陷的工程验证，不改旧run81证据、不重发close、不自动另跑已消费4calls/60的完整案例，A20仍13/20。

### R2另行准入整例复验：独立事实和退出核证通过，交产品限定签收

根在旧4次/60消费保留的前提下另行登记新一批两根/5次75synthetic Token/120秒/0沙箱外部上限，新 `ops/a05-r2-v1/ops.py` SHA256 `5c38db16168cfa0571d7af1af31b2a15535a9a61e3ed64bf831a7ab59a5d97f5`、inputs SHA256 `9ec584727dbc421215039c9ee63b20e3fbb5e061fb112aa02a078f2482097856` 经复审仅换R2候选来源、337摘要逐项核定，身份/时限与单次规则不变。新QA入口只改OPS路径，SHA256 `ab419c7c1b0d04d0beb8715d798ff228675831e5a28496f6e7f4ec46d5a42ea4`，原R1入口/准入/失败不复用。

根实际运行后，独立测试只读核 `ops/a05-r2-v1/run-25573c80b8bc45b1bad0321cdc12f8c1` 的SQL、provider状态、两个SQLite、原生消息、绑定场景证据摘要和控制记录：两根原生结果分别SUCCEEDED和FAILED/MCP_TOOL_DENIED，各2次KNOWN调用、输入20/输出10，各provider2次30synthetic Token无pending/unknown；receiver各1lookup、0write且root scope匹配；原生消息分别4/5、撤权repair未知标记保持。两个close提交意图均CONFIRMED，control均CLOSED，MCP均disabled且凭据为空。事实收据 `reviewer-independent-facts.json`。两根共同构成一个A05 v2限定受控工程案例，不算两个业务完成数；不外推真实模型抗注入、Ask写审批或外部效果。

14:32:04独立退出核证 `reviewer-exit-verification.json`：ready/exited真实PID44804及nonce匹配，CIM wrapper40872/actual44804均不在，端口可重bind，Worker行0、其他本库连接0，DB/证据按约保留。`reviewer-cumulative-and-unknown.json` 再核旧run81的close UNKNOWN和CLOSING未修改；旧4次60＋新4次60累计8次120synthetic Token，逐批均在各自5次75之内，无退款或重放旧未知。

本审查/测试结论为“R2所述事实与清理闭合，可交产品限定签收”。此处尚不自行更新A20总数或开放生产执行，须产品核准v2限域及根记录部署范围；其余W05/W06/W07/W08/F05/F07与平台持久驱动仍按原门槛待验。
