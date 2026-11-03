# BrowserSkill 后端接口与运维交接

2026-11-03 开发稿；migration 081 未应用至生产。独立工作树 `onwer_agent_browser_skill`，不得对主数据库或现有日常浏览器试运行。

MVP 支持 navigate/click/fill/select；其余录制动作原位保留阻塞原因，不删除后假装完整。单主机 local 连接无 per-owner profile，因此 live status/start 与 Work 执行必须管理员，普通账号仅能读写本人安全录制及 Skills；每次 API 和后台录制/每步执行重查权限。

接口统一 `/api/v1/browser-skills`，沿用身份验证、Origin 校验和 skills.read/manage。公开字段不含磁盘目录、CLI命令、原VOM、原始fill/select值和录制ref。

- GET `/status`：`ready,code,message,connections`；连接为 `{id:"local",name,version,busy}`。依赖未登记/离线/版本错/忙有明确code；非管理员无连接详情。
- GET `/recordings?limit=30&offset=0`：`{items}`，只含本人，limit1–100。
- POST `/recordings`：`{connection_id:"local",start_url,title}` →202 `{id,status:"starting",revision:1}`。url仅显式无凭据/敏感片段/查询参数的HTTP(S)；后台执行官方record start，启动确认后recording。不可给caller传browser id、文件或home。
- GET `/recordings/{id}`：`id,owner_id,connection_id,title,description,status,revision,draft,error_code,skill_id,skill_version,created_at,updated_at`。
- `draft:{steps:[{id,op,url?,target?,parameter?,blocked:[]}],parameters:[{name,description,required:true}]}`，target仅安全role/name/ctx。blocked非空不可发布。
- POST `/recordings/{id}/stop` 或 `/cancel`：无body，202，terminal状态幂等。cancel只清本record；restart未确认记录保持stopping/BROWSER_INTERRUPTED并隔离连接，不自动重录，见下方最终P1修复合同。
- PUT `/recordings/{id}/draft`：`{revision,title,description,parameters:[{name,description}]}`。参数按原序重命名，合法且唯一；CAS冲突409；不能删除动作或解除捕获阻塞。发布后不可编辑原snapshot。
- POST `/recordings/{id}/publish`：`{revision}` → `{skill_id,version}`，显式发布正规private Skill，不改绑定、不网页执行，重试返回同一版本。
- DELETE `/recordings/{id}`：JSON`{revision}`，活跃记录先取消，独立已发布Skill保留（现有Skills可停用/删除），返回`{ok,published_skill_preserved}`。

BSK09：POST `/api/v1/work-tasks/{id}/runs` 新增 `allow_local_browser:boolean=false`；只有admin且environment=browser可true，每次新运行/恢复副本必须重新明确勾选。它不是TaskInput或Agent配置字段；冻结到run.allow_local_browser与agent_snapshot.allow_local_browser。自动/工作流派生run列默认false，automation额外覆盖snapshotfalse，不能继承。GET `/work-runs/{id}` 返回该bool。历史缺字段不提供本机工具。

Work 仅以上两处授权均true且管理员、browser任务、已有skill_ids绑定才提供 `run_browser_skill(skill_id,version,parameters,execution_boundary,skill_name,step_count)`。execution_boundary为required Literal `local_authenticated_browser`，skill_name/step_count与发布snapshot精确核对；全部参数入single-use审批digest。metadata含`approval_scope:entire_recorded_path`和中文本机登录态/非隔离风险。审批是**整条已展示路径一次批准**，不是逐点击人工确认；UI从现GET `/capabilities/skills/{id}`的files.trajectory.json读取安全步骤摘要并核对version/count，不信任模型自由编写摘要。普通Ask只读Skill说明，需创建Work才能运行。

工具requires_approval并以既有OperationJournal消费单次整组凭据；每步重新读取DB冻结run授权、lease/取消、owner/版本启用、顶层导航域、管理员/权限；fresh strict cooperate sync观察VOM1唯一role/name/ctx后使用当前ref。任何中途失败保守记UNKNOWN，不自动重试写操作或整条路径，结束仅停止自己session。

运维准备：运行 `scripts/install-browser-skill.ps1`（可选-Proxy仅本次下载），校验固定官方0.4.1摘要并保留MIT；不修改系统代理或PATH。已有依赖不覆盖。仅在专用新空Chromium125+ profile加载解压extension。由受监督服务启动固定路径 `bsk daemon start --foreground --port <dedicated-port>`，环境 `BSK_HOME=<PLATFORM_DATA_DIR>/browser-skill/home`、`BSK_AUTO_UPDATE=off`、`BSK_AUTO_START=0`。扩展明确连接这个专用端口，登记 `BROWSER_SKILL_EXECUTABLE` 为受管exe绝对路径、`BROWSER_SKILL_INSTANCE` 为专用扩展返回的instance_id。API/Work必须同一PLATFORM_DATA_DIR，不能指向用户~/.bsk。服务脚本应沿用现有start-managed-service监督；本次未自动注册开机启动或影响生产。

受管launcher交接：API/Work也支持固定 `<PLATFORM_DATA_DIR>/browser-skill/connection.json`，status动态重读。严格7键：`schema_version:1, executable:<absolute bsk.exe>, instance_id:<8 lowercase hex>, cli_version:"0.4.1", extension_version:"0.4.1", protocol_version:"1.8"`。UTF8、最多4096bytes、不允许symlink/额外字段。此文件由ops在实际版本和扩展握手通过后原子写入，不由网页传入；PID/profile监督信息另放state文件，不混入connection。运维start/stop/check及typed launcher由独立ops角色交付，不修改原.env或密钥。

局限：当前Work只允许已授权HTTPS域名，并在每步前后检查唯一Agent tab目的地；本机扩展并非网络防火墙，网站跨域请求/重定向可能已发生后才被发现，不能宣称等同原隔离浏览器网络边界。此限制需主集成负责人决定发布门，不能自动放宽原授权。

开发自验（不等同独立QA）：新独立*_test数据库中 API/trace/Work审批替身及原浏览器校验共12通过，源码freeze hash未变，新文件Ruff通过。官方CLI/扩展真实专项：专用新Chromium149空profile实际加载0.4.1扩展，独立52983 daemon握手protocol1.8；自建toy origin `http://127.0.0.1:62175`，测试框架鼠标/键盘产生真实navigate/fill/select/click，扩展“结束”导出，平台import_bundle规范化后共享replay_step在新session重放，真实DOM output由录制`FixtureAda8672:blue`变为参数化`ReplayGrace291:blue`。不是手工拼JSON，也不是人类手工验收。证据 `.local/browser-skill-e2e-1793682516109/real-extension-result.json`。专用sessions/browser/daemon/fixture server已关闭。

专项证实并纠正初研：CLI state.page为`s1.txt`，文件在`states/s1.txt`；v3 reducer的--redact-values也遮普通fill且省略select值，因此正式录制不传flag，普通原值仅临时导入全参数化，password本身仍redacted而阻断发布；cooperate sync返回state.mode='ready'，没有state.ready字段。真实测试已捕获这三项替身测试无法发现的差异。

未覆盖：完整Work审批+真实扩展端到端、独立代码审查/QA、生产部署和外部站点。未访问3100/3131或日常浏览器。HTTP fixture仅直接调用共享replay_step验证机制，生产Work工具的HTTPS授权未放宽。测试结束递归清理其生成profile/bundle的命令被自动审批审查拒绝（blocked by policy），未换方式绕过；残留只含虚构fixture数据，待主负责人审查后清理。

### 后续组合验收与Windows修复（取代上段完整Work组合待验状态）

已补独立HTTPS组合：空profile内route精确履约`https://example.com/owned-bsk-fixture`为自建HTML，其它HTTP(S)请求abort，不读取真实外站；官方扩展实际录制fill/select/click，importer补明确entry导航并通过真实publish API发布。隔离PostgreSQL真实StartWork授权→WorkQueue.claim→WorkEngine.agent_graph与原build_work审批中断→WorkEngine.pause→decisions API→consume_approvals→Command resume→真实OperationJournal→实际官方CLI/扩展新session四步执行。

拒批分支host CLI调用为0、没有operation receipt；批准分支真实扩展DOM观察验证`WorkApproved291:blue`，operation状态SUCCEEDED，fill/click各一次，同一receipt再次start被PermissionError拒绝。payload包含固定本机边界、技能名/version/step_count和参数，中文审批description准确声明完整路径及无网络硬隔离。证据`.local/browser-skill-work-e2e-1793683567049/work-approval-evidence.json`，专用browser和daemon已关闭，未清理此前被拒的fake raw目录。

组合暴露并修复Windows SelectorEventLoop不能create_subprocess_exec的问题；BSK CLI现在通过受控Popen+asyncio.to_thread运行，固定参数数组/no shell，timeout终止与取消launch清理都保留。未更换API/psycopg事件循环。

最后组合回归27通过（含真实组合1项）；全仓freeze hash在根并行UI编辑时false，仅capability-center.tsx变化，后台源hash未变。随后新增connection manifest严格读取/reload测试，unit Work集16通过。替身仅本地ToolModel、StateBackend文件层和HTTPS本地履约；未声称覆盖WorkEngine完整sandbox生命周期、资源/计费回调或生产应用页面UI。仍需独立review/QA及部署，不把本专项等同全部生产验收。

### BSK-BE-01 独立审查修复，待复审

取消API只提交stopping并给原record job发送停止信号；该job是唯一record stop/临时清理所有者。正常停止、取消、权限撤销/异常统一在finally先保持stopping，确认CLI进程退出及record-session释放、bundle/recovery清理成功后才能写review/cancelled/failed。重复取消共享同一job，取消意图不反转，不产生第二个stop命令。

无法确认session释放、清理失败或进程launch被取消而无法确认所有权时，记录保持stopping并创建持久recovery-required。重启不先标failed来释放连接；旧活跃记录继续stopping/BROWSER_INTERRUPTED且隔离。需运维明确确认专用进程/会话已回收后恢复，不因刷新、重复取消或自动重启静默清除。此状态继续阻止录制、Work和部署准入。

新增真实DB并发回归覆盖：blocked stop期间取消/异常仍busy；新start与Work均拒绝；重复取消仅一个stop；确认清理后终态；未知停机保持隔离，重复取消和restart不解除。发布另保留审阅title/description及参数说明：合法唯一slug不变，description使用JSON兼容YAML编码，正文元数据JSON编码，恶意换行/引号不能注入frontmatter；不带原始录制输入。

修复后定向49项通过（含新增生命周期、恶意发布元数据、Ops deployment_gate与原Work/trace），后台/测试hash稳定，新修改文件Ruff通过。此为开发验证，独立review仍须复审通过后才能交QA；没有为此重开生产页面或访问3100/3131。
