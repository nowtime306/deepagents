# 平台习惯元Skill后端开发

依据docs/habit-skill-plan.md，默认关闭，只有元Skill授权且当前原始本人Ask消息可通过record_habit_preference(kind,value)取证，3个真实不同run消息生成私有纯文本子Skill。消息/附件/助手数据不互混，不做后台扫描或新模型调用。分支旧导入content.context_import与internal_purpose排除。Scope仅本人global，项目限定语句不学。

API: GET/PUT /api/v1/capabilities/habits，PUT {enabled}。GET {enabled,meta_skill_id,meta_skill_bound,preferences:[{id,kind,value,status,evidence_count,skill_id,skill_version,updated_at}],options:{kind:[{value,label}]},threshold:3,scope:global}。POST /install安装绑定元Skill，不自动开启；POST /preferences/{id}/correct {value}；DELETE /preferences/{id}忘记；PATCH /preferences/{id} {enabled}；POST /preferences/{id}/relearn仅收未来消息。读取skills.read、管理skills.manage；工具record还需chat权限和当前Ask lease。

步骤：079迁移(settings/preferences/evidence及skills来源nullable关联)→有限偏好匹配/生成/来源guard→私有API→元Skill受控三工具→现有skill_backend对派生资源撤权→隔离测试及旧Skill版本回归→独立审查后QA。工具list/read实时列当前获准版本，read显式version，过期拒绝不静默换；不动态扩大普通skill_ids/pins、不修改发布快照。

六枚举language zh-CN/en, output_format markdown/plain/json, detail_level concise/detailed, docs_first on, review_before_test on, management_layout list-create。只接受带全局长期标记的完整原始简短消息，排一次性/项目限定/引用资料/secret/权限/免审批，不接受模型自由正文。生成固定指导正文并用parse_skill验证。有限范围不识别的习惯继续按当前请求执行，不声称已学习。

所有变更在当前分支保留原数据和密钥，无新依赖、不提交推送。安装不会覆盖同名人工技能；修正创建新派生ID并撤旧；忘记清文件与证据留无内容suppression水位；手绑子Skill沿旧授权但禁用/忘记/来源失效立即阻止读。全局学习开关只约束元库自动工具，显式手绑的正常子Skill不因关总开关而擅自改变原授权。

## 当前 API 与语义

preferences包含revision/manual_confirmed/skill_enabled，设置包含meta_skill_enabled。纠正body {value,revision}；暂停/恢复PATCH {enabled,revision}；DELETE ?revision=N；重新学习POST {revision}。来源GET /preferences/{id}/evidence仅返回message_id/conversation_id/created_at/available，另需chat.read，不回显正文。状态learning/active/disabled/forgotten；候选无技能暂停后可恢复learning，不能冒充已生成。已有技能来源不足不能恢复active。

install不自动重新启用手工停用的元Skill(409)，不同内容同名手工Skill不覆盖。元Skill安装从assets/skills/habit-skill-builder/SKILL.md读取，正常私有导入并合并通用助手skill_ids，其他绑定保持。读取设置权限skills.read、修改skills.manage、安装另需chat.manage。

record当前消息由运行上下文自动查询，模型不传message_id；当前interactive原文且非context_import、非memory/OCR，lease/cancel/account/父Skill版本和授权实时校验。兼容原inline Ask的NULL lease（当前RUNNING且token确为NULL）；durable Ask/Work需匹配有效lease，Work无记录工具。工具工厂仅把可预期4xx APIError转显式rejected/unavailable且无正文，底层/API仍严格拒绝；运行失效、5xx、异常或取消不吞。未授权/过期不伪成功；普通无习惯请求被误提议拒绝后仍能正常回答。

B消费模式：list实时列出当前有效私有派生skill_id/version；read必须给显式版本，不返回失效正文。未改普通skill_ids/pins/发布快照。明确手绑派生Skill受原授权且总开关不替代该授权，但派生资源在正常能力校验和每次后台读取都检查来源/撤权。父已发布Agent版本下线则旧习惯工具也拒绝。

来源删除减少实时证据计数并阻止自动及显式手绑读取；status可能保留active，但有效性还需skill_enabled且manual_confirmed或证据≥3。遗忘清所有历史派生文件及描述、证据和值，保留无内容抑制水位，不删除原会话或既有审计/模型已接收的历史上下文。新学习只收水位之后消息。纠正创建新Skill ID并撤旧，不热改原普通Skill版本。生成正文有固定适用场景/操作约定/授权边界，内容来自枚举不是模型自由指令。

## 作者验证与已修复问题

最终7模块54项通过、0跳过、37.56秒，业务源码前后完整冻结一致。含新30项及原技能撤权、Agent版本/绑定、能力API、持久Ask、Ask流式回归；各旧批次不累加当一次全量。

新案例包括真实本机Fake模型通过完整Ask harness三次record自动生成正规Skill，后续普通任务list/read并持久化工具结果；普通请求误调用record得到rejected后仍正常最终回答；四次并发相同消息只计一次；当前来源/内部或导入消息/取消/禁学/遗忘/跨账号/只读角色；Work真实队列租约适配只读工具与取消拒绝；发布meta pins保持且下线撤权；两证据候选暂停/恢复/第三条生成；全部历史派生遗忘擦除；安装同名人工不覆盖、只合并元绑定。调用均为隔离数据库及本机替身，无真实外部模型。

早期自验发现并修正：PostgreSQL-infinity无法由psycopg日期读取（079改有限epoch默认）；中文JSON词边界；测试误用了不存在的automation origin值（原schema是schedule）；根审查遗忘description/历史派生残留、固定Skill正文不足、候选恢复陷阱HSK-FE01、学习拒绝中断普通Ask HSK-BE03。失败日志保留，不把旧失败改称通过。所有数据库均独立随机_test并finally精确删除；不处理真实用户历史或修改当前部署，不提交推送。

定向Ruff通过，当前源码+079+资产SHA见.local/team-workspace/habit-skill/backend/freeze.json；原文件精确备份与changes.diff同目录。作者冻结后等根独立审查再交QA执行，不以本作者结果替代QA。

## HSK-BE04 / P2 暂停恢复水位修复

QA静态准备提出后，作者先在新隔离库复现：两条证据→暂停→新真实消息提议被拒→恢复→用暂停期held工具重试，实际错误生成了第三条。首轮1 failed/29 deselected，9.64秒，失败日志保留。

最小修复仅在preference由disabled恢复启用时将learning_after推进now()，保留原有效证据计数；候选恢复learning、已有技能恢复active皆采用此水位。来源计数不按新水位过滤，因此原两条/三条仍有效；暂停期消息及更旧重放不再获新证据，恢复后新真实消息可继续累计。

最终七模块55项通过、无跳过，47.51秒，业务源码前后冻结一致；随后仅整理该SQL参数长行以通过Ruff，又将候选/已有技能两条关键反例在新库验证通过（2 passed/29 deselected，6.57秒，源码冻结一致）。分两批报告，不相加冒充57项单轮回归。定向Ruff通过。freeze.json已更新最新源码/资产及两份结果；等root独立复审再交QA。

## HSK-BE05 / P2 有限偏好语义修复

独立QA实测英文歌曲被当回复英文，作者新库再次复现英文歌曲、中文小说两条真实工具调用均错误learning（2 failed/38 deselected，6.48秒），失败证据保留。没有将兴趣误判当用户习惯。

修复不使用通用NLP：语言、格式、详略三类均要求枚举词与本类明确沟通意图形成局部关系。允许“回复→默认/使用等有限连接词→中文”或“中文→回复”，“answer in→Chinese”或“English→replies”等；不再通过全句另一处“回答”、泛“使用”或泛“装修风格”就认定。文档先行、审查后测试、列表新增仍沿原有有限有序模式。不能确认时工具rejected，普通任务仍继续，用户可手动纠正。

新增音乐/小说、混合英文歌曲+回复详略、装修风格及混合意图、Markdown编辑器中英文真实工具反例，均无偏好记录；保留指定中英语言、明确文档输出格式、回复详略正例。原裸“今后默认用英文”没有沟通对象，不再视作明确回复语言，自验正例改为“今后默认用英文回答”。中间64/66项批次仅是开发阶段证据，不替代最终语义版本。

最终受影响七模块78项通过、0跳过、43.73秒，生产源码前后冻结一致；Ruff通过。freeze.json含最新服务/资产SHA和最终日志文件，已重新交root独立复审，QA正式移动版本执行仍须复审放行。没有更改已部署用户数据或调用真实外部模型。

## HSK-BE06 / P2 已删除派生技能不虚报生成

根审查提出后，作者通过真实Skill DELETE API复现：派生技能被删除，外键将profile.skill_id置空但保留active，新真实消息仍返回generated而没有技能。反例首轮1 failed/54 deselected，9.39秒，原失败保留。

最小修复：record在有效来源、状态与水位检查后，对active且skill_id为空返回409 HABIT_SKILL_MISSING，要求明确纠正或重新学习。工具边界呈rejected，不插入证据、不自动复生、不声称generated；同名人工、父元Skill及原会话不改。未改普通Skill停用与profile暂停两层既有机制。

反例验证删除后原child仍404、证据保持3、父元Skill内容版本不变。最终七模块79项通过、无跳过，44.97秒；源码冻结一致，Ruff通过。freeze.json记录最新源码/资产及最终日志，等待root独立复审与QA增量验证后部署。
