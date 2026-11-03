# 图像检索与非Token费用开发

2026-11-02，对应EXP040/041/050；原数据/密钥保留、不提交推送，新增能力不恢复隐藏独立媒体页。遵循focused-change。新模块只注册独立router，由主协调集成，不编辑公共入口。

## 实现顺序与验收

1. 图搜图：复用已有DashScope多模态图片向量协议与私有图片索引。新增图片查询上传接口，校验格式/5MiB/模型维度；使用真实图片内容向量，不经OCR文字替代。图片查询不持久化，结果仍按知识库owner过滤。用固定本机向量检验近邻/非法格式/维度与权限。
2. 非Token价格与账单：074专用表，不改现model_usage及Token/缓存账本。价格版本不可变、单位限定按次/图片/音视频秒，来源及币种显式。管理员可配置，本人范围用量和账单预览/导入；唯一调用ID+计费项幂等，数量未知金额保持null；账单导入不能回写历史价格或转换币种。预览区分一致/差异/平台缺失/供应商缺失/未知，CSV下载防公式注入。测试非法金额、跨账号、重复导入和未知用量。
3. 视频帧检索：075专用处理表，固定模型/预算与私有帧。需真实受管媒体依赖，先核对可用ffmpeg容器；无依赖不得假装已抽帧。异步取消/清理和进程资源限制必须通过本机短片验证才能开放。不能把服务端任意命令执行或同步视频上传伪装成完整工程。

## 集成约定与范围

`app.media_retrieval_api.router` 和 `app.extra_cost_api.router` 由主协调注册。图片路径仍在capabilities/knowledge下继承knowledge.search/read权限；费用路径operations/extra-costs继承operations.read并再次owner/管理员判断。新组件由主协调挂到图片资料与运营页，无独立媒体导航。

外部模型与真实供应商账单仍待接入，本机替身仅验证工程契约。费用记录不能从一次请求猜测供应商计价数量；现Token预算与额外费用使用同一owner/currency锁和联合预留，未知金额继续冻结；没有明确计费契约的服务仍不能宣称账单级全覆盖。各EXP实际状态在自验后回填，代码审查通过且问题复审关闭后才交测试。

## 开发自验和首轮审查

图像查询/价格账单/联合Token与额外费用已有实际代码和组件，未作为整批验收完成。初次共享测试库遇到074早期schema缺表；换独立DB后再次因随机data目录与复用DB不匹配导致seed密钥503，按模块固定DB/data目录重建后17项全部通过。首轮类型自验发现ResourceDialog使用旧props，已修正，未交QA前发现。

主协调独立审查首轮不通过，R-MED-001～004均保留：
- 001：同模型item现在事务锁定单位/币种；预留先选全部生效最新版本再按unit选，防旧单位继续收费。
- 002：结算明确unit；通用money只call，图片路径分别image/call，其他秒单位不得按1结算。
- 003：可信迟到unknown→known只一次；管理员本人显式reconcile记录确认身份、时间和依据，保留原价格/来源；已确认不同回调409。
- 004：价格及账单均有稳定created_at+id键集续页及中文加载更多；额外用量明确最近500观测，不能伪装完整管理列表。
- 图片查询返回前再次比较model_id/revision/dimensions，变化不返回旧结果。

修复后图费19项通过（14.27秒，5新增回归+旧money7+vision7），Ruff/TypeScript通过。新组件ESLint零错误，仍有ExtraCosts首次异步加载的2条Hook告警，不能混称旧告警。代码审查待主协调复审，尚未交QA。

## 视频实现与集成

新增075、`app.media_retrieval_video.router`和API内`serve(app.state)`；主协调只在代码审查通过后注册/启动，shutdown cancel+gather。媒体镜像必须使用根.local/media-decoder-image.json的批准image_id，固定无网络、非root、512MiB/0.5CPU/pids64/read-only/cap-drop/no-new-privileges及32MiB tmpfs，输入只读挂载、输出仅私有job目录。Docker命令参数服务端固定，不接受用户command，也不在宿主ffmpeg解码。

任务队列状态持久化，内存预留与Coding5GiB/Browser3GiB使用同710016锁相互计入；旧RUNNING重启转UNKNOWN，只核对带job标签及镜像身份的容器后清理，未知结果不自动重放。取消确认停止后释放预留，失败清理仅该job索引帧，已完成可删除。每视频20MiB/前60秒/最多12帧，用户最多100份/1GiB；分页列表和实际已索引帧数，不造完成百分比。帧时间为采样网格时间，不宣称原始每帧精确PTS或完整时序理解。

帧向量请求前后检查权限/活动job/模型revision/索引维度；未知用量不填Token0。按已配置call/image费用预留后请求，返回一个图向量后按真实1帧结算；预算缺明确图片计价拒绝发送。未接入的音频秒/视频秒模型适配仍不能标完成，价格和对账工程支持其单位不等于所有供应商已自动采集。

原生基础自验1项通过9.66秒；追加低预算排队/取消前不触发decoder和模型后2项通过9.77秒。后续持久UNKNOWN回收/运行中取消/容量争抢仍需补充自验与独立审查，不以基础2项代替全部视频验收。

集成注意：删除知识库必须先锁定knowledge_bases父行再核对active video jobs（PENDING/RUNNING/UNKNOWN或holds_memory），活动时409要求取消；终态先清理视频源/帧再删除，不能CASCADE丢失容器归属。视频上传持有父FK锁，与该父行锁顺序一致。新增组件MediaRetrieval(id)、VideoRetrieval(id,canManage)、ExtraCosts(isAdmin)由协调者挂现图片资料/运营页面，不新增隐藏媒体菜单。图片查询权限后缀image-query/search要映射knowledge.search，代码内亦复核。

## 最终候选扩展与第二轮交接

依据主协调要求继续补齐有限CSV/JSON字段映射、显式账期和音视频费用hooks：
- 列名映射到call_id/item/currency/amount/billed_at；外部requestID必须提供显式对照，不猜测UUID。重复列、过大/过多行/过长列名、非法币种和非有限金额拒绝。
- 可选账期最长31天，必须明确时区、模型范围及币种、每行带账单时间；该范围内平台存在但供应商未列出的项标provider_missing。未填写账期仍明确只对提交调用ID，不谎称整账期。导入scope与结果持久保存，原始账本不回算。
- OpenAI语音识别只读取官方usage.type=duration及seconds作为已上报计费秒数；本机ffprobe duration只作发送前预留估计。Token型或不报告秒数的回复不能推断秒费已知。
- 音频理解、TTS和百炼兼容视频协议无明确计费秒数时保持未知/冻结；TTS没有可执行输出时长上限，启用按秒金额预算时明确拒绝发送，不允许用户随意低填估计绕过预算。未启金额预算可保存估计预留并事后核对。
- 音视频和图片生成使用resource_operations(kind=media)的180秒执行租约，权限/模型配置与服务fence复核，旧执行结果不越过租约返回。原生时长probe共用075持久预留/相同512MiB总预算，probe确认停止才释放；失败未知由API内worker核对清理，不复发模型请求。
- 图片生成及帧向量既结算配置的call项又结算image项；unit严格独立。人工UNKNOWN记录不能利用普通usage POST升级为已知，必须带依据走单次reconcile；可信provider迟到可按原价格完成，已确认结果不能覆盖。

最终候选自验：35 passed（20.73秒），包含6媒体/费用、3音视频费用契约、2视频（1真实批准容器及1容量/取消/恢复逻辑）、旧money/vision/speech/multimodal；无真实外部请求。其后收紧人工UNKNOWN只能明确确认，定向6项再次全部通过（7.42秒）。Ruff、全前端TypeScript、这批3个组件ESLint均通过且零警告。真实Chrome交互尚未由本程序员执行，必须在代码审查通过后交测试，不用TS代替页面验收。

官方契约：[OpenAI转写usage Duration](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)、[百炼音视频用量字段](https://docs.bailian.console.aliyun.com/zh/model-studio/qwen3-livetranslate-flash-api)。百炼的video_tokens/audio_tokens不等于秒计费，配置video_second表示用户提供的商业计费契约；协议不报秒数仍未知，不能把本机时长或Token折算为实际供应商收费。

证据：.local/team-workspace/platform-expansion/development/media-all-final.log、media-manual-confirm-final.log、media-costs-final-types.log、media-costs-final-eslint.log。首轮审查问题R-MED-001～004已修改并自验，独立复审未返回通过；整个模块保持待审，不交QA、不部署生产，原六类生产数据/主密钥未修改。


## 2026-11-02 二轮代码审查修复待复审

当前阶段仍是开发自验完成、等待独立复审，未交QA，不将R-MED005～008自行标记关闭。

- R-MED005：settle仅先查不可变调用owner，取得与人工核对/provider一致的710032 owner事务锁后，再查hold/实际数量。相同数量重入幂等；不同数量（包括零单价）409，不覆盖先确认金额。补两个独立连接在锁前屏障竞争、同量/异量、可信provider迟到、人工核对与settle竞争。
- R-MED006：向量响应费用先真实保留；写入帧前在同一事务对账号与角色、模型、KB归属持共享行锁并复查启用/审核/改密/权限、模型revision和job取消及索引pin；持锁到帧提交，fence失败回收本次向量及文件。补返回前撤权、停用、修订、取消、删KB，均无残留帧；不将已发生的模型调用伪造退款。
- R-MED007：独立安全模型元数据GET /operations/extra-costs/models返回id/name/kind/enabled；管理员可选完整类型，普通账号账期范围只列本人调用过的模型，无密钥和地址。费用组件改用该端点，不改变聊天模型选择器。实际API媒体类型可达测试通过；页面交互留待复审通过后的QA。
- R-MED008：指定模型/币种/账期的平台集合依据model_usage.created_at调用发生时间，而非extra_cost_usage.created_at补录时间。界面及API说明口径，补跨月补录和+08时区左右边界（右开）回归。
- 维护与恢复：UNKNOWN清理和启动RUNNING→UNKNOWN在maintenance.slot共享GATE下运行，覆盖容器stop、向量/文件清理及状态写入；取得准入后复核service_fence，stop后及结束前再查fence。维护active时不动作，exclusive GATE阻塞清理，清理中的stop也阻塞exclusive GATE。claim/probe在准入后复核fence。
- 执行镜像：视频抽帧与duration probe的docker create均固定--pull=never；批准ID缺失直接失败，不隐式联网拉镜像。
- 路径：统一private_path检查data_dir及全部路径祖先无symlink/junction，resolve必须在指定data_dir内。directory供KB删除复用；视频源读写、decoder input/output、帧读写、帧清理、probe与UNKNOWN清理使用相同边界。Windows真实junction测试覆盖knowledge-videos、knowledge-images及data_dir祖先，外部哨兵文件不变。

准确开发自验：在services/api执行.venv/Scripts/python.exe ../../.local/team-workspace/platform-expansion/development/run-media-reviewfix.py；脚本每次创建随机owner_agent_media_reviewfix_<8hex>_test与独立TEST_DATA_DIR、PYTHONNOUSERSITE=1、RUN_LOCAL_SANDBOX=1。模块为test_media_review_regressions、test_expansion_media_costs、test_extra_media_fees、test_expansion_video_native、test_money、test_vision、test_speech、test_multimodal。最终50 passed in 25.93s、无跳过，日志media-reviewfix-tests.log，数据库/目录标识media-reviewfix-environment.json（无凭证）。早期测试脚本因连接URL格式未符合fixture约束导致13 passed/36 skipped，已纠正为实际50项执行；一次测试BlockingPortal关键字使用错误也已修正，不计为成功证据。

Ruff目标源与新回归通过；apps/web中npx tsc --noEmit通过；node --require ./scripts/use-typescript6.cjs ./node_modules/eslint/bin/eslint.js src/features/ask/extra-costs.tsx --max-warnings 0通过。直接npx eslint因仓库TS7与typescript-eslint兼容要求失败，已使用仓库既有TS6加载脚本运行，未变更依赖。

现有秒数缺失仍UNKNOWN并冻结预留，输入本地ffprobe仅作预算估算；无真实供应商模型/账单接入。未改生产数据、启停生产或提交推送。冻结21文件SHA256见.local/team-workspace/platform-expansion/development/media-source-manifest.json，待原独立审查者复审后再交QA。


## R-MED009修复待第四轮独立复审

第三轮独立审查已关闭005～008，但发现提交ID绕过声明账期范围，本轮仅改extra_cost_api.compare_bill和既有test_media_review_regressions回归文件。

完整账期先查询本人已知提交调用（含尚无额外费用记录的调用），以model_usage的实际model_id/created_at校验声明模型及半开时间窗；已知提交计费项币种也须符合声明币种，否则预览和导入均422 BILL_SCOPE_MISMATCH。账期的账本集合只取声明范围，不再将提交ID直接OR进来。账单billed_at为供应商输入，不能替代真实调用时间。未知及不属于本人的ID均保持platform_missing，不通过范围错误泄漏其他账号调用是否存在；不带账期模式保留显式ID匹配及币种差异分类。

新增10项参数回归：起点、终点前一微秒、错模型、起点前一微秒、终点、错币种、其他账号、未知ID、无费用的范围外已知调用、无费用的范围内已知调用。UTC账期与+08账单时间相互对照；范围外预览/导入均拒绝，显式ID对照保持原行为。首次5项失败为测试误读error.code路径，已修正后重跑，不能视作通过证据。

同run-media-reviewfix.py命令在新随机测试库/独立目录执行8模块：最终60 passed in 41.78s，无跳过，日志media-reviewfix-tests.log及无凭证环境标识media-reviewfix-environment.json。Ruff目标文件和git diff --check通过。此次未改UI、迁移、根集成入口或其他已冻结源；前轮TS/ESLint证据不作为本轮新的UI测试。21源manifest已重新计算，等待原审查者第四轮复审，尚未交QA；生产数据与外部服务未改动。
