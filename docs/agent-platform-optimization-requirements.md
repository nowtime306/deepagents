# 知行平台优化需求：浏览器演示生成技能与能力对照

版本：产品冻结 1.0，2026-11-03。适用当前 Windows 本机知行平台；本轮实现范围为 WVR 工作流版本控制及 BSK 浏览器录制技能闭环。此前 SYS/EXP 已交付能力保持，真实外部模型联调继续跳过。此文档是需求，不是开发完成报告。

## 1. 调研方法与当前基线

用户所列项目去重后共 13 个仓库。以各仓库官方 README、文档和必要协议源码确认能力，关注度使用官方 GitHub 页面快照；不声称这些项目覆盖全球最高排名，不把 stars 当可靠性证明。DeerFlow 2.0 与 DeerFlow 是同一仓库；实时语音框架与可视化自动化产品用途不同，不能比较单一总分。日期为 2026-11-03，页面和后续 stars 快照时间可能不同。

本地已检查 `services/api/app/skill_library.py`、`browser_workbench.py`、`browser_tools.py` 及当前工作流源码；交叉参考 `system-requirements.md`、`platform-expansion-requirements.md`、`workflow-project-browser-plan.md`。当前已经有：Ask 持久执行与 SSE、模型/联网配置、Agent 资源和发布版本、Work 沙箱与审批/接管、知识库检索与评测、15类工作流节点及单节点调试/受限循环/并行、普通 Skills 与习惯子Skill、账号组织权限、用量预算与监控。不能把这些重新列为未开发。

真正新缺口是：真实浏览器人工操作尚不能在平台显式录制、审阅并转为可调用的私有 Skill；工作流历史虽然有发布快照，却缺易用的完整预览/恢复保护/独立复制/唯一当前发布指针。后者按 [WVR-01～08](workflow-version-requirements.md) 本轮单独开发。

## 2. 13个官方项目：借鉴与边界

以下“知行取舍”是产品判断，不是上游功能保证，也不自动成为本轮承诺。

1. **[Tencent/BrowserSkill](https://github.com/Tencent/BrowserSkill)**：CLI 与 Chromium 扩展在可见 Agent Window 操作真实浏览器；语义录制的状态—动作轨迹可供开发分析。知行取舍：接入显式录制与受控重放，不复制个人桌面代理的所有权限。只有该项需要本轮引入新的本机依赖。
2. **[LangGraph](https://github.com/langchain-ai/langgraph)**：低层有状态编排、持久执行、中断和记忆；[官方 README](https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md)。知行已经采用该基础，优化资源授权和恢复证据，不另起执行循环。
3. **[Dify](https://github.com/langgenius/dify)**：可视化 AI 工作流、RAG、模型和可观测平台；[官方 README](https://raw.githubusercontent.com/langgenius/dify/main/README.md)。借鉴画布调试和版本导航；知行已有节点与检索，本轮补 WVR，不导入其整套应用/租户架构。
4. **[n8n](https://github.com/n8n-io/n8n)**：可视化自动化、集成节点、模板、代码与审批；[官方 README](https://raw.githubusercontent.com/n8n-io/n8n/master/README.md)。借鉴设计/测试/生产分离和恢复保护，不承诺其全部集成生态。其 fair-code/Sustainable Use 许可不能当通用 MIT 复制。
5. **[DeerFlow 2.0](https://github.com/bytedance/deer-flow)**：长任务 harness，组合子Agent、记忆、隔离执行和可扩展技能；[官方 README](https://raw.githubusercontent.com/bytedance/deer-flow/main/README.md) 明确2.0是重写。知行已有同类基础，借鉴成果与进展易读展示，不重复移植另一套Agent服务。
6. **[OpenClaw](https://github.com/openclaw/openclaw)**：个人助手 Gateway、渠道、工具和技能；[官方 README](https://raw.githubusercontent.com/openclaw/openclaw/main/README.md)。借鉴统一控制面和状态诊断；知行已有通知/工具授权，完整多渠道双向会话不是本轮 BrowserSkill 前置条件。
7. **[Deep Agents](https://github.com/langchain-ai/deepagents)**：规划、子Agent、文件系统、上下文管理、记忆、人工审核与按需Skill；[官方 README](https://raw.githubusercontent.com/langchain-ai/deepagents/main/README.md)。知行二开基础保持，生成Skill走原解析器/权限，而不是另设全局宿主工具权限。
8. **[GPT Researcher](https://github.com/assafelovic/gpt-researcher)**：规划研究问题、并行采集、来源汇总、引用报告与导出；[官方 README](https://raw.githubusercontent.com/assafelovic/gpt-researcher/master/README.md)。知行已有联网和成果，专门研究报告的来源覆盖率及可复验质量还需独立场景验收；列后续 P1，不在本轮假称研究引擎全部接入。
9. **[PandasAI](https://github.com/sinaptik-ai/pandas-ai)**：自然语言查询表格、图表与多DataFrame分析，提供隔离代码执行示例；[官方 README](https://raw.githubusercontent.com/sinaptik-ai/pandas-ai/main/README.md)。知行能在受控代码任务中分析文件，但不是同等数据语义层；后续再设计表格预览/数据字典/可复现分析，不能用知识库支持XLSX冒充完整数据分析。
10. **[TaskWeaver](https://github.com/microsoft/TaskWeaver)**：代码优先分析、规划与插件、状态化执行、代码验证及经验复用；[官方 README](https://raw.githubusercontent.com/microsoft/TaskWeaver/main/README.md)。官方仓库本次抓取标记 archived，作为历史设计参考而非新维护依赖。知行已有执行历史/习惯学习，但不承诺跨任务保留Python内存；后续评估受控数据分析工作台。
11. **[ChatterMate](https://github.com/chattermate/chattermate.chat)**：客服会话、人工转接、嵌入组件及工作流；[官方 README](https://raw.githubusercontent.com/chattermate/chattermate.chat/main/README.md)。知行面向本人工作空间，客服排班/工单/访客入口是不同产品线，暂不纳本轮，避免误把任务审批等同客服转接。
12. **[Pipecat](https://github.com/pipecat-ai/pipecat)**：实时语音/多模态会话管道和多Agent协调；[官方 README](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/README.md)。知行语音协议不等于实时通话；本轮保持独立语音合成/图片理解导航隐藏，不为对照而重新开放。
13. **[LiveKit Agents](https://github.com/livekit/agents)**：实时会话、WebRTC、调度、轮次检测和语音Agent测试；[官方 README](https://raw.githubusercontent.com/livekit/agents/main/README.md)。未来如新增实时语音需单独设计会话服务/终端权限/延迟验收，不因已有Ask SSE宣称已支持WebRTC。

精确 stars 已由根协调者在 2026-11-03 12:45:17～25 GMT+8 抓取13个官方页面，13/13成功；子角色首次直连被系统网络限制，未将该失败冒称成功。页面HTML SHA和README副本留在 `.local/team-workspace/browser-skill/product/`，不把第三方榜单当官方精确数。前一批六个可视化候选研究见 WVR 文档；其中 Flowise/Langflow/Coze/Activepieces 不混入本13项样本。

## 3. 本轮产品闭环与页面

入口为能力中心→Skills→浏览器录制；继续使用列表＋新增按钮。普通Skill导入、习惯学习及当前选择不改变。录制列表显示名称、站点、状态、步骤数、更新时间、已发布Skill入口；只读账户可看本人记录，不显示新增/停止/保存/发布/删除写控。详情依次为“操作路径”“运行参数”“生成Skill”，不把原始JSON当主界面。

新增弹窗选择已注册专用浏览器连接、起始URL和名称，说明只录制本次独立窗口。点击开始后 showing starting，依赖握手确认后才 recording；显示停止/取消。停止导出后进入审阅；发布是单独动作。未就绪显示具体缺项和操作说明，不能展示绿色已连接或假造步骤。本轮仅有一个主机本地连接，**实际录制与重放仅主机管理员可用**；普通账号仍可查看/管理本人安全记录和Skills，界面明确说明权限限制，不能只显示“已连接”诱导使用。新功能不改 Ask SSE，也不将所有管理页改成SSE；录制使用独立异步生命周期和查询。

页面 Demo：[浏览器录制与技能预览](demos/browser-skill-demo.html)，为无网络交互示意，不能作为真实浏览器验收证据。

## 4. BSK冻结需求与验收

### BSK-01 受控依赖与状态

固定官方 CLI/扩展0.4.1，MIT版权与许可随分发保留。Windows x64 CLI和 Chromium125+ 扩展可本机使用；是可见浏览器/daemon/扩展方案，不是现成Python Playwright包。专用 profile/BSK_HOME/受监督进程不接用户日常浏览器。本轮只有单host本地连接，未实现每owner独立浏览器身份映射，因此host管理员本人方可创建录制和运行adapter，不能给普通注册用户接管管理员profile/cookies。未来per-owner远程连接配对属于后续版本。连接由运维登记，客户端不传可执行文件/目录/命令/端口。

验收：无CLI、版本不符、扩展离线、连接忙各有中文原因；未连真实扩展不能录制；已存在用户浏览器和设置不受改动；本机状态只返回非敏感摘要。

### BSK-02 明确录制生命周期

显式选择连接和http(s)起点，新建Agent session内的专用tab。每个连接同一时刻一个活跃录制，第二次返回忙。每步读取/结束按owner查；界面 starting/recording/stopping/review/failed/cancelled 与实际状态一致。停止幂等，导出成功且导入验证通过才review；空/不支持轨迹不假成功。用户可取消，重启后未能确认的录制显示中断，不静默启动新录制。

验收：刷新可查记录；重复停止无多份导出；权限撤销不能继续；取消只清本记录session/暂存；不得关闭其它用户tab。上游CLI只有record start/stop，start阻塞内部RPC await，不能杜撰CLI await子命令。

### BSK-03 真实轨迹与脱敏

仅接受服务端本次session产生的Trace v3 bundle，核对states引用及外置states目录内严格sN.txt路径（trace中的page为sN.txt），拒绝越界/符号链接/超限/不支持版本。video MP4、auditmetadata或手工虚构步骤不算录制。ref是录制时观察引用，不是可跨会话选择器。

平台不存原始VOM、fill值、select动态值、浏览器登录状态、Cookie或请求凭据；输入动态值转命名参数，无原始默认值。真扩展端到端验证已确认上游--redact-values会把普通fill变成***+redacted，并省略select选项；不是只有VOM遮盖。为生成普通可参数化路径，正式录制可不传该flag，但原始值仅限私有临时bundle和导入内存，平台必须全量参数化后清理，绝不持久或发给模型。password/OTP步骤只能人工处理并阻断可执行发布；目标和URL也校验敏感片段，网页文字始终是不可信数据。处理完成/失败/取消后清理本session bundle和recovery，不遗漏record-recovery.json；清理前校验路径归属。

验收：虚构秘密样本不能在响应/数据库/日志/生成资产找到；可展示操作类型、安全role/name/context、参数名和清楚的阻塞原因；不能只把密码替换成***就当可重放成功。

### BSK-04 路径审阅、参数与草稿

允许编辑标题/描述/参数名说明及受支持的定位描述，修改带revision CAS，冲突409保留本地编辑。首批动作集合由技术实现明确上报，至少完整覆盖导航→填写普通字段→选择→点击的本机演示；其它动作如无距离scroll、无目标switch_tab、unmatched、截断/歧义必须标红，禁止静默删除后发布不完整流程。参数名有界且唯一，引用存在，说明不含原始值，运行时必填。

验收：无目标/参数缺失/重名/未支持步骤不能发布；详情能定位阻塞步骤；保存成功刷新仍可见；读者无法写；草稿尚未成为Agent可选Skill。

### BSK-05 发布正规私有Skill

用户显式确认发布，生成平台现有合法SKILL.md和文本资源，受parse_skill校验，源录制/步骤/参数与发布版本可追溯。使用结构化受控轨迹，不生成任意宿主脚本。发布版本不可变、重试幂等；原草稿修改不漂移已发布内容。发布后显示Skill入口，仍沿现有私有选择/Agent发布授权，不自动授权所有Agent。

验收：新Skill可在本人Skills看到/查看，普通选取机制识别；跨账号不可见；旧Agent发布pins不被偷偷扩展；停用/删除后后续调用拒绝；发布过程零网页副作用。

### BSK-06 受控运行与真实结果

运行入口仅在Work授权上下文开放，调用固定skill_id/version和命名参数，服务端加载owner发布快照，不接受自由CLI/脚本。每一步fresh观察，role/name/context只能唯一匹配后使用当前ref；零/多匹配、人工接管或context失效停止并提示。任务目标域、权限、预算、取消和原浏览器操作审批全部继续有效；录制/发布不构成运行授权。原有通用助手可读取所绑定技能的说明，但Ask没有Work执行权时明确引导创建任务，不能暗中执行。

验收：真实fixture新session重放后页面值与提交结果正确；拒绝审批没有调用；未知副作用不自动重放；失败记录到步骤与原因，不把部分成功显示全成功；任务结束只收回自己session。登录/验证码人工完成，不读取用户日常浏览器数据。

### BSK-07 权限、保留与清理

本人skills.read可读安全摘要，skills.manage才录制/改稿/发布/删除，运行另需work.execute及原工具授权；后台每次校验，按钮隐藏不是鉴权。**本轮本地录制start还需host admin；录制后台job持续复查admin，Work重放每步持续复查admin；仅有skills.manage/work.execute不足以使用该共享本机连接。** 普通账号仅能管理其已有安全记录/Skills，不因此取得管理员浏览器身份。列表分页不返回其他用户数据。删除记录说明会清录制草稿和暂存；已发布Skill撤回/保留规则明确，不因清轨迹静默删除其独立版本；对应只读关联可标源已删除。

验收：普通skills.manage/work.execute用户start/replay本机连接被拒；管理员执行期间被撤销admin则后台/下一步停止；A猜B id时无读写/stop/publish/replay；运行中撤权下一步拒绝；合法旧Skill/习惯库不变。保留原用户数据和加密密钥，新增迁移单独编号且备份后应用。

### BSK-08 测试与发布门槛

开发自验→独立审查→QA→部署。分开报告无模型协议替身、官方CLI/扩展版本校验与真实扩展E2E。真实扩展必须专用profile和fixture新origin，人工或OS事件产生真正轨迹，再新session重放验证最终页面；不能用直接拼JSON证明实际录制。安装/握手失败时明确失败点，前端不开放可执行假入口。运行生产部署脚本，保留状态备份，健康/重启/迁移和原页面回归通过再声明上线。

验收包括：依赖不全、busy并发、停止幂等、restart、state缺失、路径越界、脱敏、参数CAS、owner隔离、Skill解析/发布、审批拒绝、目标歧义、未知提交、取消清理和手机页面。外部真实站点/模型/账号验收列待接入，不能用fixture冒充所有网站可用。

## 5. 开发API交接

平台使用现有/api/v1命名空间，具体字段由开发文档与实现一致：status；recordings列表/新增/详情/停止/取消/删除；revision受控草稿；幂等发布返回skill_id/version；受控Work执行入口。录制状态异步202并查询只限本新生命周期，不修改工作流现同步200或Ask SSE契约。数据库/进程管理/官方bundle细节见 [开发技术说明](browser-skill-development.md)。客户端不上传任意本地trace路径，不传owner或任意shell参数。

## 6. 后续优化需求池（未纳本轮实现）

- **OPT-01/P1 研究成果质量**：来源列表、引用覆盖、采集时间、冲突证据和可下载报告；复用已有研究/检索工具，验收事实来源与过时提示，不把更多搜索次数当质量。需基于真实体验补产品样本。
- **OPT-02/P1 数据分析工作台**：文件/表格预览、列类型、问题/代码/图表关联、可复验成果；复用隔离执行，不承诺跨任务隐式Python内存。独立设计容量与敏感数据边界。
- **OPT-03/P1 连接健康引导**：把已存在模型/MCP/浏览器/通知诊断组织为“可用/缺配置/失败/待真实效果验证”，每项直达配置与最近失败；先根据体验观察定位，不重复开发现有监控。
- **OPT-04/P2 渠道与人工协作**：若用户需要再设计双向渠道/客服转接，不能直接复刻个人助手Gateway或客服平台的整套授权。
- **OPT-05/P2 实时语音**：需单独的WebRTC/音频会话与延迟/打断评测；当前继续隐藏语音独立入口，不因调研自动新增服务。

不移植13仓库的全部功能，不导入无限表达式/任意宿主执行、不自动读取全历史账号、不默认接日常浏览器、不购买服务、不改现有用户注册/发布权限语义。体验师反馈将逐条附“已具备/可改进/缺陷/未来场景”的产品结论，有明确证据和验收后才能成为新开发项。


## 7. GitHub关注度快照（用户指定13个唯一候选）

2026-11-03 12:45 GMT+8，官方仓库HTML的stargazerCount字段；只是本组样本，不是全站排行。后续会变化，数字不代表功能验收。

- [Tencent/BrowserSkill](https://github.com/Tencent/BrowserSkill)：9,943 stars。
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)：44,356 stars。
- [langgenius/dify](https://github.com/langgenius/dify)：160,279 stars。
- [n8n-io/n8n](https://github.com/n8n-io/n8n)：209,412 stars。
- [bytedance/deer-flow](https://github.com/bytedance/deer-flow)：84,907 stars。
- [openclaw/openclaw](https://github.com/openclaw/openclaw)：393,759 stars。
- [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)：30,924 stars。
- [assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher)：30,444 stars。
- [sinaptik-ai/pandas-ai](https://github.com/sinaptik-ai/pandas-ai)：23,924 stars。
- [microsoft/TaskWeaver](https://github.com/microsoft/TaskWeaver)：6,161 stars；官方页面标记已归档。
- [chattermate/chattermate.chat](https://github.com/chattermate/chattermate.chat)：154 stars。
- [pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat)：17,016 stars。
- [livekit/agents](https://github.com/livekit/agents)：15,068 stars。

本组OpenClaw关注度最高，但其多渠道个人助手定位不自动决定知行整个UI/架构。本轮画布版本与浏览器录制仍按具体用户工作路径设计，参考ChatGPT清晰对话、Dify画布与列表＋新增的既有习惯。


## 本轮权限冻结补充（1.1）

2026-11-03：技术实现仅单Windows host本地profile/connection，并无真正per-owner浏览器映射。故实际录制及Work adapter重放限定主机管理员；前端解释与后端每次/每步及后台持续admin校验同时需要，不能用owner-scoped数据库记录伪称浏览器身份已经隔离。未来普通用户浏览器连接要另做owner配对/独立profile/撤销模型再开放。本条不放松记录与Skill的owner隔离。


## 真扩展实测纠正补充（1.2）

技术作者本机官方扩展实测确认：v3 reducer把redact-values同时作用于普通fill和select；此前仅按content emitter/help推断“不遮fill”不成立。最终规范见BSK-03：普通路径仅私有暂存原文后立即全参数化，password仍自动redacted并阻断发布。磁盘page是相对states目录的sN.txt，不是states/sN.txt。本纠正来自真实握手/输入/导出，不用旧静态调查覆盖实测。


## BSK-09 本机浏览器执行边界与单次任务授权（1.3）

### 为什么需要独立选择

BrowserSkill连接真实本机Chromium，不具有原OpenSandbox的出站隔离。开始/结束后检查顶层URL不能阻止网页脚本、iframe、图片、fetch等向其它域发请求。既有任务“默认禁止出站/授权域名”不能不加说明地覆盖此模式，也不能因为当前用户笼统授权开发就替未来平台任务自动同意。

### 本轮最小合同

1. 任务启动表单仅在浏览器环境、当前主机管理员身份和本机连接可用时展示**默认未选中**的独立选项“使用受信任本机浏览器”。普通用户看到当前受限原因；不能勾选或直接API获得该身份。
2. 选项旁完整说明：“可使用该专用浏览器已登录的账号；本机浏览器**不是网络隔离环境**，网页可能向任意域名发送请求。授权域名仅限制顶层页面导航，本次完整录制路径执行前需一次审批，执行中逐步校验。”不称其具有OpenSandbox等效网络边界。
3. 启动API新增布尔 `allow_local_browser`，默认false；true只允许browser环境+admin，冻结到本run不可变授权快照及运行字段（两者均需true），不从模型参数、Agent提示词或Skill文本推导。该字段只授权本次运行使用该模式，不扩大域名/平台权限。
4. false/字段缺失时不加载本机重放工具、不启动本机会话、不观察本机页面。既有旧run/工作流/自动任务保持false；本轮不隐式迁移为true。使用旧成果“创建新运行”也需要再次显式选择；恢复同一冻结run沿原snapshot，不重新推断授权。
5. 本次完整录制路径沿原browser_task的整组single-use journal，只需一次人工审批；不是每次点击都弹批准。服务端固定边界标识 `execution_boundary=local_authenticated_browser` 连同skill_id、skill_name、version、step_count、参数纳入审批args和digest；服务端核对名称、版本与发布轨迹长度严格一致，边界常量固定。完整安全步骤摘要不接受模型自由传入：审批UI按skill_id/version读取正规Skill的trajectory.json展示，版本/count不一致或读取失败时阻断批准，不把最新轨迹冒充所批历史版。固定发布快照和args共同确定整条路径身份。界面明确“批准本次完整路径，共N步”，突出“受信任本机浏览器 · 已登录账号 · 非网络隔离”，提供批准/拒绝。参数按原机制校验并避免敏感值进入审批/日志；发布仍零动作。
6. 工具暴露与实际调用均复查冻结flag；整条路径先消费一次single-use receipt，每步执行前另查当前admin、owner、work.execute/资源绑定、lease、取消和顶层导航约束，并以fresh观察定位当前唯一目标。不能仅依模型传入execution_boundary字符串；篡改模式/目标/步骤后digest不同，旧批准不能复用。撤销admin或拒绝审批时无下一步本机动作。
7. 本机模式中的domain字段诚实标“允许的顶层页面导航域名”，前后校验只能约束这一层；界面/运行详情持续显示非网络隔离标记。原隔离任务的代码/浏览器网络策略保持原实现，不全局改成仅导航检查。

### 验收

- 默认不勾选、旧缺字段、自动/流程调用及新恢复run未明确选择时，host adapter不可调用且无本机session；true+coding/普通用户请求拒绝。
- 选中浏览器模式后服务端快照为true，关闭/切换环境后新提交不意外继承；刷新运行详情明确显示边界，重启同run从快照读取。
- 整组审批显示固定版本、skill_name、step_count、完整安全路径与本机非隔离说明；换模式/改参数/改路径或重复消费receipt拒绝。批准整路径后逐步fresh/guard，拒绝时零动作，执行中撤权后续步骤停止；未知结果不自动重试整组。
- 真fixture可带跨域子资源，测试应确认产品明确说明“无法网络隔离”，不能伪写跨域已被拦截的通过结论。仍检查顶层越界拒绝和原OpenSandbox任务网络回归。
- 不新增防火墙/代理隔离服务、不自动接用户日常profile，不把未来多owner配对纳入本轮。当前实现本机闭环可以在真实授权后执行，不能靠永久关闭功能冒充接入完成。


## 审批粒度最终冻结补充（1.4）

本轮复用browser_task整组single-use journal：管理员先为本次新run明确allow_local_browser，再批准完整、已展示的固定版本录制路径一次。路径内每步做fresh观察和权限/lease/取消/顶层导航校验，不逐click请求人工批准。此前1.3讨论的“每步审批”已按这一最终合同修正文案；逐点击人工确认需要新的持久session/cursor/resume协议，未纳本轮。整组UNKNOWN不得自动整组重试，不能把批次批准当永久授权或跨run重用。


## 实际体验小改候选（1.5）

已在用户授权3131旧基线完成真实体验，见 [体验记录](platform-ux-experience.md)。建议本轮小改UX-01（检索页区分面向用户结果与模型内部指令）和UX-02（SKILL.md元信息不渲染成巨标题），均只调整显示、保留原数据/协议/权限；由根协调者确认纳入后按作者自验→独立审查→QA执行，不新增一轮大范围重构。Agent高级设置和初载模型提示列P3后续项。审批安全完整步骤按真实固定Skill资源读取，不接受模型传摘要；此项细节与BSK09已同步。
