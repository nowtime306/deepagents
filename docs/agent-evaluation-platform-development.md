# Agent评测平台化开发契约（B/C/D）

日期：2026-11-04。依据AE-18～AE-24及原接入方案。本文件是待实现接口与实施设计，contract_version=1；不代表B/C/D验收或部署。原A20=Ask5/Work8/Workflow7、B100=25/40/35、效果评测每任务三次独立重复不变。工程替身、真实任务、真实模型效果、中文UI、部署分别记状态。原方案和用户deepeval-learning资料不改。

## 前后端冻结契约 v1

统一前缀`/api/v1/agent-evaluations`。UUID、ISO8601 UTC时间和sha256小写十六进制；创建dataset必须revision=0；PUT草稿和发布使用当前revision。所有body禁止owner_id、worker lease、命令、宿主路径、原始服务token字段。列表响应`{items: [...], next_cursor: string|null}`，`limit`默认20、上限100，`cursor`为不透明分页值。所有详情先以服务器当前用户限定owner查询；管理员也不跨owner。错误沿用`{error:{code,message}}`中文安全消息。403=权限、私有ID404、结构非法422、CAS/幂等冲突409、未准入503。现有知识评测及A隔离environment/observations接口保持独立。

`GET /capabilities`（evaluation.read）返回：
```json
{"contract_version":1,"permissions":{"read":true,"manage":false,"run":false},"admission":{"status":"BLOCKED","stage_a_accepted":false,"runner_ready":false,"blockers":[{"code":"EVALUATION_STAGE_A_NOT_ACCEPTED","message":"A20独立验收尚未闭合，暂不接收平台批次。"}]},"thresholds":{"a20":{"total":20,"ask":5,"work":8,"workflow":7},"b100":{"total":100,"ask":25,"work":40,"workflow":35},"effect_repeats":3},"default_budgets":{"max_seconds":300,"max_model_calls":20,"max_tokens":20000,"max_sandboxes":2},"schema_ready":false,"supported_execution_modes":["engineering"],"max_dataset_cases":100}
```
stage_a_accepted必须来自服务器可信、可核源身份和完整性、未撤销的验收证据；不能从文档数字、客户端声明或“已有若干通过”推断。runner_ready要求已注册的固定执行驱动、依赖/源码摘要和租约/取消/证据回收能力通过预检；生产默认两者false。API存在或页面能打开不改变准入。默认无真实付费模型、Confident上传或自动评测。

### 评测集

- `GET /datasets`：evaluation.read。每项为`DatasetSummary`。
- `POST /datasets/preview`：evaluation.manage，body同下，纯验证、不保存、不执行。返回`{valid:true,normalized_definition,definition_digest,coverage,duplicate_groups,warnings,schema_valid,execution_readiness}`；非法422。exact duplicate可检测；语义去重尚需人工，不能自动认定100个独立任务。
- `POST /datasets`：evaluation.manage，201。body `{name:string,description:string="",definition:DatasetDefinition,revision:0}`，返回完整`DatasetDetail`。
- `GET /datasets/{id}`：evaluation.read，返回`DatasetDetail`。
- `PUT /datasets/{id}`：evaluation.manage，body `{revision:int,name,description,definition}`，CAS成功返回`DatasetDetail`，不覆盖任何旧发布版本。
- `POST /datasets/{id}/versions`：evaluation.manage，201。body `{revision:int,notes:string=""}`，锁定当前草稿生成不可变`DatasetVersion`；相同内容可返回原版本并标`created:false`，不能悄悄另改版本。旧草稿revision已变409。当前无已准入driver时仍可发布不可执行的不可变定义，响应明确`executable:false`和BLOCKED；不能据发布成功标可执行。
- `GET /datasets/{id}/versions/{version}`：evaluation.read，返回不可变版本及完整definition。

`DatasetDefinition`与现A执行器声明格式一致：`{schema_version:1,dataset_id:string,dataset_version:int,cases:Case[]}`。每case包含case_id(AET-[AWF]两位数)、name、mode(ask/work/workflow)、requirements、tags、input、fixture_profile{id,version}、fixture_parameters、validators[{registered_id,version,parameters,required}]、metrics、budgets{timeout_seconds,max_model_calls,max_tokens}、repeats、approval_policy、dependencies；默认值必须经同版本声明校验后规范化。只接受已登记validator/metric规则及参数schema；草稿可结构化保存尚未登记的profile数据，preview的schema_valid仅指声明结构合法，不表示固定profile参数已核验。profile类型、版本及全部参数必须另经可信driver逐selectedcase匹配固定catalog原definition和describe implemented；未验证者明确列missing_profiles并BLOCKED。参数是数据，不成为宿主可执行命令。case ID不重复且与mode一致，输入/JSON总长有界，非有限数值拒绝。

`DatasetSummary`字段：`id,name,description,revision,current_version:int|null,case_count,mode_counts:{ask,work,workflow},created_at,updated_at`。
`DatasetDetail`：Summary再加`definition,definition_digest,versions:DatasetVersion[],coverage,execution_readiness,budget_quote:{engineering,effect}`。
`DatasetVersion`：`id,dataset_id,version,definition_digest,case_count,mode_counts,notes,created_at,created_by,created:bool`；详情额外definition。`id`是发布版本UUID；`dataset_id`是平台评测集UUID，definition.dataset_id只是声明标识，不能作为owner或另一个私有资源ID使用。
`coverage`：`{unique_case_ids,mode_counts,a20_count_complete,b100_count_complete,semantic_review_status:"PENDING"|"CONFIRMED",acceptance_complete:false}`。计数达到要求不等于独立任务语义审核/执行验收通过。`execution_readiness`：`{status:"BLOCKED"|"READY",parameters_schema_verified:bool,missing_profiles:string[],reasons:string[]}`，不得把仅登记名字的profile标实现。

### 批次与样本

`POST /runs`（evaluation.run，成功202）冻结body（按dataset_id+version在owner内解析发布版本）：
```json
{"request_id":"UUID","dataset_id":"UUID","version":1,"execution_mode":"engineering","repeats":1,"acceptance_target":"engineering","version_set_id":null,"budgets":{"max_seconds":300,"max_model_calls":20,"max_tokens":20000,"max_sandboxes":2}}
```
`execution_mode`=engineering|effect；engineering必须显式repeats=1，effect必须3；effect默认未开放，有独立授权/模型/费用确认合同后才开放。`acceptance_target`=engineering|b100，缺省engineering；b100另强制100独立任务25/40/35、已确认语义去重与规定重复，不以工程小批次声称B100。`execution_mode`和`acceptance_target`缺省engineering；`version_set_id`可null表示服务器已注册默认版本集，不能将任意客户端commit/URL/路径当可信版本集。

缺省预算为300秒/20调用/20000 tokens/2沙箱保守预约；`budget_quote`由每case原预算×批次重复数聚合，沙箱每样本保守2，不是实际用量。显式四项预算均不得低于聚合，且服务器硬上限为86400秒/10000调用/100000000 tokens/600预约；不足422，不静默放大。

先查当前evaluation.run及各实际模式/资源业务权限，再查可信A门禁与runner；不足503且零队列/批次/样本写入：`EVALUATION_STAGE_A_NOT_ACCEPTED`、`EVALUATION_RUNNER_UNAVAILABLE`、`EVALUATION_VERSION_SET_UNAVAILABLE`或`EVALUATION_EFFECT_MODE_DISABLED`。重复同owner/request_id且同规范payload返回同RunDetail和202；不同payload409 `EVALUATION_REQUEST_CONFLICT`。202只表示持久排队。

- `GET /runs`（read）返回RunSummary列表，可`status`/`dataset_id`过滤。
- `GET /runs/{id}`（read）返回RunDetail。
- `POST /runs/{id}/cancel`（run）body `{revision:int}`，返回RunDetail。排队可直接CANCELLED；已开始设CANCEL_REQUESTED并合作取消，不能伪造终态/退款。终态重复取消返回原记录，不新发业务取消。
- `GET /runs/{id}/samples`（read）返回SampleSummary列表。
- `GET /runs/{id}/samples/{sample_id}`（read）返回SampleDetail，必须同时匹配本run/owner。
- `GET /runs/{id}/report?format=json|junit|markdown`（read）返回授权附件；未有有效封存报告409 `EVALUATION_REPORT_NOT_READY`。安全文件名、固定媒体类型；不接受任意file/path。有效NEEDS_REVIEW报告仍保留不确定项，不包装PASS。

`RunSummary`字段：`id,dataset_id,version,dataset_version_id,execution_mode,repeats,acceptance_target,status,gate_verdict,revision,unique_task_count,total_samples,completed_samples,counts:{pass,fail,needs_review,not_run},created_at,started_at:null|time,finished_at:null|time`。
`RunDetail`：Summary加`budgets,version_set:null|object,admission_snapshot,cleanup_status,report:{available:bool,formats:string[],sha256:null|string},cancel_requested:bool,errors:[{code,message}],scope,source_identity`。status为QUEUED/RUNNING/CANCEL_REQUESTED/COMPLETED/CANCELLED/FAILED/NEEDS_REVIEW；gate_verdict为PENDING/PASS/FAIL/NEEDS_REVIEW，不能由COMPLETED推断PASS。cleanup_status为NOT_STARTED/CLEAN/RETAINED/NEEDS_REVIEW。
`SampleSummary`：`id,run_id,case_id,case_name,mode,repeat_index,status,task_outcome,metric_status,gate_verdict,cleanup_status,revision:int,root_refs:[],created_at,started_at,finished_at`。样本status为PENDING/RUNNING/COMPLETED/CANCELLED/BLOCKED/NEEDS_REVIEW；task_outcome为NOT_RUN/PASS/FAIL/NEEDS_REVIEW；metric_status为NOT_RUN/NOT_REQUIRED/COMPLETE/ERROR/NOT_SCORED，缺记录不推断通过。
`SampleDetail`：Summary加`facts:[],metrics:[],evidence_refs:[],usage:{calls:null|int,input_tokens:null|int,output_tokens:null|int,completeness:"NOT_RUN"|"KNOWN"|"UNKNOWN"},timings:{},errors:[],scope,configuration_refs:[],review:null|object`。尚未执行的结果、用量、耗时不填0或伪结果。事实FAIL不可被高语义分覆写，初始化/子场景/重复不增加unique_task_count。

### 比较、复核和失败候选

- `GET /comparisons`（read），`POST /comparisons`（manage，201），`GET /comparisons/{id}`（read）。创建body `{baseline_run_id:UUID,candidate_run_id:UUID,changed_axis:"auto"|"model"|"business_version"|"platform"="auto",notes:string=""}`。均须本owner、两有效完整报告，否则409。返回`{id,baseline_run_id,candidate_run_id,changed_axis,comparable:bool,status:"COMPARABLE"|"INCOMPARABLE",reasons:[],summary:null|object,case_changes:[],created_at}`。不具可比性时summary=null，不能输出可信提升。
- `GET /reviews`（read，可status过滤）；`POST /runs/{run_id}/samples/{sample_id}/reviews`（manage，201），body `{revision:int,decision:"CONFIRMED"|"DISPUTED"|"NEEDS_MORE_EVIDENCE",reason:string,rules_version:string}`。append-only，返回`{id,sample_id,reviewer_id,decision,reason,rules_version,original_gate,created_at}`；不改原facts/metrics/gate或关键权限失败。
- `GET /failure-candidates`（read），`POST /failure-candidates/preview`（manage，纯预览），`POST /failure-candidates`（manage，201），`GET /failure-candidates/{id}`（read）。body `{name:string,source_sample_id:UUID|null,authorization_note:string,material:{input:string,expected_behavior:string},redaction_confirmed:bool}`；仅本人已有评测样本或显式提交的已授权脱敏材料，不拉取任意生产run。preview返回`{redacted_material,redaction_summary:{removed_fields:string[],warnings:string[]},material_digest}`；create要求授权说明及人工脱敏确认，原输入不入库，仅存脱敏后材料。候选返回`{id,name,status:"PENDING_REVIEW"|"APPROVED"|"REJECTED",revision,source_sample_id,material_digest,redacted_material,authorization_note,created_at,review:null|object}`。批准/拒绝另`POST /failure-candidates/{id}/review` body `{revision:int,decision:"APPROVED"|"REJECTED",reason:string}`，manage、追加审计。批准也不直接执行；另人工转dataset草稿、登记断言并发布版本。

### 版本集（C所需，B先冻结快照）

`GET /version-sets`（read）；`POST /version-sets`（manage，201）body `{name:string,candidate_ref:UUID,business_refs:[{kind:"agent"|"workflow",resource_id:UUID,version:int}],judge_ref:UUID|null}`；`GET /version-sets/{id}`（read）。candidate_ref来自服务器只读登记目录`GET /candidates`；没有可信登记时列表为空并给明确capability blocker，不接受自由command、URL、DSN或凭据。详情保存服务器核实的platform commit/source集合摘要/迁移/镜像/runner依赖锁/rules摘要和业务发布号，模型配置身份按实际resource snapshot补证。首期仅可信已安装候选，不实现任意Git checkout/build或用户命令。

## 持久队列与执行边界（B）

新085迁移预留agent_evaluation_*表：datasets/dataset_versions/version_sets/runs/samples/comparisons/reviews/failure_candidates及必要append-only事件/证据元信息。schema命名与旧knowledge_datasets/experiments完全独立。所有父子表带owner_id并以owner+id关联，源版本不可变；request_id唯一(owner,request_id)及payload_digest。发布草稿CAS与版本分配同事务，run与样本计划同事务落库。

正式worker使用PostgreSQL FOR UPDATE SKIP LOCKED领取到期QUEUED批次，持久lease UUID/deadline/heartbeat；默认一个目标/Work并发1，父批次时长/调用/Token/沙箱容量均硬约束。领取后先再次查当前owner角色/执行权限、版本集/部署门禁、取消/维护状态；撤权使后续新效果停止，不能仅首次HTTP通过后长期沿用权限。尚未派发且确认无效果的过期租约可重领；有SUBMITTING/未知效果时NEEDS_REVIEW保留，不能当普通重试任务换根。

复用services/evaluation独立venv和已审固定coordinator。启动命令只来自服务端固定allowlist，shell=false、绝对已核runner路径、源码/锁摘要匹配；用户definition永不变成host command、可执行文件名或路径。输入/输出目录由本服务以batch/sample UUID在专用根派生，使用受控资源handle；数据库不存目标密码/token/原始服务凭据或任意宿主路径。凭据通过专用vault/ACL临时文件或进程内env传给唯一已租约子进程，模型/Confident登录不继承。namespace/new_test DB/newowner每case/repeat不同，未知资源占用容量不退款；保持精确ID回收、无FORCE或全局prune。

worker只有实际执行器生成的facts、原生usage、各CLI审计、report-ready及三摘要经过当前identity验证后，才可写相应sample/result和batch终态。至少一个假报告/缺facts/缺root/缺audit/错误scope/空结果都不能COMPLETED+PASS。空壳worker只领租约/写日志不算实现；不存在真实驱动则admission保持BLOCKED，不能排一批假任务立即完成。取消合作请求子进程cancel.request、正式cancel已知root、确认终态再清理；不强杀后宣称已清理。

B进程权限与目标权限两层都须有效：执行器服务凭据限该batch/隔离环境，不能成为全平台管理员代理；每次新provider/tool效果仍由真实目标检查当前业务授权与父租约。父服务撤权/租约失效到隔离目标的边界传递、后台service开关和关闭中的证据保留，须独立审查/故障注入后才允许runner_ready=true。默认部署不开新worker，也不修改已有用户角色。

## 可比性与CI（C）

版本集和batch运行快照分别保存“请求的发布/候选”和“实际使用的模型/业务版本/源码/迁移/镜像/规则/资料摘要”。比较必须有共同任务定义、相同独立重复条件/固定输入和有效报告，只有声明changed_axis对应维度可变化；其他漂移列不可比较。报告保留逐任务首次repeat1、三次分布、三次全成功、失败/超时、未知用量和不可计算费用；不能挑三次最好，也不能用相同dataset名称掩盖定义变化。

CI继续消费独立执行器JSON/JUnit及ready校验的原始PASS/FAIL/NEEDS_REVIEW，关键事实失败优先；故障注入文件缺失/重复写/错分支稳定拦截。judge错误、必测skip和证据缺失不成功退出。比较API不自动merge/部署；90/80/95%只是待校准目标，100任务不能推99.9%可用率或统计显著。

## 人工复核、脱敏与留存（D）

初期事实/语义冲突全部进人工队列，其余按规则版本抽样。review追加可信身份、时间、理由和原结果，不覆写原文件。失败候选只从明确授权来源的最小材料产生，先去除credentials/headers/query secrets/个人标识/非必要工具日志；不把生产原始prompt整包复制或用正则脱敏后宣称绝对无密。机器初筛+人工确认+独立case语义去重，形成固定断言后发布新dataset版本，再在隔离目标运行。候选引用只保存本owner允许的sampleID/摘要；本首期不提供任意生产run拉取或隐式上传云平台。

设置保留期、访问日志与待删状态；被有效报告/复核/审计引用的材料不能静默物理删除，先撤销准入和引用访问，按正式保留策略清理。删除不改原任务失败、费用或覆盖分母。

## 最小实施范围与准入

后端限app/agent_evaluation_platform*.py、新085 SQL、新独立tests；root集成main与permissions分流，run/cancel用evaluation.run，dataset/review用manage，GET/export用read。前端能力中心Agent评测四列表（评测集/批次/对比/失败候选）及新增对话框、样本/报告详情；页面先依真实CRUD和admission显示BLOCKED，不造成功样本填页面。

先冻结API types及数据库/队列模型，完成真实隔离PG CRUD/CAS/owner/权限/幂等/取消和报告路径/脱敏测试；再接受控真实子进程、权限撤回/租约丢失/未知保留、完整原证据导入，最后中文UI实测、B100三次独立重复和发布门禁。代码可并行，但A20未独立闭合前POST runs持续503。当前版本不承诺持久worker、真实评测或平台UI已完成。

## 第一批实现冻结范围

第一批包含数据集草稿CAS、不可变版本、capabilities、批次和样本读取/取消/复核、固定报告下载守卫及持久租约原语。当前未注册真实执行驱动，未启动worker，也未开放可信报告写入，运行入口默认503且不创建空批次。比较/失败候选/版本集API按上述合同在后续小批实现。作者协议与ASGI替身测试不等于真实PostgreSQL、真实执行或B阶段验收。

## 本轮审查修订：真实比较身份与控制验收

比较manifest的comparison字段必须通过强类型校验：dataset_digest/rules_digest/material_digest均64hex；model_identity={complete:true,models:[{model_id:有界非空TEXT,effect_fingerprint:64hex,kind,protocol}]}，明确完整观察的0provider可models=[]；business_identity={complete:true,resources:[{kind:agent|workflow,resource_id:UUID,version:正整数,definition_digest:64hex}]}至少1；platform_identity={complete:true,platform_commit:40hex,source_digest:64hex,runner_lock_digest:64hex}。这些必须来自受信导入器核对实际证据，不能从前端或请求意图生成。任一null/空身份或格式无效409；engineering仅repeat1，effect要求每个case精确独立repeat1/2/3，缺失、重复或混用不比较。NOT_RUN/NEEDS_REVIEW不得统计为可信提升，FAIL原样参与。

A阶段可信发布收据升级schema_version=2、kind=agent-evaluation-stage-a-publication-v2；top acceptance_verdict=PASS、independent_review=CLOSED与原始任务状态分开。20个唯一case行保存acceptance_verdict、actual_task_outcome、actual_gate、expected_gate、actual_cleanup、expected_cleanup、definition_digest、evidence_digest、evidence_complete、cleanup_verified。预期来自已审固定case定义；verify_evidence必须重核当次原始bytes和来源，不能只信receipt expected字段。W01原task/gate FAIL保持FAIL，若原始控制预期亦FAIL且独立证据正确，控制验收可PASS；不能改原报告PASS，也不能要求全部原任务业务成功才接受负控制。缺实际证据/预期不符/来源变化均阻断；默认无可信收据仍BLOCKED。该协议自验不代表A20已完成。
