# 工作流版本与开发Release独立测试设计

2026-11-03，依据用户本轮请求及产品已同步核心规则：draft revision、immutable release version+active、唯一current_published。已同步[正式产品WVR-01～08](workflow-version-requirements.md)及原子恢复保护补充，接口字段依开发合同；当前设计未执行，不提前宣布通过。作者自验→独立review→最终源码冻结→QA；发现新bug先修复复审再复测。

## 版本基础与只读边界

- VT01：发布v1/v2后snapshot配置、definition、layout、name/instructions、模型与资源pin、schema/digest完整；编辑draft不回写既有版本。
- VT02：旧版本无layout按PRD诚实兼容，不能复制当前draft布局伪造历史；保留旧schema可运行及source缺失提示。
- VT03：version序号按workflow独立单调，发布v1/v2→回退v1→新发布仍v3，不复用/覆盖v2。重复同修订发布的幂等含义保持。
- VT04：active表示该版本可调用，不等于current。切current不隐式修改任一版本active，不自动下线其它已授权版本。
- VT05：跨owner查详情/版本/运行与操作404或原严格策略，不能通过猜id泄露存在性；真实viewer只读权限能浏览而管理操作403；Origin403与角色403分别证明。

## 当前发布指针与固定调用

- VT06：v2→v1切current后列表/历史侧栏唯一当前标识一致；选中版本内容完整且发布指针原子更新，不执行workflow/LLM/工具。
- VT07：target inactive、资源已停用/撤权/改版、错误versionId均拒绝或按PRD明确待配置，不静默启用target或改读latest资源。
- VT08：同workflow其它active版本、API Key的version_id、Ask/Work已固定引用、历史run snapshot/output/usage在切current前后完全不变；显式v2调用仍执行v2。
- VT09：current目标随后下线/源删除的指针行为按产品合同核对；显示不可用或清空的规则必须确定，不返回旧定义伪成功。
- VT10：并发publish/set-current、两次current写遵守服务端CAS/锁，不丢更新、不出现两个current；失败明确冲突，不能自动重发副作用。

## 恢复为草稿

- VT11：本人恢复历史版本到当前draft须CAS；新增draft revision，内容/layout/schema和默认模型/指令按该版本恢复，tested标记清空，不改变current或历史版本；同一事务先把当前已存完整draft复制为独立未测试/未发布备份workflow，返回backup_id/name，可列表访问。
- VT12：客户端有未保存dirty时明确提示后再恢复；取消保留当前编辑；确认后先CAS保存dirty再restore原子备份+恢复。仅PUT后覆盖不算备份，必须检查独立backup实体完整保存刚才本地内容。保存失败/JSON无效不restore，服务端CAS拒绝另一窗口已保存后的旧revision。
- VT13：restore后不能立即发布，单节点成功仍不能解锁；保存/整图测试/资源验证满足后可发布新version，version不倒退。
- VT14：restore不复制旧tested_pins或使用历史成功结果冒充当前通过；资源当前不合法不得通过还原旧definition绕过权限检查。
- VT15：两个restore/restore与save竞争至多一个基于相同CAS成功；失败不改draft/current/versions、无新run/任务/费用，也不留下独立假备份；模拟备份插入后恢复失败仍须整体rollback。

## 从历史复制独立工作流

- VT16：历史v1复制产生新UUID、本人owner、独立draft revision1、untested/无发布指针；name/definition/layout/schema/指令对应历史v1，不读现draft v2冒充。
- VT17：不复制历史运行、model usage、API Key、授予他人的权限、外部凭据、资源访问授权；引用资源仍是本人可用资源且受实时验证。
- VT18：编辑/发布新副本不改源draft/版本/指针，编辑源也不改副本；副本首次发布从v1开始，不继承源version号。
- VT19：源inactive/被删除/不可用资源时按正式PRD规则处理，不暗中恢复权限；另一个owner不能复制或restore源。
- VT20：重复点击/未知网络结果按协议处理，不无限创建相同副本；任何copy仅创建配置，不自动执行模型/HTTP/MCP/代码/任务。

## 中文界面与原功能回归

- VT21：列表和历史侧栏区分草稿修订、发布版本、active和current；按钮中文可访问名称明确，不把“恢复草稿”“切换发布”“复制新流程”合成一个模糊回退。
- VT22：历史详情完整只读、选择不同版本正确换内容/布局；新动作成功刷新列表/侧栏，迟到旧响应不能覆盖新流程/版本。
- VT23：错误/409/无权限/资源失效有明确提示，保留可继续编辑的草稿；网络错误不伪成功；390px面板/对话框可用、无脚本错误。
- VT24：原15节点单独测试、Dify变量与撤销、schema输入、原同步HTTP200/JSON事件、Ask SSE、Work审批/版本资源固定、外部API Key幂等保持，相关定向而非整系统数字借用。

## 开发Release归档与校验

- VT25：每批开发独立releaseId/时间/基线或parent关系/变更说明/构建与测试证据，不用未变化的git HEAD伪装本批未提交源码。
- VT26：开发前/后源快照与manifest逐文件SHA可核；跟踪和untracked业务都包含（尤其apps/web/src/lib/api/platform.ts、正式独立tests），已删源码保持删除。
- VT27：归档不含.env运行凭据、key/pem/私钥、用户数据库/附件/.local/node_modules/.next及git元数据；用临时fake secrets验证排除，不读取真实secret来扫描。
- VT28：归档缺文件/多文件/内容或manifest篡改失败，越界路径/zip-slip/符号链接不接受；不把校验失败自动覆盖或“重新生成manifest”掩盖。
- VT29：归档、校验不运行用户代码或自动回滚DB，不提交推送、不删除既有未提交改动；源快照可审阅不等于允许直接覆盖当前工作区。
- VT30：已审发布脚本使用本轮release元数据，部署后的构建id/源码digest/健康可追溯；当前用户数据/主密钥同列摘要保留，由root正式发布时核对，不用测试库恢复生产。

## 测试环境与结果记录

使用新的随机owner_agent_workflow_version_qa_<随机>_test、唯一数据目录、两个fake账号与无模型transform流程。只在审过版本启动新的隔离API/生产Next站，端口先探空，禁止原生产3100/8100/3120、真实模型或用户记录。组件mock、算法/格式单元、隔离API、真实生产代理浏览器分别记录。

发布v1/v2的基础夹具用纯trim/upper/lower转换，模型工厂可记录真实生成次数应为0。发布指针/restore/copy前后精确比较immutable rows、原run和Key字段及计数。release测试在本轮私密临时伪仓库中创建fake.env/key与正常源文件，不在用户主仓库注入secret样例。正式release按platform-version-control.md只归档确切已提交源码；VT26的untracked覆盖指开发成果先完整纳入该提交，不要求把运行时dirty工作区混入提交归档。

当前：30条设计已映射正式WVR产品规则；API最终字段与实际执行记录待开发/审查交接，不作为测试猜测或通过。


## 产品核心合同补充（正式WVR编号待同步）

inactive版本允许预览、复制及恢复到未测草稿，但set-current必须active且资源pin当前有效。下线当前版本清空current指针、不silent fallback；重新enable旧版不自动选current。新publish设置新current并推进独立release_revision；重复publish原同修订只复用既有版本，不改变pointer。draft revision与release_revision不是同一个CAS。

旧版本layout未存时返回{}，界面解释默认排布而非历史原坐标。旧workflow current=NULL，不猜最高active。无单版删除本期不做，保留历史run/key引用；删除整个workflow沿旧语义。页面继续明确运行所选version，不新建current/runs或改外部固定version调用。release文件仅内部保存，不公开下载敏感快照，不commit/push。


## 正式产品编号与执行层级映射

- WVR-01：VT01、03～05、21（真实API/组件/真实页面）。
- WVR-02：VT01～02、22～23（不可变完整快照、dirty预览返回、旧layout默认/迟到响应）。
- WVR-03：VT11～15（原子备份+恢复、CAS/失败回滚、dirty保存保护、未测不可发布）。
- WVR-04：VT16～20（独立新实体、原dirty内容保留、不自动跳转、不可用资源待修、无授权/运行复制）。
- WVR-05：VT06～10、24（active与current、release_revision、明确version请求、旧Key/Agent/旧run固定）。
- WVR-06：VT05、14、19、23～24（两个owner、真实只读角色、审批/旧行为）。
- WVR-07：上述API断言（最终endpoint如产品预期，旧字段兼容、无secret、正确CAS/错误）；新源码review后正式执行。
- WVR-08：VT25～30（临时伪源码仓库/归档摘要/无runtime secrets/失败状态、实际部署证据由root另验）。

产品最终约定：任何layout_known=false仅指未保存自定义布局，不能断言“旧年代”；无单版删除。不就绪资源允许保留历史配置到待修draft/copy，不能自动替换模型或获得访问权，只有后续执行/发布做完整权限/pins校验。


## 最新规范化与提交后读取失败合同

历史raw.definition保留不可变原文（包括旧裸节点缺parameters等），另外canvas_definition为当前画布安全投影，schema_supported明确可编辑能力；普通draft GET返回规范化definition。独立回归保留裸0.0.25风格数据的raw相等与canvas完整字段检查。

restore POST事务成功后返回完整canonical workflow及backup id/name，UI即时采信已提交实体。随后刷新/列表读取失败只是次级警告，不能把已成功恢复说成失败、自动重发restore或丢备份入口。测试需真实写入+有意阻断后续read，核对POST次数1、数据库新revision/backup1与页面新内容一致。

## 独立审查回归补充与浏览器授权

- WVR-FE01→VT12、22：在dirty草稿进入历史预览后，保存按钮禁用，模拟点击及键盘保存均无PUT；返回编辑后仍保留原dirty内容。
- WVR-FE02→VT02、22：草稿有自定义坐标而所选历史layout为空时，预览使用默认排布；不沿用草稿节点坐标。
- WVR-FE03→VT20、23：restore/copy的POST真实成功而后续GET失败，仍保留已成功状态和备份/副本入口；不能留着可再次创建的原对话框。精确核对POST仅一次及数据库备份/副本仅一份。
- WVR-FE04→VT22、24：预览vN后打开结构不同的历史运行，退出版本预览并显示该run.snapshot；不混用预览definition、layout或当前草稿。

浏览器对生产3100和独立虚拟站3131的origin曾拒绝；用户随后明确允许3131，基线真实体验已完成。本轮WVR仅在同一3131隔离站验收，生产3100仍不访问，页面交互使用Cua。构建、mock与API结果分别记录，不代替真实UI证据。上文测试工具不执行Git提交/推送是其自身行为边界；用户已另授权根协调者在验收发布流程中提交并推送，并单独登记分支及提交记录。
