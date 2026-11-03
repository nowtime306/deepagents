# 工具传输、OAuth与ACP实施

2026-11-02，依据EXP030～032。保留原HTTP默认、Ask只读和Work逐次审批。新增073专用表，原密钥不清空；全局入口由主协调集成，模块不改main/security。

先交旧SSE与OAuth：现装mcp2.3 SDK的sse_client已经校验消息端点同源及重定向；继续用validate_target限定网络。OAuth使用预注册client、RFC9728资源发现与RFC8414授权服务器元数据、S256、一次state、resource绑定、账号隔离、加密token和受锁刷新。不使用企业登录token。外部授权真实效果待接；测试用本地授权端。

stdio/ACP必须复用已批准Work环境和命令执行，不在宿主启动命令；不增加自动npm/pip下载。ACP固定v1初始化、新会话、提示、更新、取消，拒绝未实现文件/终端能力。官方参考：[MCP传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[授权](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)、[ACP v1](https://agentclientprotocol.com/protocol/v1/overview)。本文件分项如实记录进度，不用仅配置可保存冒充已经可执行。

## 实现与自验（待独立代码审查）

已实现：MCP默认HTTP保持兼容，新增旧SSE真实SDK传输和同源消息约束，HTTP客户端不继承宿主代理；配置明确streamable_http/sse/stdio/acp。隔离协议不提供宿主命令执行或自动下载，只允许镜像内Python/Node绝对执行文件与有界参数，当前镜像没有对应程序时真实报错。MCP stdio发现/调用与ACP v1会话/提示/更新使用本任务WorkBackend，冻结完整命令pin、动态绑定撤权、逐次审批、同名原始参数单次journal和有界超时/输出；不向ACP声明终端/文件能力，代理请求明确拒绝。进程继承Work命令组，结束/取消由既有命令组清理，不创建可脱离清理的子会话。Ask不提供stdio/ACP，中文界面明确仅Work。

OAuth独立router为app.mcp_oauth.router；/api/v1/tool-oauth/{id}/client GET/PUT仅管理员；status GET、authorize POST、grant DELETE和callback GET为本人授权。原资源绑定权限仍在调用时检查。元数据资源必须完全匹配，issuer/端点受网络策略约束；回调state单次且按owner消费，client修订/URL变化拒绝。令牌和verifier加密；刷新按账号+连接串行，原OAuth授权不能借普通Bearer回退；本机撤销先删除，远端有撤销端点时尝试一次并报告未确认，未确认不伪称供应商已清除。

自验13项通过（11.07秒），日志`.local/team-workspace/platform-expansion/development/tools/selftest-final2.log`。包含本机OAuth完整PKCE/resource/state重放/账号隔离/刷新/撤销，真实SDK旧SSE发现，stdio在Work先中断审批、批准后保留原始参数、改命令pin拒绝且Ask不提供；两个128MiB无网络原生容器执行真实stdio/ACP JSON-RPC；原HTTP写审批/未知不重放回归。Python Ruff、前端TypeScript和两个变更组件ESLint全部通过。新增测试位于test_tool_expansion.py。

早期自验真实发现：SSE继承系统proxy导致本机502，修复trust_env=False；角色测试共用向量目录导致锁争用，换独立DB+目录后消除；换密钥目录却沿用已有测试DB使seed模型凭证不匹配，改成全新成对测试资源；SDK子进程被宿主user-site包污染，设置PYTHONNOUSERSITE=1后真实SSE正常。不放宽warning或业务断言。测试分别使用owner_agent_tools_verified_test与services/api/.local/test-tools-verified，未接触生产模型与用户资源。

后续门槛：独立代码审查→修复/复审→测试角色验收→主任务注册router/生产构建及统一回归。上述为程序员自验，不冒充独立测试通过。真实外部MCP/OAuth/Agent CLI效果仍按用户要求未调用；stdio/ACP脚本需预先准备在任务工作区，默认不自动安装依赖。

## 首审修复与复审交接

独立首审发现5项问题，详见`platform-expansion-tools-review.md`。本次仅修改对应OAuth/隔离协议模块及测试，不扩大WorkBackend的统一执行上限。

- R-TOOL-001：回调令牌交换后，最终提交与撤销、刷新使用同一个账号/连接事务锁；按固定顺序锁定当前服务/客户端，复核flow仍存在、有效期、配置pin和client修订后才写入grant。撤销同事务删除grant/flow，客户端更新同时删除flow。回调网络等待期间撤销或改配置，旧回调均不得恢复授权；回放保持拒绝。
- R-TOOL-002：文本schema缩至2000字符；固定桥接代码压缩编码，输入使用UTF-8/base64安全传递。生成的完整命令在进入WorkBackend前检查16000字符组合预算，含配置命令、JSON参数和协议代码；超限明确413，未执行、未伪称供应商失败。参数仍使用审批时原始字典做journal，不增加宿主安装或执行。中文工具描述和schema说明预算，任意大arguments不保证可执行。
- R-TOOL-003：OAuth专用目标校验要求HTTPS，资源、issuer、发现文档、授权/令牌/撤销端点和后续访问均适用；仅显式allow_private且IP字面量为loopback允许HTTP测试，普通内网、公网HTTP不例外。普通未启用OAuth的旧SSE兼容性保留。
- R-TOOL-004：元数据131072字节、token32768字节，均stream分块限长，超限立即关闭；只接受JSON对象，有界校验令牌/refresh/scope/有效期字段。请求identity编码并拒绝压缩响应，避免解压时先分配巨大内存；错误不带原响应或密钥。撤销只看状态、不缓冲正文。
- R-TOOL-005：无凭证且不重定向探测资源401 header；resource_metadata优先，否则按path/root顺序回退。AS依次RFC8414路径插入、OIDC路径插入、OIDC路径追加；每个URL独立受TLS与网络策略约束，resource/issuer和S256仍校验，不把已有token带往新地址。发现可回退404/405；其他远端失败明确终止。

新增`test_tool_review_regressions.py`并扩充原自验：真实本地token响应暂停期间，并发撤销/改客户端→放行响应，验证无grant；真实WorkBackend长度入口、schema最大emoji文本、超组合预算；三个真实资源/OIDC发现路径、恶意header端点；元数据和token分块4MiB响应提前断连；token畸形类型与TLS边界。原两个原生无网络容器改为执行实际压缩桥接命令。仍使用独立DB/data及隔离本机替身，不连接外部授权服务器。复审通过前不交QA。

修复后自验33项通过（23.96秒），日志`.local/team-workspace/platform-expansion/development/tools/review-fixes-selftest-final.log`；限定文件Ruff、Python编译和git diff --check通过。首审报告保持原结论，由独立审查者复审后更新，不由作者自行改为通过。

## BUG-EXP004：协议进程后代清理

QA在真实无网络容器复现直接CLI退出后后台后代遗留。本次修复仅在已批准Work容器内部：桥接启用Linux child-subreaper收养退出CLI的后代；finally按`/proc`父子关系枚举子树，使用pidfd防止PID复用误伤，停止并终止子孙后回收；重复枚举处理清理期间发生的收养，不向宿主发送信号。stdio和ACP共用此生命周期；正常返回、协议错误、45秒协议超时、SIGTERM/SIGINT取消均清理，ACP先尝试session/cancel。不把协议子进程移出原Work进程组，不改变统一执行限制。

新增`test_tool_lifecycle.py`，两个协议各覆盖成功/坏JSON/实际协议超时/显式取消，CLI派生后台子孙且孙进程另开session，最终检查容器内marker进程为零。临时容器使用独立测试标签、无网络、128MiB、64进程限制和--rm，supervisor也有55秒上限。强制SIGKILL无法执行任何Python finally；外层硬取消仍须既有Work命令组终止和任务沙箱销毁兜底，模块SIGTERM自验不冒充整个任务取消链路验收。修复后须独立复审通过，QA再复测原冻结复现。

首轮8项生命周期自验通过97.26秒。最终版完整工具回归为35通过、6个API启动错误（115.30秒，`bug-exp004-final.log`）：最终版8项生命周期均通过；API错误为主入口新增identity_deliveries查询而旧独立测试DB尚无此表，已交主协调核对增量迁移，未放宽测试。Ruff、编译、差异检查通过；本修复仍待独立复审与QA原复现反向验证。

主协调确认072仍在开发阶段，schema未冻结部署生产；上述6项是作者错误复用旧工具测试库引起，并非产品迁移问题。随后使用全新随机配对DB/data复验6项API，6项全通过（18.40秒，`bug-exp004-fresh-api.log`），finally精确删除本次测试DB和目录；没有改产品迁移或原历史结果。结合最终源码35项已通过和新资源6项通过，工具41项自验全部通过，分批记录，未冒充一次41项完整绿色运行。

## R-TOOL-006：管道背压下取消通知不能阻止回收

独立复审发现大sessionId（120000字符）使finally中的同步cancel堵住CLI stdin，导致清理无法到达。统一send改为非阻塞os.write分段写；每帧最多131072字节、默认受协议总45秒deadline约束，所有request/notification/客户端拒绝应答均走同一路径。cleanup cancel使用独立0.25秒期限，不依赖已经到期的协议deadline；任何通知发送异常均继续进入子树终止与回收。未仅通过缩短sessionId绕过原复现。

新增`test_tool_backpressure.py`：原120000字符sessionId完成prompt后CLI不再读stdin，仍须5秒内退出；CLI在session/new后停止读取导致prompt写满，以及恶意客户端请求导致错误应答写满，均使用实际45秒deadline验证；接收帧可合法但应答帧超限时立即拒绝。各场景同时检查detached后代清除和同容器无关sibling继续存活。原非超时生命周期用例也定向回归，原八项无关45秒读取超时不重复耗时。

定向原生自验10项通过、2项原读取超时未重复（99.26秒，`rtool006-native-final.log`）；全新随机配对DB/data原工具回归33项通过（27.58秒，`rtool006-fresh-tools.log`），finally精确清理本批资源。Ruff、编译和diff-check通过。早期新增帧限长用例边界计算超出了接收上限，实际先被输出保护拒绝；修正替身帧长度以明确命中发送上限，未修改产品限制或弱化断言。修复再次冻结等待独立复审，未交QA。
