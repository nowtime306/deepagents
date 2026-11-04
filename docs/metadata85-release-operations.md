# 受限评测管理版本部署记录与操作入口

状态：2026-11-05已发布受限评测管理版本（`zhixing-v0.1.1-metadata.1`）。开发分支为 `onwer_agent`；业务源码固定 `1358d026dd6fba3d4b1cdabb0b5f0e9154897fdc`，351项源码清单SHA256 `8a649adc50177e38ecb7c77b19c89066012c622b9b0b72732e81cf62a1db0af8`。本入口只开放普通管理及原生独立Ask，未验收执行端点和后台继续暂停；不是完整DeepEval业务执行上线。

## 正式入口

必须成对保留 `scripts/production_restricted_c85.py` 与同目录 `production-c85-sources.json`。入口固定原生产 `owner_agent`、回环55432、原data/model.key、API8101和Web来源localhost3100，读取.env时不插值，仅使用原DB/data字段。继承执行开关和凭据环境被清除，模型仍使用原数据库加密配置。入口没有迁移、恢复数据库、删除停止标记、重试任务或替换模型的动作。

命令在项目根目录执行：

```powershell
& .\services\api\.venv\Scripts\python.exe -I -B -X utf8 .\scripts\production_restricted_c85.py check
```

`check`要求精确85迁移、没有活跃执行、维护关闭且无旧停止标记，只读检查成功不等于服务已启动。当前生产79的负控制已实际拒绝，未执行自动升级。部署前需先完成最新一致备份、独立克隆080～085、同库C85→F85→C85的真实Ask/SSE及元数据守恒验证。

## 进程切换

使用原部署admission gate等待全部活动任务结束，核精确进程身份、创建时间及持有的Windows进程句柄；通过属于本次维护的停止标记合作退出。不得按端口/PID强杀。停止后才可归档本轮标记。正式迁移只允许已审080～085，不倒退DDL或用旧库覆盖新写。先启动独立Ask并核真实心跳，再开放API，防止入口开放后出现待执行消息而启动前检查拒绝。后台启动须使用明确输入输出文件、关闭多余句柄继承、隐藏窗口；启动日志不能替代HTTP、进程、心跳和数据库验证。

API停止文件为原data下 `services/api.stop`，Ask为 `services/ask.stop`。原生Ask合作停止的SERVICE_RESTARTING/退出1保留，不修改为成功。当前旧79宿主不支持-I的同目录导入，恢复旧版必须沿其原始-B启动参数；新入口支持-I。

## 回退及延期

85数据库上的应用回退只能使用独立已验F85v4及配对旧HyGL前端，不使用原schema79宿主。F85保留新增评测数据但旧接口可404；文档/记忆/图片向量写入暂停，知识与记忆明确仅关键词检索，不伪造向量校验字段。切回C85后应能继续读取和CAS维护原评测题集及不可变版本。只读回退不等于真实数据库恢复。

浏览器工具对新增页面的本轮访问若仍被实际拒绝，按用户明确要求列为人工回验，不将HTTP或构建冒充页面验收。真实外部模型继续跳过；W05真实修后复测和其余未完成案例另列，原失败不改写、工程测试不累加业务任务。

## 本次最新备份和克隆

2026-11-05已实际完成原79一致备份和全新隔离克隆080～085。克隆收据 `.local/metadata_clone_667d4b3c2d884e3990547014124c517f_test/clone-receipt.json`，SHA256 `a86471ca7b33ae0f61aa16c5bcd46d01a589bd791063ea7a1c7c346bc40b652d`。47表原列保持，仅预期082审计新增；原key摘要保持。克隆尚未进行真实三阶段兼容验收。

备份后原79服务已恢复，root实际HTTP3100、8101ready、18180认证均200，唯一新鲜API/Ask为500/49272，Web仍HyGL。root健康收据 `.local/metadata-maintenance-667d4b3c2d884e3990547014124c517f/root-health-restored.json`，SHA256 `25fa1785593ca39156b84039d5164bc25d78f805a4680b988bb7e438ea68140f`。辅助维护进程在PowerShell启动管道继承处挂起，原SNAPSHOT_PASS仅证明备份，不能代表整段脚本通过；独立Ask已启动，root用明确stdio/close_fds恢复唯一缺席API，原失败和管理进程现场保留。首手工API恢复误带-I在导入前失败，改回原-B后真实恢复，不重做备份或模型请求。后续维护不得复用有该管道问题的自动启动段。

## 2026-11-05实际发布与最终验收

分支`onwer_agent`，业务提交`1358d026dd6fba3d4b1cdabb0b5f0e9154897fdc`；部署入口提交`2e8c9e6bdc8140a2e58135b01562e0f04e060e03`，清单LF固定提交`adb92a537d87f4382ab12e1aed3141690eb964f7`。351项业务源码仍与8a649清单完全一致。受限模式保留普通Ask及管理，未验执行仍拒绝；不是全部DeepEval业务执行上线。

实际组合验收为一次C85→F85→C85同库同会话的三轮冷进程对话：3条SUCCEEDED、3条KNOWN，输入30/输出9；消息和检查点逐轮累计1/2/3，实际TCP SSE原件与DB事件逐条一致。题集及不可变v1跨F85保留，回C85后CAS维护有效；47表原列行摘要多重集、30个原文件和key保持。独立收据SHA256 `8ee694b9baf463b70c2fc510e86a3ac787ee95a8af0a2eeb799a35f06eac1938`。原Origin403、Windows独占锁读取失败保留；测量修正后只续从未提交的两轮，不重发第一轮，工程总上限3。运行时仅vectors/.lock无法读，停止后该锁也完整hash校验；没有笼统排除原文件。

正式最后一致备份成功，旧挂起helper在合作停Ask后自然超时退出，关键锁已释放。最终备份交接收据 `.local/metadata-final-maintenance-a9d6fc64ff3e4155a13ef6ef16411968/receipt.json`，SHA256 `6e33da3f450831f56cb83771b2b2f596139e369208987b8e5b58b46d692cff14`。程序先check再正式080～085迁移，原47表及key保持，082仅预期基线新增。迁移收据 `.local/metadata-production-migration-1358-root/migration.json`，SHA256 `ee353815c63d21ee7ce57eae8982086a786054e11e0e276c1658f7b0fd33db8e`。未恢复旧库或删除新数据。

正式API实际PID74112（launcher38168）、独立Ask55140（launcher61688），均为原生未替换入口；Web实际PID68236，BUILD_ID `Uh42uwaj3WMQ4pK5b9ri7`。旧Web49684通过严格两客户端专属控制台SIGINT合作退出，原native130保留；没有执行TerminateProcess备选或树杀。新Web有专属token/PID停止预加载器，私有身份在 `.local/metadata-web-production-20261105/launch/identity.json`，不公开其中token。

正式3100首页、8101ready、18180认证健康均200，18个页面引用脚本/CSS与固定产物字节匹配。原账号先离线验证原密码未改，再实际经3100代理登录；身份、评测能力、题集/运行列表、对话、智能体和模型列表均200。评测运行提交503明确暂停，模型/业务五类记录计数保持[61,23,0,0,0]，无测试真实provider请求。生产页面代理收据 `.local/metadata-production-start-20261105/production-web-proxy-smoke.json`，SHA256 `7fc0b527b6cd4463d37a5b3401d6d706fe625d4e5f908255a5eecb72b4dbacac`；实际综合健康收据SHA256 `607588a4b7bfba81e45e971211982744a24bede71ebae8e00166f661884bd9a0`。这不是浏览器点击验收。

## 待用户人工验收及仍未开放项

- 刷新`http://localhost:3100`，以原账号登录，查看评测管理列表、新增题集、编辑、发布v1、历史版本和权限提示；实际浏览器工具页面操作仍按用户要求延期，HTTP/构建不冒充点击通过。
- 真实外部模型、真实联网/多模态质量与费用继续按原要求跳过；本次三轮是明确受控模型。
- W05真实修后复测、A05/W06/W07/W08/F05/F07等剩余真实业务证据、完整持久评测驱动与B100/C/D门禁未闭合。A20仍13/20，不因工程测试或本次三轮增加；Work、Workflow、自动任务和完整DeepEval执行保持暂停。原失败及额度预留均保留，不自动重放UNKNOWN。

后续进程身份以当前CIM/原生创建时间/心跳和私有启动原件重新绑定，上述PID仅作本次发布记录，不能作为以后结束进程的依据。schema85回退仅切已验F85v4和配对HyGL应用，不能再运行原79宿主或恢复旧数据库。

## 独立上线复核与运维交接

测试最终独立只读核验通过：实际生产85、最新快照47原列/原密码哈希/key守恒、082基线恰4、业务计数与模型用量均不增；实时3100/8101返回200、API/Ask/Web精确进程及18已服务资源复核通过。收据 `.local/team-workspace/agent-evaluation/qa-work-extra/production85_readonly_release_0404a6f8d05b4ea7825447d70a26ebc6/independent-release-receipt.json`，SHA256 `eeba919a1a9b7f76cfec71caf04865f46cd166b1e71d9a5ae0b4641ff0ebb0b4`。非作者没有再登录/发模型任务或改DB。

运维独立验真最新39备份文件、351业务+依赖/操作入口、727固定Web文件、18已服务资源及停止原件；私有交接手册 `.local/metadata-production-release-handoff-ops-20261105-v1/runbook.md`，SHA256 `4121a93656e08ac7b7cc9cd7eb1b538a7aeb7880eff87c18a7aa574f9e7d5318`；其内容验真收据SHA256 `7447872586695c654951c8afea8131d63a606b1062453e3db92c0672076908be`。运维工具本身看不到主机OS/PG的限制明确保留，实时健康由root和测试真实复核，不混为该角色亲测。

普通使用地址为 <http://localhost:3100>，沿原账号密码。新Web的私有停止token仅在本机身份文件中，交接文档不公开；后续需要停止时使用该token/PID创建专属停止文件，由本进程既有Next处理器清理，不按历史PID结束进程。其他3131/8100测试服务及18180管理服务保持，其他18个Docker容器没有重启或变更。
