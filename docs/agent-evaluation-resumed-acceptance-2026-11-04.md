# 评测恢复验收：2026-11-04

分支 `onwer_agent`。记录始于候选732b1672及其350项清单；后续业务固定1358d026及351项清单，2026-11-05已受限发布评测管理版本。各阶段原清单和失败不覆盖；实际执行仍不全量开放。

## 环境修复

3100/3131前端、8101稳定后台/8100测试后台及独立Ask现已恢复，18180管理服务沿原解释器、配置、密钥和SQLite启动。没有停止或调整其他18个Docker容器，没有迁移生产schema79。

管理服务恢复材料在 `.local/manager-restore-20261104-2228/`。本线程浏览器工具重试3131仍明确返回保存权限或管理策略拒绝；用户提供的另一轮浏览器成功记录另保留，不能替代本线程新增页面验收。本项依最新用户指令记为待用户回验，不再切换浏览器、地址或其他通道绕过。

## 评测管理接口：实际HTTP通过

只在 `owner_agent_rdc_browser_20261103_test` 安装085迁移；新增两个非管理员专用QA账号，权限仅评测读/管理/运行声明及必要只读权限，没有业务模型执行权限。原账号、角色和原11表原行摘要均保持。

实际8100 HTTP复测33项符合预期：身份检查、能力/列表/预览、创建/编辑、CAS冲突、发布版本不可变、跨账号隔离、分页、人工脱敏确认、候选审核及实时撤权。评测执行仍BLOCKED，POST运行按最小业务权限返回403，执行队列/样本/模型用量及Ask、Work、Workflow运行均零；没有声称本环境已测试503或真实评测任务。

收据 `.local/team-workspace/agent-evaluation/qa-metadata-extra/http-retest-270ea2492ce644569bd560ac172a93bd/receipt.json`，SHA256 `f7fccba80fe200cb660e5c48bd12efa0ea9d895bc9247fbf14e3096a21f38af2`。专用账号凭据只留私有文件，不写入文档。首轮测试误用发布前修订号导致409，原失败保留；改为正式GET读取实际修订后，以新样本复测，没有修改产品断言或重复迁移。

## W05：本次真实执行发现缺陷，不能记通过

用户明确批准的新批仅一次W05：300秒、10次本机受控模型调用、20000 Token、最多2个沙箱，不调用付费模型，不启动W06，不退旧两批预留。

首次v2在3100健康检查处拒绝，尚无消费标记/输出/新任务；之后v3复查出现一次旧PID状态断言失败，原因未能追溯，原件保留。后续精确只读检查确认旧目标进程缺席、旧命名空间库存零，容量恢复至约6.27GB（已扣原1GiB余量）；再次启动前采样约6.31GB。本批沿同一授权和唯一输出、共享一次性消费标记启动，未分配额外批次。

实际suite为 `.local/agent-evaluations/w05-732b1672-16d572d746f4475698fcce8401c0d69e`，root为 `56d5815c-030f-4056-8428-86b275b1cdd6`。发生真实成员文件读取、三次调用及成员超时：两次KNOWN调用各10输入/5输出，第三次UNKNOWN且Token为null；父NEEDS_REVIEW、成员UNKNOWN/TEAM_RESULT_UNKNOWN保留。

独立复算发现唯一固定事实校验不符：父任务 `usage_unknown=false`，原生账本却有一条UNKNOWN。仅在私有内存副本把该标志改true，原评估函数才通过；这是定位反例，绝非实际修复后的通过。原正式fact、report gate和退出码仍FAIL/1，READY及四次CLI审计完整，不重写报告。`TARGET_RETAINED_CAPACITY_FULL`是套件保留分支文字，本例实际已执行三次调用，不能据该文字推断容量失败。

缺陷单 `.local/team-workspace/agent-evaluation/qa-work-extra/w05_actual_review_372682624cc147fa843c090570e12ea8/defect.md`，SHA256 `755f9ef971a76354ae9e06df2173e550f5b5493aabc3a3b25e120d54b8d2af89`。独立复算收据SHA256 `0f363bbed301de3903467b69b79268fa41a953df3ac9396a1c38446a0a2ce18e`。

运维独立确认目标进程退出、DB会话/Worker/租约均零、沙箱原记录RELEASED且该命名空间管理库存/Docker集合零。原数据库和UNKNOWN账本保留，不能改成CLEAN或把null补零。收据 `.local/w05-732b-independent-retention-ops/receipt.json`，SHA256 `cfb466d4ff9e28f45231c3a3832bc8bd412b08d3868c983a8b5adede53f52bfd`。

补查确认该root `resource_budgeted=false`，开发正在核实原生团队预算分支未持久登记与 `sync_work` 严格条件之间的问题；不得凭错误码硬补标志或放宽租约栅栏。修复须先红例、最小代码、审查及独立隔离PG复测。真实W05本批已消费，不自动重跑或将旧失败回填新候选；若修复后真实任务复验受预算限制，依用户指令列待回验。

### 该缺陷的代码修复已通过工程验收

原因已确认：原生共享预算支路依据 `self.team_budget` 生效，却没有持久登记 `resource_budgeted`，导致严格 `sync_work` 更新被跳过。最小修复将原支路读取策略的SQL改为同一事务中 `UPDATE resource_budgeted=true ... RETURNING`，绑定owner、原有效租约、RUNNING、未取消；预算拒绝会回滚登记。既有未知同步栅栏、预算上限和旧运行数据均不改，不按case或错误字符串硬编码。

作者真实PG先重现旧代码失败，再以私有候选验证九项；root审查通过后合入正式源码，非作者普通导入全新隔离PG复验 **9通过、零失败/错误/跳过**。包括已知结算回调两种顺序不重复计数、KNOWN/KNOWN/UNKNOWN同步及后继拒绝、五类无权/失效状态拒绝和预算失败事务回滚。只是回调和账本SQL，无实际模型/consumer/沙箱；不是W05业务复测。

独立收据 `.local/team-workspace/agent-evaluation/qa-work-extra/usage_fix_formal_8eb5a05ed6de491798d8e281d780a5ae/independent-receipt.json`，SHA256 `2f2c47677fb9bcc26c61fa2863c1ad87d2fa2d0fef6d778fc2020bc685c8a268`。新库 `owner_agent_usage_fix_cd4b48203d324dd89420fa429f0710cf_test` 经五表零、会话零后精确回收；原失败库保留。

正式usage单元及团队预算相关工程回归另 **132通过、零失败/错误/跳过**，收据 `.local/agent-evaluations/team-active-usage-team-750752b772384bdaaf9bedc193a3c640/receipt.json`，产品源码前后摘要一致。该组与之前F05的132组不同，不累计为业务任务。API完整配置Ruff及Git空白检查通过。实际修复文件SHA256 `a74b4c92548e605cdbb4f2e2af56c77599323f98874febb2457eea6ebc73e687`。真实修后W05仍待回验，原13/20验收计数保持。

## 发布边界

本轮管理接口已在测试环境可用，生产仍是原稳定核心。真实W05未通过，不增加原13/20受控验收案例。Work/Workflow/自动任务及新版评测执行不因这些接口或工程测试自动开放。正在准备元数据管理可单独发布的迁移、恢复、启动及回退验证；缺失可信执行链的部分保持禁用并明确延期。

### 受限发布模式：代码与工程测试通过，部署验证待完成

新增实例级 `Settings.restricted_execution`，默认false、仅关键字参数，保留原有调用及默认行为。受限模式拒绝矛盾的后台开关，API及独立Ask进程都绑定自身Database实例；不使用全局产品替换。API跳过启动恢复写入、Workflow消费者及外发投递；43个当前已知执行端点暂停，内部Ask工作流工具在任何查询/提交前拒绝。普通管理与Ask通路保留。新增执行端点仍须人工审查禁用清单，不能宣称自动发现。

作者私有候选v2为 `services/evaluation/.local/restricted-metadata-host-v2/manifest.json`，SHA256 `4e61ec65f7ba6f0073df5448cee2dfd1daedca6de526a5ef1dd47f07cc1cc003`。经过独立静态审查后才安装正式源码；私有包导入分类问题只在新v2整理，非import AST、全部绑定和断言保持，原v1不覆盖。正式API配置Ruff通过。

非作者正式普通导入57项工程测试通过，零失败/错误/跳过，五个修改产品模块来源均为正式API；真实生命周期和独立Ask入口的DB/网络/服务边缘明确使用替身，不冒称真实进程或SSE。唯一被拒bind已通过单独诊断同组重跑定位为urllib3固定IPv6导入探针，仍被拒，没有放宽网络规则；重复57不累计。独立收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/restricted_policy_formal_diagnostic_f61d5434e03f4e8daf1eed1439508c24/independent-receipt.json`，SHA256 `fe45122aca4a68027df6865227529df9a1319caea71b0b5664ba9baf58ee6cc5`。

合并后客户端575、profile221、评测API单元122各自通过、零失败/错误/跳过；源码前后摘要一致。原件分别为 `.local/agent-evaluations/team-active-client-10fc31e196104b2ea126edf235b665fd`、`team-active-profile-59f4e59fa00f49cc93d6e6612683aad1`、`team-active-api-evaluation-03d9e1d876884babaa1834115f6e2928`。

生产79的只读迁移影响预检已完成，47表原列摘要和key摘要封存；083预计受影响Work/session/workflow均零，082预计插入四条基线审计。此为当时活库预估，不代替最终备份或以后用户变更。收据 `.local/production79-migration-impact-20261104-v2/receipt.json`，SHA256 `76666adc463958e9a75a5740c8f538af699e68a35244a2462aae867d8c7c6a5e`。实际79→85克隆升级、真实独立Ask/SSE与schema85应用兼容回退仍须通过后才进行生产迁移；原schema79宿主不能直接当85回退，不能用旧备份覆盖上线后新记录。

### F85 独立兼容核心：静审和无数据库工程通过

F85 v3 固定原2818源码，仅config/main/ask_workflows/ask_server四处受限覆盖，新增配置仅关键字参数；旧79宿主及原包保持。175资产来源及Git原blob经独立核验，实际冷进程57项通过、零失败/错误/跳过，136个app模块均来自该固定源码。

收据 `.local/team-workspace/agent-evaluation/qa-workflow-extra/f85_v3_cold_bc70d01bff2a4e1ebc3811d65cbbb141/independent-receipt.json`，SHA256 `7f8eadc297e87eb85ac45c631e04dcf295dd747c1c80713541d8a7cafc15c5e1`。真实PG连接入口禁止且实际调用零；生命周期组件和SQL仍为明确边缘替身，不等于真实85数据库、Ask进程/SSE、迁移回退或UI验收。首轮collect因Windows审计executable=None白名单编码失败，0测试执行，原目录保留；仅修测试入口的精确git archive白名单编码后复测，产品和断言不变。

当前C85固定正式提交1358d026、351项源码，F85固定独立v3；下一门禁是最新79备份在独立克隆完整迁移85，随后两个版本真实同库往返。生产仍保持79稳定核心。

### F85 向量兼容补充：v4审查与工程复测通过

静态检查发现原旧实现重建向量不认识084的fingerprint/dimensions，新版写入的校验字段可能在回退维护后过期。本轮没有伪造校验字段或增加生产迁移：新F85 v4在处理器前暂停9个文档/记忆/图片向量写入与索引入口；知识检索在回退期间跳过向量和重排，记忆检索明确降为关键词。普通知识库名称与记忆正文维护保留。C85正式实现不因此降级。

v4共六处原2818源码覆盖，原v3封包及57项记录保持。独立静审闭合后冷工程69项通过、零失败/错误/跳过；收据 `.local/team-workspace/agent-evaluation/qa-work-extra/f85_v4_cold_5a6716e04df34faca28d88d36207ac2d/independent-receipt.json`，SHA256 `4e9554748e6b3d317361b8c92c80f6b66ac8dac630cf41fc90fe95f520f761dc`。实际四文件API Ruff通过。baseline-overlay.patch是继承v3四覆盖，新增两覆盖见v3-to-v4.patch；包内旧Ruff口径是继承材料，不能冒充v4新检查原件。

另增独立计数反例，避免旧检索函数捕获AssertionError后掩盖调用：两个冷进程分别运行原v3/v4实际search，观察模型解析调用由[configured,configured-rerank]变为[]，均保留关键词证据；独立附加两项通过，零真实数据库或模型调用。收据 `.local/team-workspace/agent-evaluation/qa-work-extra/f85_registry_control_de54a6b7b09844dd81172976ae9c08a9/independent-receipt.json`，SHA256 `be6557b466c46be5f945a4f27a76d68c9e76a5b280d4c8fa1ddb12db87aaf147`。不是A20业务复测，也不增加原13/20。

克隆测试宿主首审发现F85重复来源校验的固定git archive会被网络/进程审计拒绝；v3仅放行冻结命令、cwd和参数，保留全部来源检查及负控制。root关闭该审查项并独立核纯控制测试。宿主采用实际API/独立AskWorker/PG/SSE和明确的单调用模型替身，不把测试专用入口冒充未经修改的生产AskServer。真实升级/同库往返仍待克隆运行。

## 2026-11-05：管理版本实际上线

组合C85→F85→C85三轮同会话工程验收及独立复核闭合后，取得最后一致备份，生产正式迁移85并启动原生受限API/Ask、新Web Uh42。原账号密码、47表原列数据、key和用户手动状态保持。实际3100代理登录及管理列表、后台/manager健康、18静态资源通过；评测执行仍503/零新排队/零新模型用量。旧Web是专属控制台合作退出/native130，没有使用强制停止备选。完整证据、提交记录和明确人工待验清单见[本次发布运维记录](metadata85-release-operations.md)。

这是评测题集/版本及管理入口的受限发布，仍非完整DeepEval执行上线；原13/20业务覆盖和全部真实失败保留。浏览器点击验收及真实供应商联调不以本次HTTP/受控模型通过替代。
