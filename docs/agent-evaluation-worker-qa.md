# 持久评测Worker审查与验收

日期：2026-11-04；分支onwer_agent。源码合入和运行启用分开：没有真实A20出版、受信bridge、固定实际runtime与显式consumer启动时，运行入口继续BLOCKED/503。本文不是上线或完整B阶段验收。

## 已审实现与默认关闭的源码合入

v2冻结摘要`3367f98d9a07272655adb521bdbf301404b269ce3f3c201a693723b321426227`，非作者审查关闭且独立66项工程通过。v1发现的required许可配置丢失竞态、非bool许可响应、实际运行器/模块/锁来源未绑定问题均保留原失败记录，v2逐项修复，见私有审查清单`01bd3b0f47185e25a19071215e3e06efe1fe82398b06a6473725f286a066f738`。

合入只包括五个已审新模块和十二处可选接线：持久租约consumer、独立固定评测子进程、同bytes MAC与nonce运行器证明、required父绑定marker、逐provider/tool/sandbox/submission许可。API不加载DeepEval，不从公开请求接受脚本或自由路径。未安装evaluation guard时既有业务路径保持原行为。实际解释器和依赖在prepare/dispatch核验，运行中安装目录仍须运维冻结，不宣称OS不可变。

root已复核全部31个冻结资产与十二处当前base字节，应用小patch，保留已有F01/Java/KB修改；仅对新增模块做导入排序、两处有理由的单行Lint例外和新增接线空行整理。不注册driver/publication、不改main/lifespan、不启动consumer或生产服务。测试迁移保留业务断言，清除私有loader/历史.local依赖；独立runtime特定环境须如实标注，不能伪称默认CI全部可执行。实际parent-bound业务链仍需另验。

## 本轮真实PostgreSQL验收

测试作者evaluation_ops，非作者审查与执行root。冻结`worker-pg-review-ops/manifest.json`摘要`63e3e00bedf0a928e26c19655f3bcb23bfa337916dc105f5701fc5eb832b33c4`；测试源码摘要`69459cc7c09471140fc5c4cb8efe3f3f505c93fd32391dfa036aa9dcf44dd0ab`。复用已审h/migration，在外层新建随机local *_test数据库/独立目录，以hash锁定的私有loader加载v2类；不应用补丁来偷换测试前提。

五项均已实际执行通过、零跳过：

1. 实际claim、第二申请者互斥、begin/renew及一次原子导入；重复导入拒绝，整行状态不变。
2. 外部fixture管理员owner及失效lease拒绝导入；失效续租/派发为false，样本仍PENDING。
3. 在sample UPDATE之后，由真实reports唯一约束触发UniqueViolation，全部run/sample/report/event回滚；没有trigger或改schema。
4. 取消后续租、派发及后续效果许可均拒绝，无额外元数据写入。
5. 撤回owner执行权限后续租403、效果许可拒绝，无导入。

队列和typed报告均是明确合成的工程数据，内部verified字段仅用于测试可信导入事务边界，不证明真实MAC、Agent事实或A20出版。最后两项只对本地Worker对象替换admission.inspect以到达实际SQL/权限校验，没有注册到应用、没有创建可信publication或调用Worker.serve/driver.execute。不能据这五项宣称完整consumer或真实Agent执行通过。

原结果`.local/agent-evaluations/worker-v2-pg-3ee7de5e16c84d14a0f061c613279d49`：5通过/1.64秒，七类实际运行效果均零、库会话零，精确新库已回收。receipt摘要`10ce81a09e56574b499924a1f526cdfc6097ae3528c32b933149238cec4f1a45`。初次包装器准备遇作者新增loader后manifest变化，在建库前即拒绝；重新审查最终冻结后才运行，没有覆盖旧失败或重放业务。

随后将这五项测试移入正式integration_tests，直接从active应用导入Worker，不使用私有loader或源文件替换，再建全新随机库实际执行：5通过、0跳过、七类业务效果零、会话零、新库已精确删除。原件`.local/agent-evaluations/active-worker-pg-18139bf5287b456fb25e97714092f41a`，receipt摘要`a00234a1dae18b3951b99ba06d2732061afdc973a9594b9f53dd5fd6711bb65f`。这是同五项的可移植复验，不累计成十项或新增业务案例。

## 合入后的已有路径回归

active源码独立执行完整客户端493通过、完整profile221通过、六个相关API单元文件53通过，均零失败；原件`services/evaluation/.local/worker-active-regression-937ccf241e2445bd931458cc41dfcde4`，分组日志/JUnit与receipt保留。这些使用受控协议/SDK/数据库替身，没有启动实际target或访问生产及浏览器；各组有交叉，不能求和为业务成功数。

新增五模块与公开PG测试Lint通过；十个修改的已有Python文件以同一检查器和HEAD逐文件对照，未增加规则/消息数量。原suite脚本18项、target脚本4项既有问题如实保留，不宣称它们全量Lint通过。首次按完整源码行比较的四项B023差异，是原四条回调闭包提示所在行增加parent_run参数造成；原始收据保留，复核记录`worker-lint-comparison-b4d8d8c84a2546a1b4dda93a8226e9c8/identity-receipt.json`摘要`2a6c2a71f5c3c65dada25e0a33a4f9e013b33aab08656edc442c1f80ef12d8e7`。未顺带清理无关代码。

## 发布准备的限定验证

root另独立执行运维候选46项纯发布门禁逻辑、六份PowerShell静态解析及port patch check，均通过，未应用/执行部署脚本。收据`root-static-policy-qa.json`摘要`394f69aa7b9e0fafbe27055abedf2718f489b67369919756551eb6a164b03187`位于私有ops-development。纯布尔门禁最多表示等待操作员审查，不替代可信事实或生产发布许可；独立Web发布器、鲜备份/恢复验证及其余业务门禁仍未闭合。

## 仍须验证

真实父绑定target HTTP全链路、效果撤权/取消与子进程的实际协作、证据导入和受信bridge；完整A20七个剩余案例；B100/C/D与实际页面。当前固定案例覆盖仍13/20，五项PG和46项纯逻辑不增加业务任务数。保持生产数据、密钥、既有测试服务及失败诊断现场；真实供应商联调继续跳过。
