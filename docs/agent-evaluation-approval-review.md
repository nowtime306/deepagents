# 固定审批用例代码审查与工程 QA

日期：2026-11-04；范围仅 W03/W04 新观察/封存模块及两个测试文件，共 5 项冻结文件，未包含共享接线或真实任务。

- AP01/P1：阶段引用在证据保存后另一次写盘，第二次失败可能凭内存别名允许清理。独立纯探针复现；改为阶段别名与证据引用同一次保存，失败统一标记不可消费。
- AP02/P1：原批准响应未知时，远程任务已结束可能进入新的重复决定探针。现在要求原意图 CONFIRMED、路径及参数摘要精确一致，才发显式探针；离线事实和 CLEAN 同样校验原决定/探针意图，不隐式核对后重放。
- AP03/P2：审批前只看 pending/unknown 而未核 native/provider 完整性。现在要求已知、相符的调用与输入/输出用量，且未达本案例预算；不完整或不符时零审批 POST。

最后冻结 `services/evaluation/.local/approval-final-freeze/manifest.json` SHA256 `4ffd7b8f684b13a144b85eedb2f07081d173dd5de0b930d4915cca947f701e76`。三项问题均由非作者复审关闭，18 项独立边界探针通过；随后只执行指定两个测试文件，**53 通过、0 失败、0 跳过**（0.59 秒）。5 项摘要前后一致，未访问网络/真实平台、数据库或 Work。53 含作者先前 39/46 范围，探针与历次通过数不累加。

原 AP01 失败证据 `services/evaluation/.local/approval-independent-review/probe_0c315a6f1376/` 保留。复审及 QA `services/evaluation/.local/approval-independent-qa/run_a4dd2224bced/`。共享上传修订、环境归属、完整 HTTP 驱动、online/offline CLEAN 和最后收据仍需后续审查测试；两个模块存在不代表实际 W03/W04 已验收。
