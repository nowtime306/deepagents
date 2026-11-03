# 本人习惯元 Skill 独立验收用例

2026-11-03。依据[冻结产品需求 HSK-01～06](habit-skill-plan.md)与平台资产 `assets/skills/habit-skill-builder/SKILL.md`。设计与初版门槛保留；最终独立验收见[QA报告](habit-skill-qa.md)。开发自验→独立审查→最终冻结后才正式测试。此功能属于知行平台，不安装到个人 Codex。

## 环境与证据

独立随机 `owner_agent_habit_skill_qa_<随机>_test` 与唯一数据/密钥/文件目录、两个假账号、多个假Agent/项目，所有聊天人为合成。不读取生产会话、不复制生产模型或主密钥。只复用现有test_api.client、FakeMessagesListChatModel/ToolModel与真实SkillsMiddleware，或隔离生产构建站；端口启动前确认空闲，原3100/8100不访问。无真实外部模型、消息发送或业务写入。

每个证据注明单元/隔离API/实际模型工具循环/组件/真实浏览器。不能将数据库存在子Skill或模型自称“已经学会”当运行加载证明。记录冻结SHA、唯一日志和假数据截图；源码变化先复审。删除只按本轮manifest的数据库、PID、namespace及假凭据，保留证据。

## HSK-01 显式启用与安装

- H01-01：新账号GET设置默认disabled，门槛3、受支持kind/value、候选/生成计数展示正确；普通问答不注册record工具、不写习惯。
- H01-02：开启+安装元Skill为本人合法Skill并合并通用助手绑定；原知识库/MCP/Skills/Memory开关均不丢失。重复安装/重复开启幂等，无重复元包。
- H01-03：手工已上传同名habit-skill-builder时，安装明确冲突而不覆盖正文；绑定达到原资源上限时明确失败，不删除已有绑定。
- H01-04：只有skills.read无manage的登录账号不能启用、安装、采集或生成；未登录401、无Origin拒绝与角色403分别验证，不相互冒充。
- H01-05：开启但未给自定义Agent绑定父元Skill，不提供元库工具；按既有发布流程绑定后新版本才可用。原Agent版本/skill_pins不被后台改写。

## HSK-02 真实当前消息与去重

- H02-01：相同kind/value在当前3个不同原始user message中提出：1/2只候选，第三条才生成；检查实际source IDs与证据数量。
- H02-02：同一message多次record、重试/刷新/恢复、并发重复调用只计1条；第三条并发提交只生成一个child，唯一约束与事务结果一致。
- H02-03：模型不能提交owner/message_id/count/任意正文，额外参数拒绝；实际来源由可信当前run解析。假造历史来源、别的run/用户/会话不得被采集。
- H02-04：role=assistant/tool、附件/引文/网页材料、提取总结和内存/OCR等internal_purpose用户形态提示不计证据；结果不带被拒绝内容摘录。
- H02-05：对话分支复制的role=user消息必须排除，即使拥有非NULL import_run；content.context_import=true、引用导入、重放历史均不能作为新原始证据。普通新发原始user消息仍可计数。
- H02-06：启用之前的旧原始消息不回扫；只处理当前消息，无全历史抓取作业；绑定撤回/账户停用发生后同一已有工具对象也拒绝新增。

## HSK-03 范围、敏感与安全生成

- H03-01：六kind及全部枚举值分别可归一化：language(zh-CN/en)、output_format(markdown/plain/json)、detail_level(concise/detailed)、docs_first(on)、review_before_test(on)、management_layout(list-create)。docs_first与review_before_test独立存在。
- H03-02：不支持的kind/value、不相符当前消息、否认/纠正语句不被错当正向证据；例如“不要默认英文”不能学习language=en。当前请求明确要求始终优先。
- H03-03：“仅这次”“在当前项目”“临时授权”“永不审批”“额度/重置卡/过期时间”不固化；含fake密码/令牌/私钥/证件号等敏感文本不进入习惯表摘录、Skill、返回值与日志。
- H03-04：生成child只含平台模板SKILL.md，parse_skill校验通过、合法稳定namespace/name、description触发范围和显式请求优先/权限不扩张声明；无scripts、命令、外部URL自动执行或自动任务。
- H03-05：生成重试/重复证据不产生副本；与手工同名child冲突明确处理，不覆盖手工文件/绑定；实际文件长度有界、不接受用户任意正文生成。

## HSK-04 受控元库与真实Agent读取

- H04-01：通用助手及绑定父元Skill的同owner Agent可list本人有效child；未绑定Agent、其他owner、未启用/已关闭入口均不得列读。相同owner的不同已授权Agent共享本人习惯是允许行为，不误测为泄漏。
- H04-02：list返回真实skill_id/version/触发说明；read按显式当前version读取真实生成SKILL.md，错owner/非生成child/disabled/forgotten/不存在均拒绝，绝不静默换成最新版本。
- H04-03：父元Skill本run版本、当前binding、账户skills.read权限/账号状态及学习开关每次工具调用检查；解绑、父版本变化或关闭后已创建reader亦拒绝。
- H04-04：修正child生成新version后旧read(id,oldVersion)拒绝；新运行重列新version可读；旧模型已收到的内容不热替换，不声明能够从供应商上下文撤回。
- H04-05：实际受控Agent连续三次record后，下一新run只给简短任务，模型必须先真实调用list、再从工具结果取得id/version调用read；断言工具正文含正确生成规范且进最终模型messages，而非模型最后自称遵从。
- H04-06：旧已发布Agent skill_ids/pins快照前后完全相同；关闭习惯入口后手工显式绑定child仍按原普通Skill授权可读；停用/遗忘child则原普通reader也被撤权。Work仅list/read，不注册record，不把task文本当用户证据。

## HSK-05 来源、修正、停用、遗忘

- H05-01：候选/生成项展示状态、独立证据数、child版本及有权限原消息来源；源会话/消息删除不回显已删摘录，证据失效后不能自动使用不再有效child。
- H05-02：本人以当前revision修正合法有限值，旧revision409；纠正后旧自动观察不静默覆盖。跨账号/profile ID拒绝，且不能通过action泄露存在性。
- H05-03：停用/重新启用状态明确；无child且2/3候选暂停不积累，恢复仅learning并保留2，暂停中新消息重试不能晋升，恢复后新有效第三消息才生成。已有child但来源不足拒恢复。停用不删原聊天或手工Skill，切换后list/read及普通绑定遵守对应状态。
- H05-04：忘记清除学习摘录/候选内容，child学习入口和普通读取撤权；保留不含原文抑制水位。旧消息重复/回放不能立即重生；手工普通Skill和原聊天保留。
- H05-05：明确relearn后只能累计新的原始消息，再达门槛才可生成，旧来源不能计入；关闭/开启总开关不等于relearn，不绕过忘记抑制。
- H05-06：界面停止学习/停用资产/忘记三种说明与效果分别验证；来源不可用时显示原因而非空白或恢复历史正文。

## HSK-06 故障、账本与兼容

- H06-01：学习工具业务拒绝/存储故障不虚报“已生成”；原普通问答可以继续，错误/跳过状态可见；重试不翻倍证据或副本。
- H06-02：不另调隐形模型，不启动独立提取任务；受控工具模型所有调用均计原Ask账本，原始3消息和读取run账本可对照，生成模板本身模型调用数0。
- H06-03：账户/父绑定在record或read前被撤销、child在读取前被修正等并发反例明确拒绝，不自动重放有副作用工具。
- H06-04：原Skills上传/绑定/读取撤权、Memory候选审核/作用域/删除、Agent版本固定、Ask SSE8项与Workflow最小回归；Work无学习写能力。
- H06-05：最终隔离生产构建与真实页面开启/列表/详情/修正/停用/忘记，手机关键入口、无脚本错误；部署由root按既有脚本核对当前数据/密钥，不以测试库恢复生产。

## 当前状态

共33条设计用例，已按冻结API和修复复审后的源码执行独立分层验收；最终86后端、15组件/读取器、14+4真实浏览器及未实测边界见[QA报告](habit-skill-qa.md)。实际报告按批次与证据层级分别记录，不把设计数量当通过次数，不把工具替身证明当真实供应商质量验收。
