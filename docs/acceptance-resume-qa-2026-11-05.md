# 本轮测试执行记录（2026-11-05）

状态：执行中，尚未产品验收。目标及条件见 [测试计划](acceptance-resume-test-plan-2026-11-05.md) 和 [产品验收标准](acceptance-resume-product-2026-11-05.md)。本记录不覆盖历史失败，不将工程、接口与页面测试混计。

## 初始检查记录（历史，后续实际结果见本文末与运维记录）

- 原环境归属核实：3100原正式库，3131原测试库；换位尚未执行，未在3100提交写测试。运维已冻结正式351源码及727前端资产，并为新3100准备独立空测试库、账号、主密钥和目录。
- DEF-ENV-01协议层反例：同host不同端口、相同Cookie名称会覆盖；零网络CookieJar重现，收据 `.local/acceptance-resume-20261105-root/cookie-protocol-reproduction.json`。不是平台登录或浏览器通过。程序员正在准备主会话/MFA/OIDC三类Cookie隔离候选；G0仍阻断。
- W05工程回归：新隔离PostgreSQL中现有9项正式预算回调测试通过，0失败/错误/跳过；产品源码摘要前后一致，无模型、实际任务或沙箱。新库已精确回收，旧真实失败及UNKNOWN不变。收据 `.local/team-workspace/acceptance-resume-20261105/developer/w05-pg-d55d40a0e11f4decb2be39b99d3288b8/receipt.json`。该项为开发自验事实，仍不能替代修后真实W05。
- 浏览器仅已有127.0.0.1:3100打开/读取登录页证据；登录及管理流程待隔离准入后执行。3131实际工具拒绝保留；禁止绕过或把API结果计作UI通过。

## DEF-ENV-01 已审候选独立工程复验

Cookie-v1六产品模块已获非作者静态审查通过。根测试另建私有副本及3个全新隔离数据库：先以旧正式源重现共享CookieJar覆盖后的401（预期红例），再执行候选15项及默认账号/身份协议兼容41项，均0失败/错误/跳过。187个app源码文件集合相同，仅6个已审文件不同，正式源码前后未变；成功库全部按精确名称、会话零和业务表零检查后回收。

独立收据 `.local/acceptance-resume-20261105-root/cookie-independent-38a2c7134efe483f997297fc0348d8c4/independent-receipt.json`，SHA256 `d2dbe55f5cb701070b3c97626bc8c95d96568737a2c87aa61671b4df91b56d11`；候选manifest SHA256 `d20c70b46e9dd184a2ae84e9a20cc32c8d1d40a9f98df9ac5e2b3c28e426cef4`。MFA/OIDC是隔离实例/受控协议验证，正式受限模式对应门禁保持，不称外部IdP联调或正式MFA开放。

不同Cookie名解决会话覆盖/误清和实例选择，不提供同host跨端口的Cookie传输保密隔离；精确Origin与分库认证仍不可替代，日志不得包含凭据或Cookie。实际双地址浏览器/进程登录、部署与产品G0验收仍待换位后独立核验。未增加A20业务案例。

## 沙箱管理基础恢复

根角色恢复18180管理服务，配置、key及可执行文件摘要与既有pins一致；SDK认证库存读取通过、页内0条。没有创建沙箱、任务或模型调用。证据 `.local/acceptance-resume-20261105-root/manager-*/receipt.json`（使用实际生成目录，不按历史PID停止）。这仅证明管理服务可用，不是Work或Workflow业务任务验收。

## 初始待办（历史，不覆盖后续闭合项）

1. Cookie修复代码审查、自验与非作者复验；环境换位脚本审查、维护准入、合作停止、部署与数据守恒核验。
2. 环境隔离通过后执行G1页面及管理链；Bug交开发修复、审查后复验。
3. W05真实新批及其他A20案例按各自候选/预算/环境准入执行；A05/W07/W08/F07未完成处理器先开发。
4. 产品逐模块签收，未覆盖范围继续标为未验收。真实外部服务按原要求暂缓。

当前A20仍13/20限定验收，正式完整执行保持暂停。没有声明全系统无Bug、全部测试通过或已全量上线。

## 3100测试环境继续验收（2026-11-05 13:29起）

根测试重新运行只读环境核对：测试3100→8100→独立port_switch测试库，正式3131→8101→原owner_agent；四个HTTP健康入口200，351测试源摘要匹配，正式六组原业务计数及主密钥摘要保持。新测试库保留前轮9个已停用QA账号和原测试管理员，模型/任务用量零。收据 `.local/acceptance-resume-20261105-root/live-environment-independent.json`。此前换位、缺失两manifest的404修复和容量失败/重采样以[运维实际记录](port-environment-switch-operations.md)为准；本段不将旧失败改写为通过。

Chrome5在本轮新标签421574799成功打开3100，实际观察并保存四组截图与AX：原测试身份跨标签保持；评测集列表与详情显示前轮已创建的合成题集、修订2及发布v1；批次列表为空且准入阻断、不提供执行按钮；退出回登录页；一次明确错误密码显示中文拒绝；正确原测试凭据重新登录；主动刷新后保持工作台会话。独立语音合成/图片理解导航仍隐藏。未修改模型、执行限制或原正式数据。

证据目录 `.local/acceptance-resume-20261105-root/ui-3100/`，`receipt.json`登记四个JPG和四个AX文件摘要。根另以只读PG确认题集 `eb2e57b9-3d4c-4bfc-a4d6-5c4b19ee7fbc` 仍revision2/current_version1，定义摘要 `9ee313bdf1b7cf8f6d1a070135732c9cbe7b47da3f3040c32ee4fdd78a5cb835`，唯一不可变v1摘要相同。前轮取消过程中编辑的说明未落盘；这只证明未保存，不能证明确认框取消交互已通过。九类执行/模型/效果表均0。题集是本轮合成复现数据，产品无正常删除接口，保留并说明，不全局清空。

前轮新增、错误JSON、有效预览、保存和发布的操作有实际工具AX记录；本轮补的是重新打开后的持久状态，不伪称前轮动作已补录截图。只读/跨账号、并发冲突、窄屏与确认框完整UI交互仍未验；正式3131实际浏览器测试仍缺前提。先前浏览器原生确认框处理超时及策略终止保持原件，本轮新标签恢复不自动关闭此缺陷。

程序员定向发现评测表单dirty判断把“已有非空初值”也视为修改，未改编辑也触发放弃确认；正在准备最小产品内确认交互候选。A05完整v2首次独审发现两处真实链阻断：实际AskWorker新建MCPService未接归属工厂、原生preserve_partial补偿消息与原判据矛盾。已交开发R1修复，先复审，不以34个Mock工程通过启动实际生成。当前没有新增模型、沙箱或A20业务完成数，仍13/20。

## 继续测试：R1复审与页面补证

实际重新核验四个服务HTTP200与正式原计数/密钥保持。原Chrome5连接不存在，库存显示新的Chrome4连接；在同一已允许测试地址3100成功恢复页面，不是绕过站点拒绝。实际展开合成题集“定义、摘要与不可变版本”，AX显示current_version1及v1定义摘要与草稿一致。保存 `.local/acceptance-resume-20261105-root/ui-version-expanded/expanded-ax.txt`、`expanded-full.jpg`；完整历史编辑/冲突UI不因这一只读展开自动通过。此前语义定位器未匹配details摘要后，按实际AX控件点击成功，保留这是工具定位差异。

知识库页面实际创建合成 `QA-20261105-关键词验收`，未配置Embedding，列表及详情均显示关键词检索/0文档。实际文档上传在选择器 `setFiles` 阶段被Chrome扩展要求启用Allow access to file URLs，未发生上传；不绕过权限、不将入口成功算文件导入/检索通过。已关闭上传表单，保留空合成库。`.local/acceptance-resume-20261105-root/ui-knowledge/created-empty.jpg`和AX记录当前状态；完整页截图曾超时，普通截图成功，均不算产品缺陷。新上传与检索UI标为环境阻塞，其他测试继续。

A05 R1 manifest `1dfb0848eef3418aab7681b4897cfe9fa43b32fbbab19cae7614f9d0ee23c61e` 已独立审查通过，复制候选后36项无生成工程通过，实际覆盖Worker新建MCPService真实HTTP归属头和原生Runtime补偿消息。证据 `reviewer/a05-r1-independent-bfa1721bf75d45a5a568cc78852fb36e/independent-receipt.json`，原v2两P1及旧失败仍保留。不增加A20；真实两根待独立环境/QA入口核对和根准入。

评测取消/dirty两文件候选已静审通过，进入源码集成与独立目录生产构建；未修改正式3131冻结资产。是否关闭缺陷，以新构建3100真实继续编辑/放弃/键盘保护及持久验证为准，不能凭tsc/ESLint通过关闭。

## A05 R1实际执行与新缺陷（未通过验收）

第一次独立环境 `ops/a05-r1/run-42d5f3ba670943bf83c1b1386d8d8de8` 启动断言失败：Windows venv wrapper PID不同于实际服务子进程。未调用QA run或handler initialize。运维和独立测试均核实际父子进程退出、端口释放、Worker与模型/业务表零、未创建provider state；旧DB与失败保持。修复父子原生身份识别的新运维脚本经复审，根只在零业务实证后批准新环境尝试，不改旧审批、不重放业务或退款。

新环境 `ops/a05-r1-v2/run-81e3698aaec149feba610a3877531631` 的337输入、迁移85和QA import check通过，根批准按原2roots/5calls/75 synthetic Token/120秒/0sandbox/0external合同实际启动一次。宿主识别wrapper38340与actual42752成功。实际固定处理器initialize及两场景execute完成；**cleanup首个receiver close POST返回500，Journal保守标UNKNOWN，QA退出1，整例未接收**。

原始日志根因是 `evaluation_a05.close_scene` 的停止监听后SQL将 `mcp_servers.token_ciphertext` 写NULL，违反NOT NULL约束。任务执行、receiver实际效果、provider/native用量与CLOSING/UNKNOWN状态正在独立复算，不能因execute返回PASS关闭整例。程序员已接缺陷，修复新候选及真实PG无生成回归；不重发旧close，不改旧UNKNOWN，不自动重跑本批。运维只按已审身份合作退出，退出不等于CLEAN，旧DB留证。

取消UI仅两源已集成并完成独立目录生产构建，BUILD_ID `YNGSgFoYy443HjMLFVJky`，代理8100；当前仍待测试3100实际换Web及浏览器复测，不宣称正式部署或缺陷闭合。A20仍13/20。

## 修后新批与页面部署结果（2026-11-05）

A05 R2仅将错误NULL清除改为标准空字符串，候选d5271bbc经独审、真实新PG旧红新绿与36项无生成回归通过。根另行登记新2roots/5calls/75synthetic Token/120秒/0sandbox/0external工程批，不退款或修改原失败4calls60/UNKNOWN。

新批ops/a05-r2-v1/run-25573c80b8bc45b1bad0321cdc12f8c1实际initialize→execute→cleanup退出0。独立SQL/provider/SQLite复算：正常根SUCCEEDED，撤权根FAILED/MCP_TOOL_DENIED；两根各2次KNOWN、输入20输出10，各receiver仅1次lookup、0write；close意图均CONFIRMED、control CLOSED、MCP停用/token清空。原生result_unknown消息保留。进程nonce/API/receiver端口、Worker及其他连接退出已核，DB留证。旧新累计8calls120。收据reviewer-independent-facts.json、reviewer-exit-verification.json、reviewer-cumulative-and-unknown.json、ops-stop-observation.json；independent-report目录包含真实独立事实离线JSON/JUnit/Markdown与SHA，1案例2根，不冒充平台下载/持久驱动或真实模型效果。最终计数按产品结论。

测试3100已实际部署YNGS，正式3131/API/DB未换。首次Web部署FileExistsError发生于停服前，修后实际ACL预检/独审才部署，原失败保留。根Chrome4六项实测通过：未改关闭、确认默认继续、继续保留输入、Escape保留输入、明确放弃恢复原值、改回原值直接关闭；PG仍revision2/v1。截图及AX在.local/acceptance-resume-20261105-root/ui-discard。遮罩/窄屏/busy/完整Tab环未测。

已审Cookie六模块已精确集成，原独立15+41测试证据保持，正式API未因此重部署。本地提交e3f6c7517(UI2文件)、2d46cda77(API6文件)，分支onwer_agent；推送记录见releases/history.md，不含任何本地密钥、数据或构建。

产品最终第十节已核原始事实/退出/累计用量与独立JSON/JUnit/Markdown一致，限定接收A05 v2一个案例，覆盖更新14/20；剩余W05/W06/W07/W08/F05/F07。六项取消/dirty交互限定接收，未测条件仍公开保留。正式完整执行未开放。
