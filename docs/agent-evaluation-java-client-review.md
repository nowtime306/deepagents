# W02正式客户端接线审查与工程验收

日期：2026-11-04；分支onwer_agent。实现作者与根审查/执行者分离。依据AE-04/08/09/11/14及Java保护专项。此记录不是W02真实任务验收，也不增加当前8/20个不同案例的覆盖。

## 审查范围

私有实现冻结eba51a17c667f9fcba9c2b6dd5591fa15cddbe7225f699ee09ba5c1944f57193保留；新程序pin补充99abaf0cb86884db45dd70ffdb6ade00450d60e6669979e773af95da5b4e0d2d只更新保护程序身份。根角色核对原文件摘要后合入四个共享小增量及两个模块，未用旧整文件覆盖其他改动。迁入五个测试只调整active导入和路径，原业务断言保留；导入排序检查另行修正。

- 候选通过正式Work队列真实读取/修改上传修订2；最新完整snapshot唯一Max.java下载至多64 KiB，校验长度、UTF-8及原生摘要，源码与diff可重算。
- 封存候选证据后，正式创建独立空修订1任务；固定注册接口仅接收任务/原运行/成果/源摘要，不接命令、路径或脚本。同一个WorkWorker仅将已封存任务分流到保护执行器，不伪造RUNNING或另开worker/manager。
- `PROGRAM_SHA`为实测2c91f045fd9bdd7713255efed8b52588464c07bddad190d21d0b743008f14a62；能力、注册、结果、原源码与编译/逐测试输出、退出/超时及保护凭据逐项绑定。
- 零模型验证不仅检查缺少provider记录，还要求真实原生六项零值、已完成的封存executor、同一真实Work结果及唯一源provider三次/45 Token。缺省、未确认或未知用量不补零。
- owner/task/run/model身份及全案例根数严格检查；禁止额外任务、操作、未知事件和重复/倒序read/edit调用。已知源码或保护测试失败先保留FAIL，后续资源未知不能改成PASS。
- 两根预算与两次沙箱预约共享、持久化，消费者重建保留固定回调。未知提交不重放，证据持久失败禁止继续派发；最终事实/ready与精确会话、原生用量和库存必须闭合后才可CLEAN。

此前十二项遗漏反例（多余操作/事件、额外owner资源、未知用量、倒序及重复call ID）在原私有准备中复现并修复，原失败日志保留；根角色复核最终路径后再执行工程QA。

## 实际工程结果

根执行者使用两个独立现有环境运行active代码：客户端新增43通过、目标侧新增30通过；随后完整客户端396通过（含原353与新增43）、目标侧完整149通过（含Java等既有组），均零失败/跳过，不将重叠批次相加。Ruff通过。分别使用独立临时目录与受控HTTP/SDK/DB替身，没有真实业务、PostgreSQL或Java执行。

原件分别为 `services/evaluation/.local/w02-active-client-d58faa554e2a47c1910330a853bdfd44/`、`w02-active-profile-c1b02a15fc454597a4bfd9b76d14e923/`、`w02-full-client-3b92abaa4fc441d2b22cecb708c50550/`、`w02-full-profile-132f477528ce43639cf8c76b8c52f421/`，日志/JUnit原样保留。Java11组独立物理测试及交叉复算另见Java专项，不把它当作W02任务执行。

## 放行边界

固定suite登记W02处理器和最多两次沙箱创建，初始化器摘要同步到ed507df0c1f9ce7bbde954d9187789d0ffc527c118612e19920a85a73c82fad3；这只是允许全新隔离目标预检和后续正式样本，不代表任务已通过。仍须固定源码候选、隔离实际PG/队列/双Coding链路、原事实独立复算及回收；不得借本篇宣称A20/B100、真实模型效果、页面或生产上线完成。

## c71候选实际失败与接口修复

原批次`suite-w02-a03-a04-c71bed9-aa7de6e5de88441bbde22bb5a4223bf4`的W02已正式提交源码修复任务，随后因Work详情缺少`model_id`抛KeyError，尚未提交保护验证任务。run和cleanup的最新审计为ERROR；旧report仍是初始化状态，不能据此断言未提交。该环境保持NEEDS_REVIEW/RETAINED，原目标进程已停，诊断数据库保留，不补写原报告或复用重跑。

修复仅在现有owner限定SELECT增加已提交的`model_id/model_revision/task_revision`，不连接当前配置、不暴露密钥。非作者静态复审关闭，收据摘要`c439bf44076b5bcc448da49cd6e43a9fcc5e1e6cfa9e0514625c9a2d01b180de`。新增真实PostgreSQL/HTTP回归：当前模型7/草稿4、历史运行3/2，读取必须返回历史版本且外部管理员404。原代码实际200后KeyError的红证据保留；修复后1通过、零业务副作用/零会话并精确回收新测试库，路径`.local/agent-evaluations/work-projection-green-9c4eb236b6e14f66a07a1af97e1e469e`。最初夹具缺work.read导致403的失败另行保留，不冒充产品缺陷。

另补三项字节pin资产的精确Git换行规则，沿用原程序/初始化器CRLF和launcher LF，不修改源码字节或摘要。独立前缀在autocrlf=true下原launcher摘要不一致；统一LF尝试又改变另两项，失败证据均保留；最终三项按原换行检出摘要完全一致。非作者复核摘要`526988b7200ae3990bb207aa5849cabfb30671473ecde62c001a2ca4af4822b9`。后续须新候选、全新W02环境复验。

## f2fd样本缺口与双阶段对照开发

全新`suite-w02-f2fd3c9-fbc59bd6688944889707a2f681042db7`正式两任务/两会话执行，修复源码三项实际JVM用例全通过，原生源三调用30入15出、验证器六项零值；正式CLEAN并精确回收新库。独立核验发现原错误源码未在本批实际运行，静态推导2失败/1通过不能代替实际负对照。因此原代码PASS不冒充完整W02验收，累计案例数不增加。已回收后的Docker历史事件为空，保留历史覆盖限制，不伪造生命周期记录。

后续最小修复沿用原PROGRAM/LAUNCHER字节pin、两任务和两沙箱：同一真实验证lease和第二个沙箱先运行固定原错源码、持久保存完整报告，再运行修复源码。可信bootstrap只选baseline/candidate两个固定阶段与固定目录，归档原保护目录后重建相同路径；不接受自由命令、路径或第三沙箱。原源码必须正常编译及实际运行三例输出2/-7/4，确认两项错误，不能拿编译失败、超时或缺报告作负对照。

注册schema3仅新测试环境；BASELINE_RECORDED重建保留UNKNOWN、不重放。server、observer、客户端、离线及CLEAN共同核两报告、原/成果源码摘要、同JDK/program/launcher/tests身份，baseline保存失败或lease丢失不执行下一阶段。旧单报告目标在preflight阻断，原历史报告不回填。

私有实现冻结摘要`29db3365efa4f4e6b1a2e92cd6b57d58fbeb39980765aa975e5a0fa6c65f50b0`经root非作者代码审查关闭，独立等层副本50项工程通过；只是协议/SDK/SQL/ASGI替身，未实际执行新Linux双阶段。可移植迁移仅替换导入、更新真实双阶段夹具及增加反例，保留原有断言；四项offline/CLEAN反例改用现有完整MockTransport fixture，不在仓库测试中依赖私有历史报告。迁移冻结摘要`a5da1fff3da8ed70808f43f0f448f96657927a3e2d8fd088c5d6adf74a5bb42b`待root复核后执行active工程测试和新正式样本。先前私有依赖历史报告的四项仅为合成QA，未改原报告。

root复核并合入四源增量及六测试迁移：完整active客户端492通过、profile219通过，零失败/跳过，原件`services/evaluation/.local/java-baseline-active-a09306f603844751ac623804fab3a725`。包含既有488/199及新增4/20，不累计相加。target同时保留已审F01预算/温度补丁，只改变baseline capability一行；合入前目标原字节摘要acc978...与已审私有预算候选仅换行不同，规范化文本完全相同，未覆盖其改动。原PROGRAM/LAUNCHER字节未改。下一正式样本须固定候选并预先采集生命周期；当前11/20的限定案例覆盖不因本工程测试增加。

## f047双阶段正式限定验收通过

新候选`f047aea6b7876c184865c1b54bdf688c84f559fd`在全新`suite-w02-baseline-f047aea-1bb02c81a7af4e4e95441e5ee18bd59a`实际执行。原源码正常编译，三次JVM实际输出2/-7/4，两项FAIL一项PASS；修复版三项PASS，两报告同JDK/program/launcher/tests，registry持久baseline/report与正式结果相合。真实源三调用30入15出，验证器六项原生零且实际provider无调用，差异/成果原字节可重算。

预先启动的只读生命周期观察捕获两真实容器/会话和精确run/owner/namespace：每个create→start→die→destroy各一次，源容器destroy早于验证容器create，六次manager样本及末尾库存零，错误为空。lifecycle原摘要`710241e0f232aeed0bc4bd57d3b4aae42313167cf09217d2d9139200161bcd6e`。非作者另核57原件未变、328来源对应原Git候选、四CLI当前审计/ready及父子报告一致；现场只读库不存在、两个原进程已退、namespace与两精确egress-sidecar均零。

独立清单`.local/team-workspace/agent-evaluation/ops-development/w02-baseline-f047aea-verification-manifest.json`摘要`c7070d61f8ff045833081e307fbf7f97009e29faf4380e3faf3fa1fdb1fb0304`，后记摘要`2e6534ccae1d4f47d192725e4b0d504665e360de839f3720928359ec257e08a6`。后查Docker历史为空不覆盖执行时完整原事件。此次只计一个受控修复案例/一次独立重复；旧c71和f2fd结论保留，不代表全A20、真实供应商质量、页面或部署。
