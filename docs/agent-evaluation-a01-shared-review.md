# A01 与审批共享链路审查及工程 QA

日期：2026-11-04。基础冻结 `a01-shared-freeze/manifest.json` SHA256 `6537f60ee34ca17f6fd5da0c782953399a83b521e2d0c9a270ffc461b22a9555`；修复冻结 `a01-shared-review-fix/manifest.json` SHA256 `e1cbcdae94fb72f789baacbf74846d4d26daabf415a9803a980e6f9c57e601df`。独立审查与 QA 均非实现作者，Root 的审批模块依赖未改。

- A01-S01/P2：原提交、启用或调度意图被改为 UNKNOWN，离线报告仍接受 CLEAN。逐阶段及离线增加 CONFIRMED、精确路径/参数摘要、提交返回根绑定，原未知不得借后续结果通过。
- A01-S02/P2：早期 provider 用量不自洽可被最终正常结果掩盖。启用与单次调度前、离线复算均要求唯一原根 3 次/45 Token、原生输入30/输出15及无 pending/unknown。实际 enforcing provider 本身不会在 pending 时给 complete=true；问题是留存事实的独立一致性校验不足，不夸为已证在线预算绕过。

两项经原 6 个独立反例与 3 个阶段 HTTP 替身反例修复复审关闭。共享上传修订、固定动作审批、namespace、新消费者接口、无指标审计阶段及旧 Agent 发布版/新模型实际修订的源码接线已静态审查；Root 另核 W04 控制器只在已知静止审批状态合作停止并重建真实 WorkWorker、使用同原根和 PG saver，不是 OS 进程重启。

闭审后独立客户端指定 22 文件 **310 通过、0 失败、0 跳过**（20.47 秒）；API profile 指定 7 文件 **69 通过、0 失败、0 跳过**（5.56 秒）。两组分别记录，不相加为任务数，不累计先前 291/67 等。244 项源码/测试/配置和 21 项冻结/依赖摘要前后及最终一致。

证据 `services/evaluation/.local/a01-shared-independent-qa/run_8a1304d61817/`，QA Markdown SHA256 `1ce96c1cdfcb70f42f8afed7880f16ed183a98597bb897489ab6a40b1c06e129`，完成收据 SHA256 `b81f251db839b19d9ab7aa5c1d85dd94d85470ae2f6d9f5291c82016c73ac25c`。API 首次 fixture scope 配置警告保留，私有 runner 显式 function scope、警告作错误后重跑最终无警告；未改产品或断言。

网络范围：客户端父进程无网络事件；既有网络专项子进程自建 loopback 并验证外连拒绝。API 仅 53 个 Windows 标准库 asyncio 私有 socketpair 本机连接，不能描述成全程零网络或 OS 出站证明。没有真实数据库/Agent/沙箱/页面/供应商或生产部署；Java/W02 未接入该批，真实 A01/W03/W04 仍待提交候选后执行。

## 真实执行发现的引用修订类型修复

候选 `4627ea03e072d5f8b22c495f9f72b5f0754bd7e9` 的新隔离 A01 源 Ask 已真实成功，维护后模型实际调用 3 次、输入30/输出15 Token；但资源快照的引用 `revision` 是 PostgreSQL TEXT `"2"`，客户端错误要求整数2而阻断。原生调用 `model_revision` 则确实是整数2，不能将两种合同混为一谈。正式暂停草稿未启用、occurrence0，W03/W04未派发。旧实例已合作停止，库和原证据仅保留诊断，不能称 CLEAN 或 A01 完成；run/cleanup 的 ERROR 审计使旧 init ready 失效，拒绝旧收据是正确行为。

最小修复冻结 `a01-text-revision-fix/manifest.json` SHA256 `4f939c77ae686a4d315f8c8a9e10cd73854bdaa9795fbd7c7a0b51c2d6be5ba9`，严格保留引用文本与原生整数，逐调用交叉校验，不做宽松字符串转数值。独审及19个类型探针关闭；完整客户端独立 **328通过、0跳过**（26.31秒，含旧310，不累加），新增真实PG/API投影契约 **1场景通过**（6.83秒），新库已精确回收。该场景验证正式发布/维护/受控Ask/SSE/UsageRecorder/trace和实际SQL字段类型，不代表完整自动任务生命周期。

证据 `services/evaluation/.local/a01-text-revision-independent-qa/run_7be5d6492acb/`，报告 SHA256 `c2db5a02290bdd28c351d4cf2d008ab2bbfcd071724f103a6265270508141391`，完成收据 SHA256 `ad787b0f72043f0761870eb5e70138f9d6fd0b725a16352238efc5ad6c531d14`。247项来源摘要一致；Python4次标准socketpair与libpq真实本地PG连接分别说明，不称全程零网络。旧库/报告没有被连接续跑或重评。

旧批次 `.local/agent-evaluations/suite-a01-w03-w04-4627ea0-82d19eac/` 的独立保留核验为 STOPPED_DIAGNOSTIC_DATA_PRESERVED_NOT_CLEAN：SQL和审计表明无继续运行副作用、精确PID已退出、namespace容器0。详见该批 `blocked-diagnostic.json`（SHA256 `a6a439555c728ba19edaafb63d6c612a741bb8ee71813b6bd0c85a0233ec4afe`）与 `blocked-preservation-receipt.json`（SHA256 `d54bc2be394fa2efc625b8f7216761aad793ce2b0de563ecb352d6617692d3b9`）；不重导出、重POST或放宽旧ready。

另：69项工程QA后、4627提交前仅修 `test_automations.py` 多余末空行的最后CRLF两字节，Root和独立角色均核完整字节边界及含位置AST一致，保留原bytes与 `a01-test-eof-fix` 清单。测试期间原摘要不回填为新摘要，产品代码没有因此改变；并非新的业务测试。
