# 身份模块独立代码审查

2026-11-02，审查人 `expansion_review_identity`，作者 `expansion_identity`。依据产品EXP-020～022审查072迁移、identity_api/identity_oidc/identity_recovery、中文身份组件和自验用例；未修改产品代码、启停生产或访问真实外部服务。按文件和调用链审查，未把仓库全部未跟踪文件当本模块差异。

## 首轮结论

**不通过，暂不能交QA或开放身份入口。** 作者的20项自验说明已覆盖基本流程，但下列账户绑定和恢复边界尚未覆盖。以下行号为首轮读取时的位置，修复后以问题编号追踪。

### R-ID-001 / P1：更换issuer后沿用旧身份绑定

- 位置：`services/api/app/identity_oidc.py:134`（save_provider），同文件callback约297行；`072_identity.sql`的identity_bindings主键。
- 触发：来源A已经有管理员等账号的subject绑定；管理员编辑同一provider id，把issuer改为另一个租户/IdP B。B对不同用户签发恰好相同的subject。
- 原因：编辑直接改issuer且只撤销会话，bindings仍以provider_id+subject查询，没有固定原issuer；有效B凭据因此落到A的旧owner。
- 影响：跨身份提供商错误合并，可登录原有高权限账号；违反issuer+subject身份隔离。
- 要求：issuer不可变，或显式迁移/重新绑定；不要仅清会话。补不同issuer相同subject不能沿用旧owner的实际回归。

### R-ID-002 / P1：显式绑定意图在发起会话被撤销后仍有效

- 位置：`identity_oidc.py:198` state写入、约297行绑定分支；`identity_recovery.py:268`密码重置及现有会话撤销调用链。
- 触发：用户重新认证后开始企业身份绑定；在回调前找回/修改密码、撤销全部会话；仍携带5分钟browser state cookie完成回调。
- 原因：绑定state仅存owner_id/browser_hash，不固定发起会话及密码版本。回调只检查账号当前有效，不检查授权绑定的会话是否仍有效。
- 影响：被撤销的绑定意图可继续添加持久登录入口，削弱改密/撤销会话的恢复边界。
- 要求：固定发起session和密码/身份版本并在回调事务中复核；改密、找回、撤销或重置安全凭据后旧绑定意图不能完成。补撤销前后及合法回调回归。

### R-ID-003 / P1：替换已验证联系方式后，旧地址重置令牌仍可使用

- 位置：`identity_recovery.py:192` verify_contact、223行联系方式更新；234行request_reset、268行reset。
- 触发：已验证地址A，开始验证新地址B（A按设计继续有效）；在B确认前请求一次找回，令牌发到A；随后确认B，再使用A刚收到的reset令牌。
- 原因：确认B只消费verify令牌并改contacts，不撤销该期间的reset令牌；reset令牌没有地址hash/版本，重置时也不复核当前已验证地址。request_reset在联系方式锁之外还存在并发窗口。
- 影响：原地址已失去恢复资格后仍能修改密码。
- 要求：令牌固定发起时验证地址/版本，reset复核；新地址确认时原子撤销旧reset，发起/验证/重置使用一致并发边界。补上述顺序及并发回归。

### R-ID-004 / P2：渠道编辑空凭证未按约定保留

- 位置：`identity_recovery.py:120` save_channel，约134行直接加密新config；IdentityAdmin的保存渠道按钮。
- 触发：已配置SMTP密码或短信secret，编辑主机等非密钥字段，密码输入留空后保存。
- 原因：提供configuration时直接整份覆盖。SMTP空password覆盖原密码，短信空secret在验证阶段直接失败；没有合并旧密文配置或明确清除意图。
- 影响：常规编辑可能使找回通道失效，与产品“编辑空secret保留”的要求和开发说明矛盾。
- 要求：解密原配置后按明确规则合并，空secret保留；需要清除时独立显式意图且不能启用缺少必需凭据的通道。读取/错误回包不得泄漏旧秘密。补两种通道的留空/替换/清除回归。

### R-ID-005 / P2：统一JSON回包仍存在找回账号时序枚举

- 位置：`identity_recovery.py:234` request_reset，246行存在分支、255行同步await send。
- 触发：在同一可用渠道分别请求存在且已验证的账号和不存在账号；可用接收端或SMTP故意延迟响应。
- 原因：前者等待外部SMTP/HTTP完成（超时可10秒），后者直接返回。虽然响应JSON相同，发送耗时只存在于真实账号分支。
- 影响：可利用明显时序差异判断账号/已验证联系方式状态，不满足反枚举边界。
- 要求：使用一致可观测的提交处理或有界受控异步发送，真实接收对象仍只限已验证本人；不能给不存在账号发送消息来伪装一致。补可控慢接收端与不存在账号的时序/响应测试，以及投递失败与重启的真实状态说明。

## 其他核对与待集成

已看到正确采用RS256验签、issuer/audience/nonce、单次state与浏览器cookie、PKCE、MFA恢复码哈希及行锁消费、TOTP步长重放保护、密码版本绑定的登录挑战、本地管理员保留、没有按邮箱自动合并账号。这些正向观察不能替代上述缺陷修复。

主协调尚未集成本地login hook及导航。正式集成时必须保证MFA返回202不发完整会话、前端不把challenge误当AccountIdentity；OIDC pending cookie闭环；vault缺失保护包括全部identity密文字段。主入口集成后还需独立审查对应差异，再交测试。

本轮首审没有重新运行作者20项作为独立QA，也没有宣称真实企业IdP、SMTP或短信送达效果通过。问题已发主协调和作者，等待修复说明、回归证据及复审。

## 第二轮独立复审（2026-11-02）

**结论：仍不通过，尚不能交QA。R-ID-001～005本轮关闭；新增R-ID-006、R-ID-007待修复。** 原首轮证据保留，上方“尚未集成”的说明是首轮历史状态。本轮已检查root新增的本地登录MFA202、全部身份router、个人安全/管理员入口、vault密文探测和投递worker生命周期接线。

### 原问题关闭依据

- R-ID-001：`identity_oidc.py:142`拒绝已有provider修改issuer；不同来源相同subject在本机签名IdP回归中得到不同owner，原管理员绑定不被复用。
- R-ID-002：072为绑定state增加发起session hash及密码版本；begin和callback分别事务复核，回调锁定会话/用户并检查版本。原会话撤销、改密均拒绝旧意图；合法Lax浏览器state在不携带Strict认证cookie时仍成功。没有用无法跨站发送的Strict cookie阻断合法回跳。
- R-ID-003：重置token固定已验证address_hash；请求、联系方式确认、重置使用710018锁；确认新地址撤销旧reset及其投递。重置再核对当前验证地址/账号状态。顺序及并发真实数据库用例通过。
- R-ID-004：合并原解密配置，空password/secret保留；显式clear_credential且缺凭证不得启用。SMTP/签名短信均验证保留、替换、拒绝非法数字、停用清除及重新启用校验，不回传凭证。
- R-ID-005：请求只提交加密持久队列，不再等待外部发送。已验证和不存在账号在本机慢接收器测试中均快速返回相同JSON；不存在账号无消息。投递错误没有伪装成成功送达。

### 新增worker及集成审查

`identity_delivery.py`领取和外发全程共享GATE、领取前/外发前fence；明确建连前失败最多3次；外发取消、HTTP结果不确定及过期sending转unknown且不自动重发，结果终态清除payload密文。SMTP取消等待有界同步发送收尾后释放准入。072状态包含小写sending及unknown/error_code，token过期或撤销级联删除队列。本机用例已核对维护拒绝、fence拒绝、外发期间排他锁无法取得、取消/残留不重放。

main本地login在账号有效/密码版本锁定后调用challenge，202不创建完整session；独立新增用例实际走main login而非直接调用辅助方法，确认验证器恢复码成功前auth/me为401，验证后才可登录。三router接线、worker在vault保护之后启动/池关闭前停止、全部身份密文字段保护和页面入口已静态核对。该观察不能覆盖以下callback遗漏。

### R-ID-006 / P1：OIDC GET回调绕过维护准入，备份期间仍能外发并写入

- 位置：`services/api/app/identity_oidc.py:260` callback；`services/api/app/main.py:289`维护准入仅覆盖POST/PUT/PATCH/DELETE。
- 触发：正常发起显式绑定获得尚有效的state/browser cookie；随后平台进入maintenance；IdP以GET回跳callback。
- 独立复现：在全新隔离数据库，先由本机真实签名IdP准备合法绑定；设置`platform_maintenance.active=true`，再回跳callback。接口实际303且`identity_bindings`新增当前owner绑定。该过程包含删除state、token/JWKS外发、写绑定/审计；并未持GATE或检查maintenance。
- 影响：维护入口已关闭写请求时，GET身份回跳仍能跨越一致性边界，备份/恢复期间出现外发和新增身份/会话状态；无法依靠写方法中间件保证维护独占。
- 修复要求：callback从消费state前开始纳入完整共享GATE/维护和fence边界，直到外发与事务结束；维护拒绝应不消费原state。覆盖正常回跳、维护503且无state消费/外发/写入、外发期间排他GATE不可取得、fence失效。

### R-ID-007 / P2：MFA挑战失效后页面没有重新开始登录的路径

- 位置：`apps/web/src/features/identity/identity-security.tsx:335`、361（challenge始终优先，隐藏企业入口），`apps/web/src/features/ask/platform-app.tsx:1113`、1121（本地challenge隐藏登录表单）；`services/api/app/identity_api.py:143` pending只判断cookie非空。
- 触发：本地密码验证得到202，或企业登录跳回MFA页面后，用户让页面保持打开超过5分钟。React中的challenge仍存在；后端401后只显示错误，不能返回账号登录或重新发起企业登录。错误账号或失效的挑战也没有取消入口。
- 独立证据：向pending提供不存在/失效challenge cookie，仍返回`mfa_required=true`；challenge接口明确401后，再读pending仍为true。中文组件没有清除父/子challenge的按钮或失效处理。
- 边界说明：浏览器通常会按max_age=300清除OIDC cookie，因此不能称为永久cookie锁死；但已挂载页面的React状态不会随cookie消失而清除，只能靠整页刷新脱离。可用性缺陷是登录页内无法重新开始，而非MFA可被绕过。
- 修复要求：提供明确“返回登录/重新开始”同时清除父/子challenge及服务端pending cookie；pending只返回仍有效挑战并清无效cookie。过期错误能回到可操作登录入口，不把真实MFA错误直接当已登录。测试本地202和企业pending两条路径，保留错误动态码可再次输入的正常行为。

### 本轮独立验证与边界

独立运行新建随机`owner_agent_identity_review_*_test`，独立TEST_DATA_DIR；finally只删除本轮随机库。首次受限网络Win10013没有记成产品失败，获准本机网络后执行完成：**33项通过，34.75秒**。日志：`.local/team-workspace/platform-expansion/review/identity/independent-review.log`；复现用例：同目录`test_independent_identity_review.py`；隔离运行脚本：同目录`run_independent_review.py`。

其中30项是作者协议/数据库回归，本审查独立执行；新增1项验证真实main MFA202不发会话。另2项是**断言当前错误行为可重现的审查探针**（维护callback303及无效pending为真），它们的绿灯不是产品验收通过，必须修复并反转为期望行为后复审。

没有执行真实外部IdP/邮件/短信送达、生产迁移或启停服务，没有输出用户密钥/正文，也未宣称中文浏览器完整验收或零bug。作者修复R-ID-006/007并冻结后，需要再次独立复审；通过前仍禁止交QA。

## 第三轮独立复审（2026-11-02）

**身份模块范围通过，可交独立QA；不代表整站或真实外部接入验收通过。** R-ID-006、R-ID-007本轮关闭，原001～005保持关闭。

- 006：GET callback在消费state前获得共享GATE并检查维护，准入前/后及token/JWKS交换后检查fence，直到事务结束才释放。维护503保留state，解除维护后原合法回跳成功；交换后fence失效不写绑定。
- 007：pending复核挑战有效期、密码版本、账号/MFA及来源修订；无效清cookie。cancel显式删除传入与cookie挑战；中文“返回登录，重新开始”清子状态，并通过root接线的onRestart清本地202父challenge；不会把错误动态码当已认证。
- 独立全新随机库和TEST_DATA_DIR运行 **36项通过，57.90秒**，无跳过，日志仍为review/identity/independent-review.log（本轮覆盖）。30基础用例已增至33项，另外3个独立探针分别验证维护callback拒绝、无效pending不再需要MFA和真实main密码登录202不发会话。本轮探针已经改为断言正确行为，前轮复现事实以上方记录为准。
- 复审未改业务源码、未部署/操作生产、未调用真实IdP/SMTP/SMS；UI组件与父回调接线为独立静态核对，实际浏览器操作和用户接入仍交QA。不得把代码审查通过写成零bug或全产品完成。
