# Agent评测独立执行器运维（阶段A）

2026-11-03。依据[用户方案](agent-evaluation-integration-plan.md)和[AE需求](agent-evaluation-requirements.md)。本文描述新脚本契约；编写时尚未安装或执行，不代表20任务、真实模型、UI或部署通过。原方案及deepeval-learning保持原样。

## 环境与依赖

services/evaluation独立pyproject.toml、uv.lock和.venv，Python>=3.12,<3.13，DeepEval固定4.3.2；实际解析内容以runner提交的锁文件及安装验证为准。API的.venv和既有部署脚本不改。本阶段是按需CLI，不增加Redis、常驻评测服务或新的SSE。

PowerShell7入口为scripts/install-evaluation.ps1、run-evaluation.ps1、test-evaluation.ps1，共用evaluation-process.ps1。使用当前本机Python3.12及uv；安装执行uv --no-config sync --frozen --project services/evaluation --python 3.12 --no-python-downloads，不自动下载Python、不更新锁文件。安装可能访问锁定的包源，这是依赖准备流量，须与运行期间出网统计分开。依赖来源、哈希和许可应随锁文件审查。当前未提供Linux shell专用入口，PowerShell7可移植部分仍需目标系统实测。

示例从仓库根执行。以下均为说明，本轮没有运行：

```powershell
pwsh -File scripts/install-evaluation.ps1
pwsh -File scripts/test-evaluation.ps1
pwsh -File scripts/run-evaluation.ps1 -Action preflight -TargetManifest <私有目标JSON> -Dataset <版本化题集JSON> -Output .local/agent-evaluations/<批次ID>
pwsh -File scripts/run-evaluation.ps1 -Action run -TargetManifest <私有目标JSON> -Dataset <版本化题集JSON> -Output .local/agent-evaluations/<同批次ID>
pwsh -File scripts/run-evaluation.ps1 -Action report -Output .local/agent-evaluations/<同批次ID>
pwsh -File scripts/run-evaluation.ps1 -Action cleanup -TargetManifest <私有目标JSON> -Output .local/agent-evaluations/<同批次ID>
```

未指定Output时安装/测试/preflight/run生成新的随机目录；需要继续观察原批次时必须明确复用原Output及幂等标识，不能把新随机目录当作恢复。report和cleanup强制指定原目录。输出只允许本项目.local/agent-evaluations独立子目录，拒绝链接路径，Windows设置当前用户/System/Administrators私有ACL。不要把含输入/输出和证据的报告当公共CI附件上传。

## CLI、认证及隔离

包装器调用独立venv的python -I -m evaluation_runner。preflight/run传--target-manifest、--dataset、--output；report只传--output且不联网；cleanup传--target-manifest、--output。schema、预算、发布版本、归属及环境身份由runner校验，包装器不另造一套业务协议。

专用测试账号通过EVALUATION_USERNAME/EVALUATION_PASSWORD子进程环境提供；不在命令参数或文档保存秘密。EVALUATION_API_TOKEN仅按runner明确支持的协议使用，不把普通平台API密钥误当Bearer。脚本不加载用户.env、不转发provider/Confident密钥、代理变量、个人云登录或PYTHONPATH；只白名单必要系统变量及显式评测凭据。测试/安装/report不接收认证变量。无生产默认目标。

目标须提供GET /api/v1/agent-evaluations/environment隔离证明：服务端显式启用评测环境、DB为_test、目录/沙箱namespace/owner/部署指纹与manifest一致；生产不开该入口。manifest中客户端自称is_test不构成安全证明，loopback地址也可能指向生产。原API当前端口状态只能按时间记录，不由脚本探测后接管。

预置只针对另建的隔离实例，绝不在生产data目录写标记：

1. 创建随机`owner_agent_evaluation_<uuid>_test`库（template0）、独立basename以`_test`结尾的数据目录及新Vault；不得从生产业务库复制。执行所测版本迁移，建立专用测试账号及最小业务/evaluation.read权限。读取该测试库的真实`work_sandbox_namespace.id`，不复制生产namespace。
2. 生成新的environment_id UUID，记录所测平台40位提交；在独立私有数据目录创建`evaluation-instance.json`，严格仅包含下例六字段。owner_id为刚建测试账号；库名和namespace必须来自同一测试实例。fixture_profile首期固定controlled-v1/version1，不允许任意代码。
3. 仅在该隔离API进程环境设置`DATABASE_URL`、`PLATFORM_DATA_DIR`、`EVALUATION_INSTANCE_ID`、`EVALUATION_PLATFORM_COMMIT`；前两项指新库/独立目录，后两项与marker完全一致。为API和必要Ask/Work后台分配专用端口及进程登记，不调用生产受管启停脚本，不覆盖生产.env。评测wrapper不替使用者创建或接管目标API实例。
4. runner正式登录后请求环境证明，再核manifest的api_base_url/environment_id/owner_id/platform_commit/fixture_profile/required_migrations。404表示未开放；503表示隔离未核实；403表示账号不匹配。任一不符停止，不通过改marker将生产伪装成测试。endpoint证明的局限仍保留：它核对受管声明、数据库和标记，不证明宿主无其他配置泄漏或任意外部服务全部隔离。

```json
{
  "environment_id": "<新UUID>",
  "owner_id": "<专用测试账号UUID>",
  "database_name": "owner_agent_evaluation_<随机值>_test",
  "platform_commit": "<所测40位小写SHA>",
  "sandbox_namespace": "<该测试库真实namespace UUID>",
  "fixture_profile": {"id": "controlled-v1", "version": 1}
}
```

以上是人工/fixture预置模板，不是可直接使用的JSON实例；不含连接串、数据目录或密码。marker、target manifest及环境证明三者有关字段一致才可运行，target manifest仍遵循runner独立schema。

隔离目标脚本骨架为`scripts/evaluation-target.ps1`及固定Python实现。`start -StateDirectory .local/agent-evaluations/<目标ID> -AdminConnectionFile <私有管理连接文件>`只创建新的随机测试库；输入文件只含管理员libpq连接串，不从生产.env寻找默认值，管理连接使用postgres管理库。执行前仍须审查/批准，当前仅静态解析，尚未创建目标或验证HTTP。复用API已有依赖，不安装DeepEval到API环境。

启动使用新生成的账号/密码/Vault及隔离marker；`target-manifest.json`不含秘密，`credentials.json`和`server-private.json`只保存在私有目录。模型/fixture实现固定加载runtime所有的`services/evaluation/profile_support/evaluation_target.py::build_app`，不接受任意Python模块路径。API使用保留的loopback socket，端口随机且不使用8100/3131；Origin和内部API URL与manifest一致。密码只通过私有文件和受控登录环境使用，禁止输出到控制台。

不传ManagerKeyFile时`sandbox_config=None`、`work_enabled=False`，只允许非Work准备，不默认读取仓库manager.key。root明确授权后可显式传入原受管OpenSandbox的key文件路径，child只临时读取构造ConnectionConfig，新增持久配置仅保存非秘密路径，不复制key值到manifest/报告/用例或新配置；此时开启真实WorkEngine。固定profile复用真实create_app/Ask队列/Workflow，不伪造HTTP响应；其实现及目标运行仍待独立审查/实测。

共享服务的隔离依据为新测试DB、namespace、工作区与登记session/container ID，不是Docker daemon隔离。OpenSandbox1.2.0另启同daemon管理器会全局恢复已有sandbox过期任务，因此禁止另建第二manager。经root授权，若原18180离线且确切`opensandbox.io/id`标签全部容器（含停止/保留）为零，才允许原start-opensandbox.ps1启动既有受管服务；若存在不明对象先报告，不自动启动或清理。安全配置不弱化，不prune用户对象。

`status`只读状态，`stop`写本目标合作停止请求；`cleanup`要求服务已停止、namespace一致、无活动连接/运行或未知操作，并向明确受管manager按本namespace+purpose只读确认无剩余sandbox，才删精确登记测试库。远端不可达/仍有对象则保留库供核对。保留数据目录/密钥/日志作证据，不强杀、不FORCE DROP、不按前缀删除，不把数据库会话为空等同容器已回收。

## 遥测、缓存和实际出网

在import前设置DEEPEVAL_DISABLE_DOTENV=1、DEEPEVAL_TELEMETRY_OPT_OUT=1；HOME/USERPROFILE/APPDATA/LOCALAPPDATA/DEEPEVAL_HOME/ENV_DIR_PATH及缓存/结果/临时目录指向批次私有路径。DeepEval可能在import时加载dotenv，官方提供禁用开关；遥测退出只管理遥测，不等于关闭模型或Confident API。[官方快速开始](https://deepeval.com/docs/getting-started)、[官方隐私说明](https://deepeval.com/docs/data-privacy)。

runner负责在DeepEval导入前安装Python进程内出网约束，按登记origin仅允许本机目标和受控judge；记录导入、实际指标执行、导出三个阶段的连接/拒绝证据。报告必须区分无请求、已允许请求、拒绝尝试和观察不完整。关闭开关、空凭据、HTTP代理或网络快照均不能独自证明完全离线；进程内观察不承诺覆盖所有原生扩展或子进程的OS级网络路径。当前不配置Confident登录、不执行真实外部模型或付费judge；评分器不得默认回退供应商。

每批隔离缓存，不跨账号/配置/轨迹复用。三次独立任务必须重置初始环境；同证据重评分单独标记，不算重新执行Agent。Work并发起步为1，其他并发、时间/调用/Token/金额预算由已冻结dataset和runner控制。

## CI和报告

业务评测输出report.json、results.xml、report.md，preflight输出preflight.json；journal登记资源和实际运行引用。runner退出码0仅表示required gate PASS；1表示FAIL/NEEDS_REVIEW；2为契约/配置；3为环境/执行器故障；130为取消。非零不能放行；必测跳过、评分未知、清理未核实不能伪成功。包装器保留runner退出码，启动配置失败返回2，子进程创建失败返回3。

test-evaluation.ps1只执行services/evaluation/tests工程pytest，禁自动插件加载，输出pytest原退出码、results.xml和ci-result.json；该JSON明确business_dataset_acceptance=false。不能把工程测试数量算为20业务任务或真实效果得分。stdout/stderr保存在私有目录，控制台仅给退出码/路径；runner仍须脱敏日志，私有ACL不替代脱敏。

CI至少核对JSON门禁、required有效覆盖、JUnit结果与退出码一致，并单列工程替身/真实业务链路/未跑外部模型。报告缺失或解析失败直接阻断，不能仅看最后一行通过字样。阶段A脚本不自动打发布标签或调用旧部署脚本。

## 取消、故障及回收

先停止新增派发，再按journal中activeRunRefs取消已提交运行并确认终态，最后只删除createdResources登记且归属核实的精确ID/路径。UNKNOWN、没有返回运行ID或远端终态未核实，不删除父fixture、不重提交写操作；记录NEEDS_REVIEW并阻断环境复用。

PowerShell包装器不强杀生产服务或扫描进程。宿主中断不等于远端已取消；需要取消时，在原批次输出目录创建空`cancel.request`文件。runner在提交前、轮询和评分前检查它，停止新增任务，并正式取消已知运行；终态未核实仍不能删除父资源。操作员等待partial报告及清理结果，再以原Output执行cleanup。文件保留作为取消证据，不删除它来伪装新运行；确需重跑时先核清旧资源，再创建独立批次。该契约不代表控制台Ctrl+C已经安全传递到远端。清理失败保留journal及受限证据，由运维按精确资源核对；不DROP生产库、不清空向量全局目录、不运行Docker prune。

升级或回退仅作用于独立评测环境：停止派发、核对旧运行后重建独立venv。保留旧锁/源码摘要/报告，不为消除错误恢复生产备份或改写原评测结论。阶段B持久worker的租约/升级/发布方案另行设计，本阶段不借CLI宣称已有持久后台。

## 待执行的验证

审查后再安装并记录Python/DeepEval/OS/锁摘要；验证环境白名单与插件隔离、真实DeepEval指标正反例、JSON/JUnit、三阶段出网及错误目标拒绝、取消/清理故障。20任务的正式HTTP三模式验收、真实沙箱、真实UI和部署各有独立门禁；本文和脚本存在不代表它们通过。

包装器作者调用补记：root读审包装器后，使用runtime独立venv实际执行test-evaluation.ps1，输出`.local/agent-evaluations/ops-selftest-62c8ecf3/`，pytest工程组21 passed/1.53秒，JSON/JUnit生成、包装器返回0。没有安装依赖、连接既有8100/3131或调用外部模型；该结果仅是包装器调用工程测试，不是核心独立QA、平台HTTP或20业务任务验收。profile及target骨架仍未运行。

## Suite协调器草稿与批次预算（尚未运行）

新增evaluation-suite.ps1/.py及evaluation-suite-tests.py。当前仅工程实现/静态解析；固定profile init/observe/cleanup及sandbox上界契约未冻结，协调器在创建任何目标前会BLOCKED，不能拿A02启动映射冒充20任务。CLI示例仅说明显式预算，不代表批准这些额度或已经执行：

```powershell
pwsh -File scripts/evaluation-suite.ps1 -Dataset <冻结A20.json> -Output .local/agent-evaluations/<全新suiteID> -AdminConnectionFile <私有DSN文件> -ManagerKeyFile <获授权原受管key路径> -CandidateCommit <40位SHA> -RulesId <冻结规则版本> -MaxSeconds 7200 -MaxModelCalls 200 -MaxTokens 400000 -MaxSandboxes 32
```

协调器固定调用受审target脚本和`evaluation_runner.fixed_run`的preflight/init/run/cleanup；代码没有dataset可传入的executable、module、shell或SQL入口。fixed_run为runtime待审接口，不是已经完成的core命令扩展。每case/repeat新DB/owner/data/Vault/namespace/结果目录，单并发且最多一个未回收目标。父suite保留原datasetDigest、caseDigest、repeatOrdinal、candidate commit、规则ID/规则内容digest和实际源码/锁摘要；子dataset独立ID且repeats=1，不能把重复次数算成不同任务数量。fixed runner还须复核原catalog/case定义，不仅相信客户端摘要。

父预算在目标创建前预留该case声明的最大调用数、Token及经审核profile给出的沙箱上界；已经派发的额度不因失败或UNKNOWN返还，不能靠重建目标清空父预算。可信sandbox上界必须覆盖保护verifier等额外环境，来自已审源码契约`profile_support/suite-capabilities.json`，不能采用dataset自报数量；该契约和执行限制尚未交付时禁止派发。时间到或父cancel.request出现后停止新增命令，向当前子输出写合作取消文件，不强杀/重放；远端停止未证实仍为REVIEW，不宣称精确进程截止或零副作用。

清理只消费已审fixed_run的实际结果：cleanup未确认、UNKNOWN或Task删除409不能当作204。保留历史行及未决目标/DB/日志并占满唯一容量槽，停止派发。确认任务/namespace资源已回收后才合作stop、核stopped、用target精确DROP本测试库，保留证据文件。事实FAIL仍FAIL；预期故障被捕获作为工程控制单列，不改成业务成功。父JSON/JUnit分别记录FAIL、REVIEW与未执行，进程退出与报告gate不一致不能PASS。费用中外部模型为NOT_EXECUTED，未定价基础设施为NOT_PRICED、完整总成本为null，不能填零。

工程测试先交root审阅再运行，且只测预算停止、固定命令、重复/摘要和JSON/JUnit，不启动目标、模型或20任务。新freeze后须先完成真实单case，再经profile审查和明确门禁决定是否运行20。

非Work目标补记：第二个全新测试目标实际经过原API lifespan，专用账号登录及GET环境证明200、未登录401；唯一A02模型通过正式admin创建和sealed ID绑定，manifest实际mapping非空，未调用模型/执行case。首轮target子环境SYSTEMROOT大小写过滤错误已修，原失败日志/库留证；随后中文UTF8题集暴露core默认cp936解码错误，原失败exit2保留。UTF8修复独审closed后原目标CLI preflight重放exit0/status ATTESTED，任务/模型用量表仍0；这不是A20 readiness。成功目标原声明ae69f50b6，首个失败目标178c38，重验CLI当前HEAD9df446cdf，均按原记录分开，没有改旧marker冒同候选。私有证据见`.local/team-workspace/agent-evaluation/non-work-target-result.md`和`target-preflight-693ecbe1/utf8-preflight-recheck.json`。

### Suite O1–O4静态审查修复（待复核/工程执行）

root静态审查发现候选可错标、固定执行输入未闭合、CLI调用误计业务启动、早退未必停止目标四项。当前修复候选先等于真实HEAD，每个子目标manifest也必须一致，再由fixed preflight进行正式HTTP attestation；共用纯stdlib `evaluation_sources.py`覆盖API/执行器/所有profile_support代码、迁移SQL、题集、脚本及依赖锁，重算文件集合能发现新增/删除，不仅比旧列表hash。

执行输入选择“固定注册stock完整校验”路线：派发前固定调用fixed_run describe，原catalog/Case规范digest、rules_id和implemented必须完全一致；init/run仍传suite_context让固定runner再次核对。digest直接复用已审journal.digest。子dataset用于审计和repeats=1，不假称runner使用与已登记stock不同的输入。可用`-CaseId AET-A02`选择原catalog内子批、`-RepeatCount 1..3`设置独立重复，不改原catalog/case定义digest；重复仍不算新的唯一任务。

计数分别保存dispatch_attempted、business_started/root_run_ids、unknown_submission。业务启动信号必须来自真实report/journal的started和已返回合法UUID根运行；CLI预算阻断不计为已开始业务。该信号说明平台已受理根运行，不自动说明模型/工具已执行或任务成功。

每个子目标进入try/finally：无论start/preflight/init/run失败，均尝试正式cleanup/取消，再单独尝试合作stop，前者失败不短路后者。未证终态、未知提交、缺stop证明或远端剩余时不DROP，保留记录并停止派发；finally完成后才写父JSON/JUnit。target自身核stopped.environment_id、state快照末次一致、model_usage PENDING/UNKNOWN阻断；不改UNKNOWN换清理成功。

新工程反例同时覆盖四项和target身份/未知用量。当前仅AST/PowerShell语法检查，未运行工程pytest或A02；待root复核新冻结清单后执行。旧两个自有预检目标也等此审查后，按精确身份、真实进程/连接和namespace条件回收；首个旧版失败目标不得伪造graceful，需带实际核查来源的startup_failed receipt。

O6/O7追加修复：每attempt完成正式清理/stop后再次核HEAD与完整source集合，最终aggregate前再核一次，覆盖最后一个case期间变更而没有下一轮检查的窗口。来源变更使已PASS样本转REVIEW并在JSON/JUnit一致阻断，已有事实FAIL保留。run非0/1或退出码/report不一致仅追加infrastructure_error，不能把已持久FAIL改REVIEW；即使最终report缺失，也从journal保留必需事实FAIL。新增末例HEAD/内容/文件集合漂移和code0/3/130与事实FAIL、缺report但journal有FAIL反例。仍待root审阅后才执行工程测试。

### 后续工程QA与契约记录

root独立读审关闭O1–O7后，非作者执行suite工程组首轮10 failed/17 passed：统一题集夹具仍引用被strict registry拒绝的final_json，未进入目标/数据库。仅将测试夹具一行改为真实output_equals/expected={}并经root复审，独立重跑**27 passed、0 skipped，0.33秒**；target独立工程组此前**4 passed**保留，不重复跑也不混加为A20。源码冻结前后核一致。证据分别为`.local/agent-evaluations/ops-independent-064c17ddf3a5408385d7b46b89e162e2/`和`ops-independent-recheck-3281e10d9b474118b12ae7f6480b3648/`。首次失败日志不删除，不放宽产品registry。

profile扩展PF1/PF2由本角色非作者独审发现并关闭：精确4冻结工程测试文件18项加原2独立反例，20 passed/0 skipped，1.95秒；详情见[profile审查报告](agent-evaluation-profile-review.md)。首轮目录收集多含2个未审WIP测试，额外结果保留而不计正式范围，已按精确文件重跑。没有启动真实API/PG/manager/容器或付费调用。

最小suite-capabilities.json已生成，只登记A02及fixed_run d51f529...，保守sandbox上界2与A02无沙箱创建步骤分开记录；SHA3abdab11f794347dcd2efccba54d9077b084a141c35cf588f18bcd6d9ef4f8b4。等待root核对并提交所有已审source形成固定HEAD，再全局短暂停更源、生成新目标，才进行正式A02单例。原两个旧自有目标尚未回收，依root后续授权按身份/namespace/unknown用量/真实进程条件处理；未触及其他用户服务。

旧预检目标回收补记：root核读父MD及29项独立工程测试后授权，已回收f7d21a9c、693ecbe1两个精确自有测试库。先核每库真实namespace、0业务任务/用量/沙箱会话、PENDING/UNKNOWN0及该namespace Docker清单为空；F7实际无PID/匹配serve进程且数据库连接0，用有CIM/SQL/原WinError日志摘要来源的startup_failed收据，明确graceful=false；693只发本目标合作停止请求，真实shutdown留下graceful=true。随后原cleanup两次退出0，无FORCE；后核两库不存在/会话0、scope进程及namespace容器空。数据目录、私有密钥文件和原失败日志保留，不回补旧A02业务事实。总收据`.local/team-workspace/agent-evaluation/old-target-cleanup-summary.json`引用各final-cleanup-receipt.json；未接管用户8100/3131、未改或重启原manager、未重发任务。
