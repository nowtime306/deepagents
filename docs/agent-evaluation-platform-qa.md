# Agent评测平台独立审查与工程QA

## 可复用数据库回归补充

根角色审查可移植fixture的隔离、只清理本fixture账号、首败留存和无CREATE/DROP数据库逻辑后，将测试迁入`services/api/tests/integration_tests/test_agent_evaluation_platform.py`；字节SHA256为`ca46e6cf4e61c472a2f4ed07665e8bea341d6be7848fd6ae1a0207fcec76177d`，业务断言与原私有22项一致。

随后在另一个全新本次数据库实际运行，**22通过、0失败/错误/跳过**。它与下文私有22项覆盖重叠，不累加。调用者显式提供本机`TEST_DATABASE_URL`与真实独立`TEST_DATA_DIR`，两者后缀均`_test`；测试文件不创建/删除数据库、不读取服务密钥、不TRUNCATE、不覆盖已有评测元数据。由外层受控包装器核七类实际业务/模型/沙箱记录为0、连接0后，精确删除并核数据库不存在。

证据`.local/agent-evaluations/platform-portable-33d809f0ff654af1a6fe6501945effab/`保留日志、JUnit与回收收据。此补充仍是元数据/HTTP/权限/真实PG验证，不是运行Agent、真实worker、页面或部署。开发版本`ce95a1196914c855aef9068cc53fae08f2f9c45e`包含此前已核前后端实现；后续新增fixture不改变原通过批次的字节或时间。

日期：2026-11-04。独立审查/执行者：rdc_history；前端与权限接线作者为root，后端core/extension作者为rdc_index。本记录按文件内容摘要绑定候选，不声称某个旧HEAD已经包含未提交实现。**本轮通过的是元数据接口、权限、SQL事务及前端工程检查；没有完成真实中文UI验收、持久评测worker验收或生产部署。**

## 冻结范围

- 前端/权限7文件：`services/evaluation/.local/platform-fe-freeze-125ea1d54d764e8daa20ade9cc5d179a/manifest.json`，SHA256 `7e54c674cff71faea9b7246a80a6e68dfaae66f67bc2d602a5b0759c36c04646`。
- 后端base：`.local/team-workspace/agent-evaluation/platform-core-freeze-62e2fa78791f4cc7a6d1f714fae9844a/manifest.json`，SHA256 `da6df2a93dfb1fd90b2a0ca3282c93d7fe7635d6db5d8e5717bf22590de26031`。
- extension：`.local/team-workspace/agent-evaluation/platform-extension-reviewed-base-32d7439933764e418dad6664e2d017bc/manifest.json`，SHA256 `9806a2204f658938d1b402ae0364d04ea10c0be3bb9951cfe6596a72382434ef`。
- BE01–03/profile/publication-v2修复：`.local/team-workspace/agent-evaluation/platform-be-review-fix-e4b1543a02d74c15a78335665d149437/manifest.json`，SHA256 `ac7d12f30dde7d4b9288e1a3b6b8c925d84fdc05c211c77db2b3971b10d45822`。
- BE04 hook修复：`.local/team-workspace/agent-evaluation/platform-hook-review-fix-a1b46e1cb92d4df68a76e423751a9892/manifest.json`，SHA256 `321e210a725b44f3e57dc3132ea78407b35d4b5d3df923a626feb4f52e6e8fbe`。

后端按上述顺序覆盖同路径后共10文件。085/store/queue保持原冻结字节；正式main的router接线另在窄smoke中核验。各组执行前后相关冻结文件摘要一致。私有证据目录不会因本公开文档而自动随仓库发布，须按现有证据留存方式保留原件。

## 审查发现与闭环

前端FE01–FE07已闭合：批次QUEUED/CANCEL_REQUESTED识别与GET轮询、取消CAS修订、工程模式固定单次与明确422解锁、完整未保存关闭提示、定义数量不冒充独立业务目标、编辑保留description、已加载样本分页窗口与旧响应竞态。保留首轮c6e254的原始缺陷复现，不把旧失败记录改成通过。

后端BE01–BE04已闭合，并实际重复原独立反例：

- BE01：effect声明三次但仅有repeat1时拒绝可比较；逐case必须有准确且无重复的1..repeats集合，工程/效果模式与重复数配对。完整三次正例仍可比较。
- BE02：null身份/摘要不再因双方相同而产生可信提升；实际模型/业务/平台身份强类型化。模型ID保留现有TEXT兼容，明确完整的零provider列表与缺失身份分开。
- BE03：整行鉴权/cookie值先脱敏，原`Authorization: Basic`凭据残留反例已被拦截；仍要求人工确认，不宣称正则能识别所有敏感内容。
- BE04：固定profile hook只有literal True表示核验成功；空对象、明确BLOCKED对象、字符串和整数1均不能准入。

Publication-v2区分控制验收与原业务结果：W01原task/gate FAIL不得翻绿；正确负控制可按已审定义获得独立控制验收PASS，仍必须由可信验证器核验原始来源/摘要。默认没有可信发布收据或注册执行驱动，平台执行持续BLOCKED。结构化草稿/preview合法也不表示profile参数已验证或可执行。

## 前端独立工程检查

证据根：`services/evaluation/.local/platform-fe-independent-review/qa_8f2c2981ffed/`。

- 自主21项纯TS/处理函数检查通过，0失败：状态/未知不绿、description与CAS、显式零值预算、dirty/busy关闭、422与未知提交区别、原request_id与原payload显式重试、200行分页窗口保留、旧响应拒绝和取消revision。
- 作者9项权限路由用例由非作者独立执行，9通过，0失败/错误/跳过，0.09秒。这是路由映射检查，不是HTTP owner/实时权限证明。
- TypeScript6 noEmit/incremental=false与两个新FE文件定向ESLint/max-warnings0均exit0。
- 独立生产构建exit0，Next16.4.2/webpack，优化编译2.6分钟，静态页4/4。使用私有源码副本`qa_8f2c2981ffed/web-source`和独立输出`.next-ae-independent-qa_8f2c2981ffed`；build ID为`frVNDrkpYE8m40SpHXic8`。构建自动修改的tsconfig/next-env只位于该副本，未写运行服务使用的默认`.next`，未复制产物到任何服务。

纯TS检查使用受控函数替身，未挂载真实React/DOM；类型、lint与build不能证明布局、焦点、键盘、弹窗或实际页面体验。首个私有编译器命令文件名错误日志保留，只修正私有工具定位后完整重跑，未改产品或放宽断言。

## 后端独立工程与真实PG

证据根：`services/evaluation/.local/platform-fe-independent-review/backend_qa_d8c912d824a7/`。

- 精确两个作者contract/extension文件，由非作者独立执行：**111通过，0失败/错误/跳过，0.55秒**。这些主要是纯合同与ASGI持久层替身，不能当作真实SQL。
- 另写的22项私有独立用例在唯一新库、真实Database/085迁移/会话cookie/identity/当前角色/ASGI HTTP下执行：**22通过，0失败/错误/跳过，3.34秒**。没有mock数据库、身份或权限查询。

22项覆盖：默认503且零计划；跨owner（管理员同样404）和实时只读/停用；草稿CAS、并发单胜者、旧发布版本不可变；run相同request_id并发/重放唯一、异payload409；故障触发器下发布及父子计划事务回滚；排队取消CAS/终态重复不新取消；lease token/期限/权限栅栏、未派发可重领、已派发未知保留容量；8种坏profile hook零enqueue；报告owner/格式/摘要/附件及NEEDS_REVIEW不改成PASS；复核追加不改原事实/评分/gate；候选脱敏/审批不自动执行；分页不重复与坏cursor拒绝。

允许创建metadata计划的测试显式使用隔离、受控的Publication/driver替身；这些替身不代表真实A20证据、注册worker、业务执行或可信结果导入。Queue函数的手动SQL栅栏检查也不是一个实际worker生命周期。报告行使用明确合成的元数据fixture，不是实际业务报告。

## 正式main窄接线smoke

最终证据：`services/evaluation/.local/platform-fe-independent-review/main_smoke_qa_b5c0b5922f10/`。**1通过，0失败/错误/跳过，5.86秒**。

在另一个新库和独立data_test下真实`create_app`/登录，依次读取capabilities、创建/发布本人A02有效subset、确认run默认503且零批次/样本、读取原Ask会话列表、核对新增路由审计201/201/503来自同一actor。ask/work/automation/evaluation/media开关显式关闭；main原有无条件workflow消费者只看到空表。专用虚构资源连接与隔离instance避免回退读取原manager密钥。未发起Ask/Work/Workflow/model运行，未启动API监听服务。

两次先行私有前提错误保留原FAIL/log/源摘要：第一次Settings拒绝低端口，尚未进入create_app；第二次全部正式HTTP步骤已过，最后附加audit查询误把LIKE字面百分号作为psycopg占位符。仅修私有地址/参数化查询，原业务断言不变，再用新库完整重跑得到最终1通过。它们不算产品失败，也不与最终通过相加。

## 隔离、回收与证据摘要

所有子进程使用系统变量白名单、私有HOME/temp/data，不继承真实模型/Confident等凭据。PG管理仅从原配置读取本机DB credential source，创建随机`owner_agent_platform_*_test`，不连接生产业务库；未输出DSN/密码。Python审计仅放行本轮PG端点和精确stdlib socketpair：contract/PG/main组分别记录17/23/3次socketpair、0其它被拒事件。libpq/OS完整流量不在Python审计覆盖内，不能由此声称OS零外连。

本轮核心库、最终smoke库及两个已知测试前提错误库共4个，均精确DROP并后核不存在/连接0。核心与最终smoke回收前读取Ask/Workflow/Work/模型/资源会话五类计数均0；两个失败诊断库另核上述五类及操作、评测运行/样本共八类均0，才回收。不存在强制删除/全局清理。原失败日志、诊断及原evidence未改，回收记录另附。未接触旧4627真实业务诊断库或任何用户目标。

私有摘要（SHA256）：

- `final-fe-review.md`：`895a3f0028422096034740367289d332c8666b91580d8cd0b2df355fb6bd1f53`；`final-fe-completion.json`：`11e87eedbdad47d6c52320ec98273f77bd00e3fe280f09cfc55980361755f22d`。
- `backend_qa_d8c912d824a7/evidence.json`：`fd6b20d57d6f0433c2f3d3beab344df24f32ea57fddc61639efe339cf51e4437`；contract JUnit：`7c7c43c4bd4b0003d2cef47d42aa1cf149a4edc3e661e0213caccaa84c9aa77d`；PG JUnit：`954a07d6a2ffcb77fd5431a7eb34e89613eb7b0b6f241abfd94ef095e73c1b81`。
- `main_smoke_qa_b5c0b5922f10/evidence.json`：`31fdc52b3c9eaa12a4dfe07447b581e056026f8f580fd07f1d5aadd081426517`；JUnit：`3f89e0751ab15a19a928fc947bf18bb1d3fbc28dbc2c3bf65b2ddecbf667bac1`。
- `backend-hook-fix-probe-results.json`：`9903cd083a9d0c2e3aaa21f2dc251db857c613a005eee6ff3951883cc512cc0a`。
- `smoke-diagnostic-cleanup-summary.json`：`a80b9e277b6d9f6fcd548d2836f4b88afe4f8ba0a7cfd13cb944cabcb3339146`；两份单库诊断回收收据分别为`8e4a7f619c16d9d90c9c963b29c2c441b6d3dc0cf2ac52dbefc40c5609d5afd4`和`42e51cc8ca26fd2efc40e20cb9af3c6612ee5d33bb4571a7f2c29d89018477e8`。

以上文件均在`services/evaluation/.local/platform-fe-independent-review/`下。分组结果不得与旧401/66/18、Java工程或此前真实suite重复累计。

## 未测与发布门禁

- 实际页面仍未验收；没有绕过已保存的浏览器拒绝，也没有通过抓HTML冒充页面体验。管理员/只读/窄屏/键盘/对话框、真实下载与未保存提示仍需获准浏览器验收。
- 未验收实际持久worker、子进程/来源验证、真实target权限传递与每次效果撤权、取消回收、真实report importer、费用或外部模型语义效果。默认BLOCKED不因元数据API、构建或本报告改变。
- 原完整A20旧catalog的A05仍为空输入，按已冻结剩余需求须先修订为明确非空任务并更新定义摘要，不能放宽成空题通过。本轮真实CRUD使用有效A02 subset，不声称完整旧A20导入或20例执行完成。
- B100仍是设计/分批实现门禁；未做100个不同业务任务及三次独立效果重复，未做统计显著性或可用率承诺。
- 未部署、未重启用户服务、未发布新UI；未访问8101/3100/8100/3131，未运行供应商模型或付费服务、上传云平台或启动资源manager。

可复用的22用例正从原私有源码准备为`services/api/tests/integration_tests/test_agent_evaluation_platform.py`审阅候选：必须显式本机*_test库和独立data_test，不能创建/删除数据库、读取原服务密钥或清空他人metadata。该可移植fixture与原已执行22项的隔离生命周期不同，批准后仍须独立新库执行，不能预先填写通过。
