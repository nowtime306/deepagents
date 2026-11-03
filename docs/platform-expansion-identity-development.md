# 身份模块实施

2026-11-02，EXP020/021/022，独占072迁移、identity_*后端和features/identity组件。原users/密码列不改，凭证加密沿用model.key。MFA先完成可运行闭环，OIDC标准发现授权码/PKCE/nonce及显式绑定、联系方式恢复随后集成；每项分别实测，不以设计完成冒充实现。

API注册identity_api.router；本地密码正确后，在现login事务内插入会话前调用login_challenge(connection,request,user)，返回非空JSONResponse直接返回。挑战仅有5分钟临时token，无会话；成功验证一次消费挑战再发12小时cookie。TOTP SHA1/30秒/6位，允许前后一个步长并原子拒绝重放；恢复码只显示一次、仅存哈希。

OIDC参考[OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html)，TOTP参考[RFC6238](https://datatracker.ietf.org/doc/html/rfc6238)。仅既有cryptography/httpx，无新依赖。外部服务不联调，隔离本机签名IdP和接收器验证。

## 实现与注册交接

- `identity_api.router`：个人安全状态、TOTP绑定/确认/关闭、MFA挑战、OIDC重定向后的pending挑战读取。密码正确只获得短期challenge，没有完整会话；确认挑战时复核账号、密码版本及企业来源修订。8个恢复码只显示一次、哈希保存并原子消费；同TOTP步长不能重复。新绑定撤销其他会话，关闭验证器需密码或近期企业认证再加动态码/恢复码。
- `identity_oidc.router`：管理员来源新增编辑/停启、公开来源列表、POST begin、GET callback、本人显式绑定及解绑。标准发现、授权码、PKCE S256、单次state、浏览器Lax cookie绑定、nonce、RS256真实验签、issuer/audience/azp/iat/exp/nbf验证。企业来源配置变更撤销其登录会话；MFA挑战也绑定来源修订，停用后不能完成旧挑战。相同邮箱不合并；自动注册仅普通账号。绑定先重新认证，已有MFA时还需第二因素。
- 当前有限兼容：标准OIDC发现同源HTTPS端点、RS256、client_secret_post或public PKCE；仅loopback本机IdP可HTTP。没有宣称任意OAuth网站、跨源供应商方言或所有JWT算法已支持。默认来源关闭、自动注册关闭，本地管理员入口保留。
- `identity_recovery.router`：管理员SMTP/签名短信Webhook渠道、本人联系方式验证、请求找回及更新密码。新联系方式先放pending表，未验证不覆盖旧已验证联系方式；请求统一返回、不回显账号是否存在。令牌只有哈希、10分钟有效、单次；发送/IP/账号/令牌限流。MFA账号重设密码仍需MFA，不静默关闭验证器；成功撤销全部旧会话及挑战。SMTP需STARTTLS，只有本机测试接收器允许明文；短信HMAC覆盖timestamp+body。OIDC绑定/登录及恢复操作追加脱敏审计。
- 新企业账号没有可用的已知本地密码；最近5分钟内且来源仍启用的OIDC会话可作为个人安全操作的再次认证，普通本地会话不能用空密码绕过。个人页面说明此边界；超时须重新企业登录。不是仅凭邮箱的恢复入口。
- 所有密文均复用现vault；管理员编辑来源secret留空保留，明确clear_secret才清空；渠道configuration省略保留原配置，更新时空password/secret保留原凭证。

前端 `features/identity/identity-security.tsx` 导出 `IdentitySecurity`（验证器/恢复码/联系方式/绑定）、`IdentityAdmin`（来源列表/新增编辑弹窗/停启、找回渠道）、`IdentityLogin`（企业登录、MFA及自助找回）。root负责集成，未在此模块直接修改main.py或现导航。IdentityLogin传入本地登录202返回的challenge和onAuthenticated回调；OIDC跳回登录页时自身读取pending cookie。既有独立媒体导航不改。

root注册三个router，并在本地密码正确后的事务中、account检查后/创建会话前调用login_challenge。API启动密钥保护检查应纳入全部identity_*密文字段，避免只有身份密文时model.key丢失又生成新密钥。身份模块不自行启停生产服务或应用迁移。

## 开发自验及审查门槛

使用每轮随机 `owner_agent_identity_<id>_test` 数据库与独立 `TEST_DATA_DIR`，仅该库在finally删除；不与其他角色共享Qdrant文件目录。不访问用户生产账号，不调用外部模型或身份/消息服务。20项自验包含6个RFC6238向量/重放、有效RSA签名及8种无效claims、真实PostgreSQL的MFA/恢复码/权限、实际本机HTTP IdP的PKCE/nonce/单次state/非管理员新身份/停用撤销、签名短信接收器与旧联系方式保留、一次性密码找回/会话撤销、实际SMTP收信。

第一次独立SMTP单项默认受限环境WinError10013，不记产品失败；按本机网络授权重跑通过。最终完整20项在允许本机网络的执行中通过，日志 `.local/team-workspace/platform-expansion/development/identity/selftest.log`。后端Ruff通过；身份组件TypeScript、ESLint零错误零警告。未把协议替身通过冒充真实企业IdP、邮件/短信送达效果通过。

状态：开发自验完成，等待独立代码审查。**审查→按意见修复→复审通过后**才交独立QA和开放入口；尚未部署、未宣称实际页面完整回归通过。测试可在审查放行后按EXP020/021/022用例逐项检查，生产停启由主协调统一安排。

## 首轮独立审查修复（2026-11-02）

首审报告 `platform-expansion-identity-review.md` 提出5项，首审不通过的证据保留；本节是作者修复记录，不能替代复审结论。

1. R-ID-001：来源创建后issuer不可更改，编辑不一致返回409；中文编辑框禁用issuer。更换来源应新建provider并显式重新绑定。真实本机签名IdP回归验证同一subject在不同issuer来源登录不会落到旧管理员账号。
2. R-ID-002：绑定state固定发起session hash及密码版本，发起时再次验证未撤销会话，回调事务锁定会话及用户并复核有效期/密码版本；撤销会话或改密后旧意图不能绑定。回调通过原Lax浏览器state校验，复核服务端固定session仍有效，不依赖跨站重定向时不会发送的Strict认证cookie；合法外部跳回仍可完成。
3. R-ID-003：找回令牌固定发起时已验证地址hash，重置时复核仍是当前已验证地址且账号可用；确认新联系方式事务内删除旧reset令牌及级联投递。请求、确认、重置使用相同710018事务锁；新增顺序与并发回归。
4. R-ID-004：渠道更新解密原配置并合并非空凭证；空password/secret保留，接口新增明确 `clear_credential`，清除后缺少必要凭证的渠道不能启用。中文管理页提供“停用并清除该渠道凭证”；读取/错误不回显秘密。两种渠道均测试保留、替换、启用时拒绝清除、停用后清除、重新启用校验。
5. R-ID-005：找回请求只做数据库提交，不等待SMTP/HTTP；真实已验证账号产生持久加密outbox，未知账号不投递。`identity_delivery`每进程一个worker，逐条处理、行锁SKIP LOCKED；未发送pending可在重启后投递，仅明确建连前失败最多重试3次。外发中取消、HTTP失败/不确定网络结果及30秒租约过期的旧sending记录均转unknown，绝不自动重发；过期令牌级联删除队列，成功/失败/未知后清除投递密文。新申请会替换旧令牌及其队列，只有用户主动重申才创建新的发送。投递失败不改变统一找回响应，不宣称真实送达；用户可重试或联系管理员。状态pending/sending/sent/failed/unknown及脱敏error_code供核查，日志不记录接收地址、令牌或服务商错误正文。

072尚未生产应用，直接在072补充state版本字段、恢复地址hash和投递表；必须用全新测试库初始化，不能复用登记过旧072的测试库。root集成新增生命周期钩子：迁移/密钥检查之后 `start_delivery(app)`，关闭数据库池之前 `await stop_delivery(app)`；vault保护追加identity_deliveries.payload_ciphertext非空密文检测。未新增依赖或独立部署服务。

首轮修复自验27通过、1失败：并发用例保留了自身待发队列，后续用例改变测试短信接收端后收到前一用例消息，导致消息计数断言失败；原日志 `review-selftest-first.log` 保留。已只清该用例自己的令牌/队列，完整新库重跑28项通过28.44秒；随后补充Strict cookie跨站合法回调回归，最终完整29项通过25.81秒，日志 `review-selftest-final.log`。渠道类型校验还覆盖错误数字凭证，避免无效配置进入投递worker。Ruff及身份组件TypeScript/ESLint通过。修复仍需原独立审查者复审，未交QA或部署。

### 运维集成边界补充

root新增后台准入要求后，外发完整生命周期通过 `maintenance.request_slot` 持有共享GATE，在领取前、外发前检查API fence，维护模式或旧API实例不能领取发送。SMTP同步线程在取消时等待当前有界I/O收尾才释放GATE；取消/不确定发送持久标记unknown，之后不自动重放。维护busy检查精确为 `identity_deliveries.status='sending'`，unknown可监测核查但不阻塞备份。Fixture启动真实API生命周期后先停止自启身份worker，由用例显式驱动，防止与手动测试争抢。

加入维护拒绝、API fence拒绝、外发持共享门闸、取消后unknown、遗留sending转unknown不重发、HTTP错误不重发及明确建连前失败3次上限验证；全新隔离库与独立目录完整 **30项通过35.19秒**，`review-gate-selftest-first.log`。实际本机慢接收器验证找回提交不等待发送，未给不存在账号发消息。复审前继续冻结；这份开发自验不是独立QA验收。

## 第二轮复审修复

原R-ID-001～005由独立审查关闭，新增006/007仍不通过，报告原样保留。

- R-ID-006：OIDC GET callback整个生命周期持共享 `request_slot`，消费state前检查维护开关与API fence，外部IdP交换完成后、写账号/绑定/会话之前再次检查API fence。维护期间返回503且不消费有效绑定意图；解除后同一合法回调可完成。补充外发期间旧API实例失效后的写入拒绝回归。
- R-ID-007：GET pending核对挑战尚存在且未过期、账号有效、MFA仍启用、密码版本及企业来源修订仍匹配；失效返回无挑战并清除cookie，不依据cookie是否存在直接决定。新POST pending/cancel删除本地传入或cookie中的挑战并清cookie。中文登录组件增加“返回登录，重新开始”，清除当前验证码及挑战；`onRestart`回调由父级清本地loginChallenge并恢复登录表单，异步pending读取不能在取消后复活旧状态。此为页内恢复，未声称cookie永远残留。

完整新库/独立目录 **32项通过53.35秒**（`review-round3-selftest.log`），Ruff、TypeScript、身份ESLint、diff检查通过。外发后fence检查新增后，维护/外发失效/MFA重新开始3项边界用例通过13.04秒（`review-round3-boundary.log`），用例总数33。已只读核对父级接入onRestart清challenge，重新显示登录表单。可复用自验脚本 `.local/team-workspace/platform-expansion/development/identity/run-selftest.py`，从services/api使用虚拟环境Python执行；可传pytest路径，自动随机库与目录并只删除本次自建库。仍需第三轮独立复审，未交QA或部署。
