# A01 与审批共享链路审查及工程 QA

日期：2026-11-04。基础冻结 `a01-shared-freeze/manifest.json` SHA256 `6537f60ee34ca17f6fd5da0c782953399a83b521e2d0c9a270ffc461b22a9555`；修复冻结 `a01-shared-review-fix/manifest.json` SHA256 `e1cbcdae94fb72f789baacbf74846d4d26daabf415a9803a980e6f9c57e601df`。独立审查与 QA 均非实现作者，Root 的审批模块依赖未改。

- A01-S01/P2：原提交、启用或调度意图被改为 UNKNOWN，离线报告仍接受 CLEAN。逐阶段及离线增加 CONFIRMED、精确路径/参数摘要、提交返回根绑定，原未知不得借后续结果通过。
- A01-S02/P2：早期 provider 用量不自洽可被最终正常结果掩盖。启用与单次调度前、离线复算均要求唯一原根 3 次/45 Token、原生输入30/输出15及无 pending/unknown。实际 enforcing provider 本身不会在 pending 时给 complete=true；问题是留存事实的独立一致性校验不足，不夸为已证在线预算绕过。

两项经原 6 个独立反例与 3 个阶段 HTTP 替身反例修复复审关闭。共享上传修订、固定动作审批、namespace、新消费者接口、无指标审计阶段及旧 Agent 发布版/新模型实际修订的源码接线已静态审查；Root 另核 W04 控制器只在已知静止审批状态合作停止并重建真实 WorkWorker、使用同原根和 PG saver，不是 OS 进程重启。

闭审后独立客户端指定 22 文件 **310 通过、0 失败、0 跳过**（20.47 秒）；API profile 指定 7 文件 **69 通过、0 失败、0 跳过**（5.56 秒）。两组分别记录，不相加为任务数，不累计先前 291/67 等。244 项源码/测试/配置和 21 项冻结/依赖摘要前后及最终一致。

证据 `services/evaluation/.local/a01-shared-independent-qa/run_8a1304d61817/`，QA Markdown SHA256 `1ce96c1cdfcb70f42f8afed7880f16ed183a98597bb897489ab6a40b1c06e129`，完成收据 SHA256 `b81f251db839b19d9ab7aa5c1d85dd94d85470ae2f6d9f5291c82016c73ac25c`。API 首次 fixture scope 配置警告保留，私有 runner 显式 function scope、警告作错误后重跑最终无警告；未改产品或断言。

网络范围：客户端父进程无网络事件；既有网络专项子进程自建 loopback 并验证外连拒绝。API 仅 53 个 Windows 标准库 asyncio 私有 socketpair 本机连接，不能描述成全程零网络或 OS 出站证明。没有真实数据库/Agent/沙箱/页面/供应商或生产部署；Java/W02 未接入该批，真实 A01/W03/W04 仍待提交候选后执行。
