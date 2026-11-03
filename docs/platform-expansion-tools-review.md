# 工具模块独立代码审查

2026-11-02。作者expansion_tools，独立审查expansion_review_tools。范围为EXP030～032的mcp_service、mcp_oauth、tool_transports、tool_protocol_bridge、capability_api MCP段、capabilities MCP段、073迁移、两处工具前端及test_tool_expansion。未修改业务源码、启停生产或调用真实外部服务。

**当前结论：第四轮增量复审通过，R-TOOL-006已关闭，可交QA复测BUG-EXP004。** 原001～005关闭仍有效；以下保留历轮问题和范围，最新独立执行证据见文末。原生SIGTERM/协议清理通过不等于整个任务SIGKILL/销毁链路已验收。

首轮结论为不通过，不得交QA。作者13项自验不能覆盖下面的并发、安全和边界情况，已先修复后重新冻结提交独立复审。

## R-TOOL-001 / P1：撤销授权被正在交换令牌的回调复活

- 位置：`services/api/app/mcp_oauth.py:164`、`:186`、`:191`、`:272`。
- 回调消费state并检查配置后，在数据库事务外等待token端点，再无条件store_grant。此期间revoke删除grant和flows并返回revoked，随后回调仍能插入新grant；config revision不变，该grant可供下一次工具调用使用。
- 复现顺序：本机token端点暂停响应→callback已经消费state→同账号DELETE grant返回→放行token响应→查询授权和调用。现代码不存在落库前的flow存在/撤销代际复核，也未与revoke串行。
- 修复：使回调最终授权提交、撤销、配置修订按同一账号/连接锁或撤销代际协调，重验flow/来源/配置。撤销已返回后，旧回调不能创建新可用grant。补独立连接并发回归；不要只测先回调后撤销的顺序情况。

## R-TOOL-002 / P2：声明合法的长输入必然超过执行后端硬限制

- 位置：`services/api/app/tool_transports.py:54`、`:70`～`:73`；现有`work_backend.py:131`附近的16000字符上限。
- 输入schema允许text长度16000，模块把整个BRIDGE和JSON内联到shell。离线仅字符串构造实测：BRIDGE4182字节、合法16000字符文本生成21844字符命令；WorkBackend必定拒绝，用户批准也无法执行。arguments接近32768字节上限时更明显。
- 修复：与真实后端限制一致地约束输入，或使用受控且不泄漏未批准参数的独立bridge/payload传输。保留原始审批参数和配置pin，不能为绕过此问题提高所有命令上限。补真实WorkBackend长度边界回归，不能只用不检查命令长度的fake backend。

## R-TOOL-003 / P1：OAuth允许公网HTTP传输客户端和刷新凭证

- 位置：`services/api/app/mcp_oauth.py:29`、`:52`～`:56`、`:184`～`:188`及refresh/revoke端点调用。
- 新OAuth只使用通用MCP validate_target。该函数为兼容普通HTTP工具而接受公网http，并不要求TLS；因此发现、授权、token及撤销链路可以发送client_secret/refresh token/Bearer相关凭证到明文HTTP。`allow_private=false`也不拦公网HTTP。
- 修复：增加OAuth专属通信约束，授权服务器端点强制HTTPS；本机替身仅在明确允许且确为loopback时例外，不能扩大成任意内网/公网HTTP例外。保留普通旧SSE的既有兼容性，不给所有validate_target调用强加不相干行为。补公网HTTP拒绝、HTTPS接受和本机显式例外测试。
- 依据：[MCP授权规范通信安全](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)，要求授权服务器端点使用HTTPS。

## R-TOOL-004 / P2：元数据“限长”发生在完整缓冲之后

- 位置：`services/api/app/mcp_oauth.py:31`～`:35`、`:188`～`:190`及refresh token响应。
- `await client.get()`已经把整个body读入内存，之后检查131072字节无法保护API免受超大响应；token响应同样直接全量buffer再json解析。配置的外部服务错误/被入侵时，可在单次授权请求中消耗大量API内存，15秒超时不是体积限制。
- 修复：stream模式有界读取，超限即关闭响应并返回安全错误；元数据和token有独立小上限，JSON结构/字段类型也需验证，不回显原响应或凭证。补本机分块大响应测试，断言提前终止而不是接收完整body后拒绝。

## R-TOOL-005 / P2：标准发现只实现单个成功路径

- 位置：`services/api/app/mcp_oauth.py:41`～`:49`。
- 当前只请求path-specific资源well-known和一个RFC8414发现地址。合法MCP服务仅在根路径提供受保护资源元数据，或通过401的WWW-Authenticate提供resource_metadata时，当前返回404而不是标准回退；AS仅提供OIDC发现时同样失败。现fixture只覆盖唯一成功路径，不能证明标准互通。
- 修复：按授权规范实现受网络策略约束的资源metadata发现与回退，以及AS的OAuth/OIDC well-known候选顺序。禁止盲目重定向或携已有token去未知来源。补根资源、header指定metadata、path issuer的OIDC回退及恶意元数据地址拒绝测试。
- 依据：[MCP资源和授权服务器发现要求](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)。预注册client的有限范围不等于可以略去标准元数据发现。

## 已核验边界与交接

- 所用官方SDK sse_client源码确实校验消息端点origin并限制重定向，不能把同源检查误报为完全缺失。新工厂trust_env=false符合本机测试和既有网络约束。
- stdio/ACP通过现WorkBackend执行，未发现模块直接在宿主spawn配置命令；参数shell引用、固定Python/Node可执行路径、配置pin及动态绑定检查存在。它们只在Work中提供，Ask不暴露；文档/UI应持续明确此范围。
- bridge真实解析JSON-RPC，ACP拒绝未广告的文件/终端请求，保留输出/时间限制及进程finally清理。此次没有重跑实际容器取消/超时演练，不将静态核验当作原生行为全部通过。
- 主协调尚未注册router和新密文表的vault保护，属于已约定集成事项，不认定作者未注册即Bug。最终集成还须复核tool-oauth权限映射、回调来源/会话和主密钥缺失时不得生成新密钥。
- 本轮执行的是有限文件静态/调用链审查、已装SDK源码核对和无网络字符串边界复现；未读取用户正文、密钥、生产数据库或运行外部OAuth。本报告不保证不存在其他Bug，后续独立复审通过后才进入QA。

## 2026-11-02 修复后独立复审

复审限定为mcp_oauth.py、tool_transports.py、两份工具测试及开发文档，必要回看原MCP/WorkBackend调用边界和工具配置UI。未修改业务源码、未启停生产、未调用真实外部模型或授权服务。所有以下问题关闭，未发现本次修复引入的新阻塞问题。

- **R-TOOL-001 已关闭。** callback在网络交换后取得与revoke/refresh相同的账号/连接事务锁，锁定当前server/client并重新校验配置pin、client修订、尚存在且未过期的已消费flow，随后在同事务store/delete flow。revoke同锁原子删除grant/flow；configure更新配置时也删除flow。独立重跑的两个实际API并发场景均在token响应暂停期间撤销或改配置，放行后callback被拒、grant仍为空；state重复提交保持拒绝。
- **R-TOOL-002 已关闭。** text的schema、运行时检查和中文工具说明统一为2000字符；Web工具配置页仅配置命令和传输，没有另一个ACP text上限输入。固定bridge压缩、UTF-8/base64传递，完整生成命令在WorkBackend前执行16000字符组合预算校验，超限413且不执行。独立重跑真实WorkBackend边界、2000 emoji、超预算arguments及两个容器里的实际编码命令均通过；未提高WorkBackend统一上限，审批仍使用原payload。运行时通用无效输入提示与schema共同表达限制，组合预算另有中文错误说明。
- **R-TOOL-003 已关闭。** OAuth专用校验覆盖资源、issuer、发现URL、授权/token/revoke端点及后续调用；HTTP仅在allow_private且IP字面量确为loopback时允许。普通内网和公网HTTP均拒绝。实际创建的OAuth客户端全部禁用重定向。除独立重跑TLS参数测试外，本审查者另用内存HTTP替身验证HTTPS token返回302至元数据HTTP地址时只请求原地址即拒绝、授权元数据中的HTTP token端点在请求前拒绝，未发送外部请求。
- **R-TOOL-004 已关闭。** bounded_json以stream在读取中限制元数据128KiB、token32KiB，拒绝非identity压缩响应和非JSON对象；令牌、scope及有效期字段有类型和长度验证。独立重跑两个本机4MiB分块响应均提前关闭连接，畸形JSON字段、压缩正文未读即拒绝等回归通过。revoke仅读取状态，不缓冲正文。错误未带原始响应或凭证。
- **R-TOOL-005 已关闭。** 无凭证、不跟随重定向地探测401 resource_metadata；无header时按path/root回退；授权服务器按RFC8414、OIDC插入、OIDC追加顺序发现，逐URL执行TLS/网络检查，继续验证resource、issuer和S256。独立重跑本机header/root/path与三种OIDC位置、恶意metadata端点拒绝均通过。

### 独立执行证据与QA边界

本审查者新建随机`owner_agent_review_tools_<8hex>_test`数据库和独立`services/api/.local/test-review-tools-<8hex>`目录，设置PYTHONNOUSERSITE=1及RUN_LOCAL_SANDBOX=1，直接执行`test_tool_review_regressions.py`与`test_tool_expansion.py`：**31 passed in 15.69s，无跳过**。两个原生stdio/ACP容器使用本地已有镜像、无网络、128MiB/0.25CPU；其余OAuth/SSE均为本机替身。测试完成后按精确名称删除本次创建的数据库，未复用作者测试库。另加本审查者临时内存探针**2项通过**，覆盖302拒绝和元数据HTTP降级拒绝。这些为独立执行结果，不引用作者33项自验代替本次验证。

默认受限环境中的两次Python检查无输出，已中止，未作为通过或业务失败证据；上述有输出的验证在获准的本机测试环境完成。

本次复审未重复演练所有进程取消/超时/后代清理场景，也未运行实际页面操作、真实远端OAuth/MCP/Agent CLI或生产部署。QA仍须覆盖会话过期/权限映射、stdio/ACP取消和UNKNOWN不重放、原有HTTP/SSE回归、中文配置操作及生产集成后的主密钥保护。主协调的router注册、vault新密文表保护和全系统门禁仍为独立集成事项，不能由本报告代替。HTTP客户端的15秒读超时属于分段网络超时，不把它声称为整个发现链的总耗时保证。

## BUG-EXP004增量独立复审：R-TOOL-006 / P1

2026-11-02，复审tool_protocol_bridge.py新增Linux subreaper、pidfd和信号清理及test_tool_lifecycle.py。主体递归清理校验父子关系，不因setsid脱离原Work进程组；不枚举/信号宿主进程。作者8项正常生命周期自验和分批41项结果为作者证据，本审查未将其复制为独立通过。

**006问题：ACP取消通知阻塞导致子树清理永不到达。** send使用同步process.stdin.write/flush，没有非阻塞写或期限。finally先发送session/cancel，再terminate_children；CLI若不再读stdin，超过pipe容量的cancel会堵住，finally此时还忽略SIGTERM/SIGINT。协议正常request send也不受45秒读循环deadline约束。

独立无网络128MiB原生容器复现：session/new返回120000字符sessionId，小于当前131072字节消息缓冲限制；CLI正常读prompt并返回end_turn，随后停止读stdin且已有另开session的后台子孙。bridge在finally写cancel时**5秒仍不退出**；探针仅在容器内强止bridge后，检查得到**detached_descendants=1**，证明后代终止尚未执行。60000字符对照bridge正常exit0，符合pipe容量边界。探针容器--rm结束后所有进程销毁，未信号宿主或生产进程。

原生探针已冻结：`.local/team-workspace/platform-expansion/review/tools/rtool006_probe.py`。从services/api以项目venv Python运行并将PYTHONPATH设为当前services/api，即可复现；脚本只创建本地已有镜像的独立无网络容器，--pull=never，不调用外部Agent或模型。实际执行输出为`{"bridge_hung": true, "detached_descendants": 1}`。

建议所有协议发送按剩余deadline使用有界/非阻塞写，明确最大消息字节；finally取消通知使用独立短期限，失败也无条件继续子树终止与回收。sessionId长度校验可补充，但不能只缩短该字段来代替对堵塞pipe的通用处理。加满管道/停止读取、正常响应后取消、错误/超时以及同容器无关哨兵不误杀的原生回归。

SIGTERM可运行finally与SIGKILL完全不同；外部强制终止只能依靠任务沙箱销毁兜底，不能以SIGTERM测试宣称SIGKILL链路通过。本轮未执行生产/真实任务SIGKILL，外层Work取消、UNKNOWN不重放及销毁兜底仍须集成QA验证。006修复后先独立复审，通过才恢复QA。

## 第四轮：R-TOOL-006关闭，可交QA原问题复测

2026-11-02，仅独立审查最终冻结bridge及新增test_tool_backpressure.py，必要回看protocol_command/WorkBackend长度限制。所有send共享非阻塞stdin、select和4096字节os.write，逐段校验总45秒截止，JSON帧含换行最多131072字节；原始120000字符sessionId保留。finally取消通知另设0.25秒期限，任何发送异常后继续terminate/reap，未用缩短sessionId掩盖原复现。子树清理仍依赖实际父子关系/pidfd，不向同容器任意PID或宿主发信号，保留原Work进程组。

本审查者独立执行结果：

- 冻结原rtool006_probe.py反向重放：**bridge_hung=false、exit=0**，原大cancel不再阻塞清理。
- 新背压4场景及原生命周期success/failure/cancel两协议6场景：**10 passed in99.26s**。包含实际45秒prompt写、错误应答写截止、超帧大小拒绝、同容器无关sibling保持存活、detached后代零残留。使用已有本地镜像、无网络/128MiB临时容器，未信号宿主或生产。原2项45秒读取超时未在本轮重跑，不把本轮10项写成原全部12项。
- test_tool_review_regressions.py：**19 passed in5.89s**，含新版bridge压缩后2000 emoji/真实WorkBackend 16000字符边界及超预算拒绝、既有OAuth网络安全回归；该组本机HTTP替身无真实外部请求。
- 本审查者另构造2000中文字符实际ACP编码往返：**通过**，完整命令12321字符，回传文本逐字一致。增加20000字符arguments时在protocol_command阶段明确413，未派发、未执行；没有为新bridge扩大统一Work上限。

未发现本次修复新增阻塞问题，006关闭。作者分批33项工具API/协议自验仍为作者证据，本轮未再次创建数据库重跑不受bridge修复影响的旧API集成；此前本报告的独立数据库证据保留。可恢复QA对BUG-EXP004的冻结原复现与完整Work任务链测试。

通过边界：这里验证的是正常结束、协议错误、期限和SIGTERM可执行finally路径。SIGKILL无法执行Python finally，仍必须实际验证任务沙箱销毁、进程全清、UNKNOWN不重发及同任务后续命令不会继承残留；强制销毁兜底不能由本报告提前盖章。真实外部Agent CLI、宿主服务和生产资源均未操作。
