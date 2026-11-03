# 媒体与额外费用独立复审

2026-11-02。作者expansion_media_costs，独立审查expansion_review_tools。范围以`.local/team-workspace/platform-expansion/development/media-source-manifest.json`的20文件为基线，必要回看维护准入、模型安全元数据和权限调用链。原R-MED-001～004见`platform-expansion-code-review.md`的主协调首审，本报告复核其修复，同时记录新发现。仅新增本报告，未修改业务源码、启停生产或调用外部模型/账单服务。本轮正式报告已冻结供作者修复。

**当前结论：第四轮复审通过，可交QA。** 原001～009均已修复并经独立复核关闭；以下历史问题与证据保留，最新关闭理由见文末。通过范围为媒体/费用模块，不代替主协调集成审查、实际页面或外部服务验收。

## 原R-MED-001～004修复核验

- **001单位/币种及旧价格选择：原问题已修复。** add_price按model+item事务锁检查单位与币种不可变；reserve先取全部item最新生效版本再筛unit，避免退回旧单位版本。原价格不覆盖。
- **002按单位结算：原混淆已修复。** settle显式接收unit，通用Token结算只call，图片/帧另结算image；无上报秒数的音视频保持未知，未把秒数按一次调用结算。其并发单次约束仍有R-MED-005。
- **003UNKNOWN迟到与人工核对：原顺序路径已修复。** record_charge先按owner锁定，可信provider未知转已知一次；manual不能从普通usage请求升级UNKNOWN，显式reconcile记录依据/身份/时间且条件更新一次，原价格不变。R-MED-005尚可能通过另一个settle入口破坏已确认值，故不能据此宣布全链路幂等通过。
- **004价格/账单分页：原可达性已修复。** 价格和账单为created_at+id键集续页，组件有中文加载更多，usage明确标“最近500条”。新媒体价格模型下拉另见R-MED-007。

## R-MED-005 / P1：先读后锁使并发结算覆盖已完成金额

- 位置：`services/api/app/extra_cost_budget.py:60`～`:75`。
- settle先SELECT全部holds及actual，再取得owner事务锁；等待锁后继续使用旧row。若两次都读到actual=NULL，先行结算提交后，后行仍无条件UPDATE actual和usage，覆盖第一笔。当前`if row["actual"] is not None`只保护串行进入后的读取，不能保护等待期间变更。可与不同quantity结算或人工确认竞争。
- 独立真实DB复现：预留audio_second=10，使用两个独立连接分别settle quantity=1/2；临时执行包装器仅在取得owner锁前同步两个连接，确保它们均已完成原SELECT。**两次均成功，最终amount=2.000000000**；预期一个成功、另一个冲突，原金额不被改写。未更改业务源码。
- 建议：取得owner锁后重新读取hold/actual（需要稳定owner来源和单位过滤），或条件单次UPDATE并在失败时读取已完成值判断幂等/409。使用同一顺序与record_charge/reconcile协调，补不同quantity并发、相同quantity幂等和人工确认并发回归。

## R-MED-006 / P1：视频帧返回后未重验权限与模型修订

- 位置：`services/api/app/media_retrieval_video.py:246`～`:317`，尤其vectorize前`:256`～`:258`与响应后的`:274`起。
- 请求前检查knowledge.manage和model revision，响应后仅检查service fence、job取消及index的model_id/name/base_url/dimensions。模型停用或同地址修订不改变这些index字段；账号撤权也不会由job状态自动体现，所以旧请求仍写入帧文件、Qdrant和数据库。开发文档“请求前后检查权限/模型revision”与当前实现不符。
- 独立真实DB复现：创建RUNNING视频job、真实私有图片索引；vectorize内存替身在返回向量前把owner降为viewer、禁用模型并revision+1。**index_frame仍成功，knowledge_video_frames新增1行**。没有真实外部请求。
- 建议：响应后及写索引提交前复核账号授权、owner/job和模型启用/修订，并与job/模型变更建立一致的提交边界；失效时不写任何帧，已发生供应商调用仍真实保留费用/未知状态，不假退款。补暂停响应→撤权、停用/修订模型、取消和知识库删除的独立竞争回归。

## R-MED-007 / P2：价格与账期模型下拉只有聊天模型

- 位置：`apps/web/src/features/ask/extra-costs.tsx:28`～`:31`及模型select；调用方`services/api/app/main.py:507`。
- ExtraCosts仅取GET /models的items，此现有字段明确过滤supports_chat。图片/音视频/多模态向量模型不在items，导致新增媒体计费版本及指定媒体账期的模型范围无法选择。本机API自验直接提交model_id，绕过了真实操作入口。
- 独立实际API验证：新建multimodal_embedding/dashscope_multimodal模型后，`/models.items`中该ID为False；组件没有其他模型来源。图片理解虽另有vision_models字段，组件也没有使用，音视频仍缺失。
- 建议：提供费用页面完整且受权限限制的模型安全元数据；管理员新增价格可使用完整管理列表的安全字段，普通账号账期选择应按实际可见/发生调用的模型，不能返回密钥。不要为了费用下拉把所有用途塞进聊天模型选择器。补实际页面新增image/audio_second价格及媒体账期选择测试。

## R-MED-008 / P2：迟到费用按补录时间移出原调用账期

- 位置：`services/api/app/extra_cost_api.py:212`～`:232`，period筛选使用extra_cost_usage.created_at。
- model_usage.created_at是调用发生时间，extra_cost_usage.created_at是费用记录插入时间。迟到用量或人工补录在下个月插入时，原调用期的完整账期比较不会把它计入provider_missing，反而属于补录期；界面目前只称“账期”，未说明是入账期，开发记录却声称覆盖迟到/缺失对账。
- 独立实际API复现：model_usage调用发生于20天前，今天record_charge插入已知费用；提交覆盖原调用日期的模型/币种账期（供应商不含该调用）→**该平台调用没有出现在provider_missing**，只返回提交的其他调用platform_missing。未改历史生产账本。
- 主协调已确认本功能采用调用发生期。建议按model_usage.created_at或不可变的服务发生时间取平台集合，不按补录时间，并在API/界面说明该口径；入账记录时间只作审计，不能隐式改变原调用账期。补跨月迟到、边界时区、模型/币种范围与缺失检测回归。

## 独立验证与尚未验收边界

第一套全新随机`owner_agent_review_media_<8hex>_test`数据库及独立data目录执行7个模块：test_expansion_media_costs、test_extra_media_fees、test_expansion_video_native、test_money、test_vision、test_speech、test_multimodal，PYTHONNOUSERSITE=1、RUN_LOCAL_SANDBOX=1，**35 passed in 27.73s，无跳过**。含批准本地媒体镜像的无网络实际抽帧/ffprobe、私有向量索引和删除；追加R-MED-005/006独立探针均复现失败。第二套全新`owner_agent_review_media_period_<8hex>_test`及独立data目录追加R-MED-007/008实际API证据。两次均精确删除本次创建的测试数据库，未复用作者库、未读取用户密钥或真实业务正文。

- 视频claim/probe与现有Coding/Browser共享710016容量锁和512MiB持久预留，slot维护准入存在。UNKNOWN保留预留，核对label/image身份后清理，不自动重放原模型请求；取消前不触发decoder/模型的回归通过。
- 容器使用批准imageID和固定资源/网络/文件挂载参数；stop按job标签和镜像核对后操作，不按用户任意名字清理。尚未实际演练运行中取消、daemon不响应、创建超时、进程后代或镜像被清除等全部负向情况，不把基础原生测试当这些场景全过。固定`--pull=never`可进一步落实不隐式下载，镜像缺失应明确失败；此为待核验加固建议，未作为已复现新Bug。
- 根协调负责视频router/serve生命周期、maintenance idle新增视频观测、knowledge.search后缀权限和知识库删除前活动job保护，不把尚未接线当模块作者Bug；这些hook须在最后集成审查/QA验证。活动job或holds_memory不得由CASCADE删除丢失容器归属，清理确认前不能释放容量。
- 真实供应商价格、秒计费契约、模型效果、真实账单仍未接入。独立页面操作、中文只读/错误状态和最终生产构建未在本代码复审中完成，必须在修复后代码复审通过才交QA。

## 第三轮：005～008已关闭，新增009阻塞

本审查者再次使用全新随机`owner_agent_review_media_fix_<8hex>_test`及独立data目录，运行旧7模块+test_media_review_regressions共8模块：**50 passed in 38.40s，无跳过**。含真实批准本地镜像无网络抽帧/时长检测、真实Windows目录联接哨兵测试和实际PostgreSQL并发锁。测试数据库已精确删除，未使用作者测试库。未修改业务源码。

- **005关闭**：settle先以稳定call owner取得共享owner事务锁，再读mutable hold/usage；同时比较amount和quantity，零价不同quantity亦409。独立重跑不同/相同结算、provider/manual竞争和零价冲突，金额不能覆盖。
- **006关闭**：响应费用保留后，落库事务锁users/role、model、知识库owner、job和index，重验账号权限、模型enabled/revision和取消/归属，持至帧写入提交；撤权、停用、修订、取消、删库五分支均无帧残留，已发生调用记录保留。
- **007关闭**：费用页面改用专属/operations/extra-costs/models；返回仅id/name/kind/enabled，管理员可选所有用途，普通账号仅本人实际调用过的模型，未向聊天选择器混入媒体用途。接口字段及调用组件已核对；实际Chrome操作仍交QA。
- **008关闭**：平台完整账期集合改用model_usage.created_at，界面/API明确调用发生期；独立重跑跨月迟到和+08:00起止边界，起点包含、终点排除。
- 维护/路径加固：recover在startup、UNKNOWN容器清理/文件删除/取消状态写入整个过程持共享维护GATE并检查service fence，独立阻塞/释放测试通过。decoder/probe create固定--pull=never。private_path拒绝data_dir及祖先联接、叶路径联接和resolve越界；三个实际Windowsjunction测试均保持外部哨兵不变。此证据不代替最终主协调main/KB删除/维护集成和生产负向演练。

### R-MED-009 / P2：提交ID的OR分支绕过完整账期模型和时间范围

- 位置：extra_cost_api.compare_bill，SQL的`u.call_id=ANY(...) OR (账期/模型/币种条件)`及随后known匹配。
- 即使指定完整账期，提交行的call_id仍不受模型/调用发生时间限制，且查询结果不带这两项用于后续范围校验。账单行billed_at只是用户输入，不能证明真实调用属于声明账期；错误模型或错期调用仍会显示match，API/UI却称仅指定模型/调用期核对。
- 独立第三套新随机`owner_agent_review_bill_scope_<8hex>_test`实际API复现：声明model_ids=['test']、最近1天账期；提交同owner另一多模态模型20天前call_id，账单行billed_at填本期，金额一致。**返回200、classification=match**。该调用同时违反声明模型与调用发生期，应拒绝或明确scope mismatch，不能视为范围内一致。临时库已删除，无外部账单/模型调用。
- 修复：完整账期模式要求所有已知提交调用符合owner、model_ids、currency和model_usage.created_at半开时间窗；不符合时422或明确范围外结果，不能fallback成match。无账期模式保留显式ID匹配的既有契约。补模型范围外、时间范围外、时区边界以及无账期合法ID对照。供应商未知ID可保留platform_missing，但不能把已知范围外调用当作本范围一致。

第三轮报告已冻结，作者可开始修复009；005～008无需重复开发或倒改历史证据。

## 第四轮：009关闭，可交QA

仅extra_cost_api.py和test_media_review_regressions.py增量复审。完整账期先按owner查询已知提交call，验证实际model_id、created_at半开区间和已知提交item币种，范围外422 BILL_SCOPE_MISMATCH；即使尚无fee记录也校验已知调用。主集合仅在ID-only模式采用提交ID分支，完整账期使用实际时间/模型/币种范围，原OR绕过已去除。未知ID与他人ID均保持platform_missing，不借scope错误泄漏他人调用存在；ID-only语义保留。

本审查者在新随机`owner_agent_review_bill_fix_<8hex>_test`和独立data目录重跑变更测试文件全部用例：**25 passed in18.43s，无跳过**，包含10个009模型/时间/时区/币种/无fee/未知/他人分支及此前005～008、GATE、路径回归。测试库精确删除。结合第三轮独立50项证据，未发现本次两文件修改引入新的阻塞问题，009关闭。真实页面、main/维护/KB删除集成与供应商联调仍按既述边界交后续角色验收。
