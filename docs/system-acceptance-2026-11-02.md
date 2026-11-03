# 统一需求补齐与全系统验收记录

2026-11-02，工作区 `D:\project\my_agent\deepagents`，分支 `onwer_agent`。依据[系统总需求](system-requirements.md)和[完整测试用例](system-test-cases.md)，先产品核对、开发自验及冻结，再全量后端、本机生产部署、整站页面与串行恢复验收。不提交或推送。

**结论：本轮本机可验范围完成。** SYS-072、SYS-074实现并部署，必要回归与恢复检查通过，样本清理和原数据保护检查完成；外部效果、可选基准和另定扩展按末节保留。51项需求都有对应测试用例，共62条，无遗漏或重复编号。

## 本轮开发交付

- **SYS-074**：任务、工作流、仓库、批次及触发记录的服务端稳定分页和字面搜索；两类运行历史续页；旧工作流精确定位及保存，旧运行精确轮询。保留原配额和接口的items结构，无数据库迁移。
- **SYS-072**：整套部署脚本 `scripts/deploy-platform.ps1` 和只读检查 `scripts/check-platform.ps1`。独占准入锁跨越检查与停后台窗口，离线备份及manifest校验后迁移，按序启动API/Ask/Work/Ops，构建生产Web并检查四种真实心跳。保留原配置与密钥。
- 产品冻结时发现的上述两个真实缺口已实现。SYS-073可选官方协议对照、SYS-076真实外部质量及SYS-081另定扩展，不与本轮实现混为一谈。独立语音合成、图片理解入口按用户决定继续隐藏；对话图片及知识库相关能力保留。

## 开发自验与缺陷修复

- 4项候选分页页面测试通过（9.7秒）。首轮发现续页失败清空原列表及游标，修复后验证失败重试保留列表；搜索迟到响应隔离和旧记录定位保留。
- 全量后端 **458 passed，0 failed，0 skipped，1018.54秒**。使用隔离 `owner_agent_test`，开启真实本机执行环境；外部模型和业务协议使用确定性替身。证据 `.local/system-backend-full.log`。
- 全量后审查追加游标id整数输入回归，任务和工作流两个接口均先复现AttributeError（2项失败），显式字符串校验后，核心及资源分页 **6 passed（13.26秒）**；证据 `.local/system-cursor-repro.log`、`.local/system-pagination-final.log`。
- 部署helper释放前补最后一次锁/连接验证，避免EOF与连接丢失相邻时误报成功；随机隔离库的 **16项准入测试全部通过（2.99秒）**，含确定性失锁+EOF及真实断连+EOF。测试库已清理，未模拟覆盖生产库。
- TypeScript和全后端Ruff通过；前端完整ESLint **0错误、38条既有警告**。不把警告隐去或顺手改写无关组件。生产构建编译、类型、静态页面及trace全部完成。

全量458与后续定向6/16分别记录，不相加冒充单轮480项全量。后续仅修改两个边界校验并复测其调用范围，没有更改业务执行或权限规则。

## 部署与原数据

12:55:59本机整套部署成功：`.local/platform-deployment.json` 为 `status=deployed / phase=complete`。迁移前备份为 `20261102T045448Z-7eddb865`，已实际校验manifest；已有管理员未重置。旧Web构建留在 `.local/knowledge-web-previous-20261102-125517-5a8be2b9`。

API ready、生产Web、OpenSandbox认证、Coding/Browser镜像、API/Ask/Work/Ops归属及真实心跳均通过，迁移状态current。记录 `.local/system-deployment-run.log`、`.local/platform-health.json`、`.local/knowledge-ui-build.log`。

部署后、页面样本准备前，按本轮新基线 `.local/system-regression-before.json` 核对原users、model_configs、conversations、conversation_messages、task_runs、model_usage各ID摘要及主密钥：全部一致。使用原记录子集比较，允许新增合法记录，不把旧轮次模型摘要覆盖到当前配置。

## 整站与恢复执行

默认整站79项清单已分批覆盖，最终 **77项通过、2项按用户决定隐藏而跳过**，没有未处理失败：Browser单项1通过（19.2秒）；其余78项首轮75通过、2跳过、1失败（约4.8分钟）。唯一失败为团队旧测试仍查找旧DOM的直接子级诊断节点：最终回答、22/6/3用量断言已通过，旧定位超时后又被finally请求超时掩盖。测试改为按run定位当前轮次、展开处理过程再查看诊断，保留团队状态、成员输入输出Token、工具权限和390px断言；finally仅为样本清理增加15秒，metadata先保存且替身连接总关闭。团队及相关对话交互8项复测全部通过（10.8秒），不将重复7项累加为唯一用例。此处只改测试，没有更改产品或放宽业务断言。

证据：`.local/system-browser-native-first.log`、`.local/system-browser-full.log`、`.local/system-team-chat-retest.log`。

默认清单之外，真实审批跨重启两项通过：准备待审批1项（17.3秒），实际更换API/Ask/Work主进程和子进程后恢复1项（38.2秒）。API子进程50924→14216、Ask46724→40640、Work50064→56080；前后记录 `.local/system-workflow-restart-before.json`、`.local/system-workflow-restart-after.json`。两次审批、重启前输入、固定代码及执行次数1均通过，零模型调用。首次手工恢复Ask时API尚未ready，启动脚本正确拒绝；按依赖先等待API ready后恢复Ask/Work，整套检查通过。证据 `.local/system-workflow-start.log`、`.local/system-workflow-resume.log`。

另补TC-023真实对话进行中独立API重启：既有 `tests.manual.verify_ask_restart` 通过，模型服务仅为本机替身、无工具调用，不触发真实外部检索或向量服务。API host40156→46312、child51264→41948；Ask host27372/child40464及独立实例不变，Work/Ops也不变。对话SUCCEEDED，仅请求1次，用量输入10/输出3/调用1，external_providers_called=false。首轮在只读安全预检期间超过helper的90秒编排窗口而未恢复，独立保留 `.local/system-ask-restart-first-timeout.log`，不算通过；确认其finally清理完成后，使用同一未改helper受控重跑通过。证据 `.local/system-ask-restart.log`、`.local/system-ask-restart-before.json`、`.local/system-ask-restart-after.json`。两轮均未改用户资源绑定或延长业务断言。

离线备份与隔离恢复1项通过（46.8秒）：备份 `20261102T052050Z-44bc868c`，records_match=true，files_verified=46，credentials_decryptable=7，referenced_files_verified=7，四后台各1实例在线。已包含未提交工作副本源码归档，未覆盖生产数据库。证据 `.local/system-operations-backup-native.log`。

随后对同一备份执行既有 `tests.manual.verify_repository_restore`，新隔离库 `owner_agent_restored_27f7ecdb` 中的原生Git检出、删除文件保持删除、本地提交保留全部通过，0模型调用、未启动副本平台后台，验证数据库finally删除。日志含原环境ID已不存在的404提示，最终在隔离新环境中检出验证成功，不能将旧环境当作仍可连接。证据 `.local/system-native-git-restore.log`。备份前重新创建Browser并重复1项页面种子检查（19.0秒），只用于形成加密状态恢复证据，不重复计算整站唯一用例。

普通Chrome样本按metadata、前缀与归属检查精确清理，包括失败团队样本；三组原生fixture及流程重启fixture均清理并移除metadata，远端释放收到确认。没有清空用户数据。最终新基线核对：users 1→1、model_configs 4→4、conversations 9→9、conversation_messages 75→75、task_runs 14→14、model_usage 32→32，原ID无缺失、摘要无变化，主密钥一致。证据 `.local/system-regression-preservation.json`。检查覆盖这些受保护表与密钥，不宣称所有审计、备份作业等新增观测表逐字节不变。

补充对话重启检查后再次确认六类记录数量/原ID摘要及主密钥全部一致，并再次运行最终 `scripts/check-platform.ps1` 退出0：API ready、Web production-ready、执行环境认证、两镜像、四后台归属与真实心跳、迁移current全部通过；证据 `.local/system-regression-preservation.json`、`.local/system-final-health.log`。页面保持 [http://localhost:3100](http://localhost:3100)。

本轮遇到的是正确容量排队：当时可用预算约7.27GiB，持久Browser预留3GiB，Coding需5GiB，合计超过预算。Git零模型样本按15秒退避等待；Browser实测1项通过（19.2秒），校验同页、加密私有状态及0模型调用并释放后，原Git运行正常进入审批。只对原metadata做严格归属与摘要校验后补一条离线测试源绑定，新建运行0，不改配额、不关闭其他应用、不改用户任务。

为遵守本机预算，默认79项清单分批执行：Browser单项先行，其余78项随后；持久流程重启验证在Coding窗口执行。最后重新准备Browser状态用于备份恢复，不将重复种子测试累加成整站唯一用例数量。详细操作顺序见测试手册。

## 验收边界与交接

- 真实外部模型、原生联网、语音/视频/图片生成质量、业务MCP、GitHub及真实网站等，仍按用户要求跳过真实联调；接入后按[integration-checklist.md](integration-checklist.md) U01～U16逐项验证。当前协议正确不代表真实服务效果或费用已验收。
- SYS-073官方LangGraph开发服务基准为既有U11可选项，本轮未安装可选CLI/官方服务，不冒充已验。真实50题业务检索和生成引用质量SYS-076未测，合成题检索指标不能代替。
- 没有故意破坏生产迁移/构建演练全系统回滚。已有Web旧构建、离线备份和隔离恢复，以及准入失败测试有各自证据；不把它们写成任意故障下的自动全系统回滚。部署失败按阶段日志人工核对，不自动覆盖生产数据库或重放未知操作。
- 开发交接：[实施文档](system-completion-development.md)、[核心分页](system-pagination-development.md)、[资源分页](system-resource-pagination.md)。测试交接：[62条完整用例](system-test-cases.md)。运维交接：[部署说明](system-deployment.md)。
