# 智能对话 SSE 前端开发记录

2026-11-02，按用户最新要求将范围限定为智能对话。任务工作台、工作流维持本轮前的 HTTP 刷新和同步测试方式，不改造为 SSE。

## 保留的智能对话行为

followRun/decodeFrames 保留既有调用入口；Fetch 读取 UTF-8/CRLF 分包，按持久事件序号续传去重，快照等控制帧不推进游标。取消会停止读者与退避，401/403/404/422 不重试；snapshot-only 断线不会无限重连。新协议接收最终快照和 stream.end 后结束；完全无控制帧的旧协议仍兼容终态加 EOF。

Ask 快照 context 提供运行状态、诊断、用量与团队进度，活动会话使用推送，历史按需读取；文本增量按动画帧合并，保留现有页面与会话切换取消机制。控制帧不会伪装成正文；不伪拆最终答案。

## 已撤回的跨页面变更

恢复来源：services/api/.local/backups/20261102T114245Z-875e1c0c/source.zip；已比对 20261102T113819Z-9373feae/source.zip，目标文件内容相同。当前撤回前文件另存 .local/team-workspace/unified-sse/frontend-before-scope-withdrawal。

按备份原字节恢复 work-run-panel、workflows、workflow-execution、workflow-run-trace、token-usage，及 platform、repositories、workflow-node-debug、workflow-forms、workflow-studio 五个既有测试文件。Work 就绪状态、审批、成果、恢复、用量和节点测试均回到原来的查询方式；工作流 POST 不再传 stream=true，继续使用原同步 200 行为。

共用 run-diagnostics 和 agent-team 仅恢复 Work 的活动刷新，保留 Ask 的推送快照。移除仅用于 Work/Workflow 的 run-history-merge 工具、无引用 SSE 页面夹具及其专用测试；保留 Ask 传输、分包、重连、权限和取消回归。不覆盖其他既有功能，不修改 Next 配置、服务进程、用户数据或密钥。

## 验证边界

作者须通过 TypeScript、Ask 传输测试、恢复文件逐字节对比和定向 ESLint 后冻结；独立审查通过后再交 QA。ReadableStream 自验不证明真实 Next 代理或浏览器已流式，候选构建与浏览器验收由审查后的 QA 执行。真实外部模型仍按用户要求跳过。

作者收窄后自验：8 项 Ask 传输测试通过（12.7 秒），TypeScript 通过；定向 ESLint 0 错误、1 条既有 effect 警告。上述 10 个恢复文件逐字节与备份一致。生成的 test-results/sse-qa-legacy 旧 SSE 测试备份按协调要求移至 .local/team-workspace/unified-sse/qa/withdrawn-legacy-generated，保留其内容，避免它参与 Web 编译。RunSnapshotContext 限定 mode=ask，provider 仅包智能对话分支。platform.ts 原被 gitignore 排除、备份包未收录，保留原有 api 方法及本轮 Ask 专用读取器修复，没有用整文件基线覆盖。最终作者冻结，等待独立审查与 QA。
