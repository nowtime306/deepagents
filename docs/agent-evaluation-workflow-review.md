# 固定 Workflow 事实与回收审查

日期：2026-11-04。范围：F02 分支/变量、F04 输入拒绝的正式只读工作流接线。根角色非作者静态审查；不替代独立工程 QA、真实任务或页面验收。

## 修复与复审

基础冻结 `fixed-workflow-freeze/manifest.json`（SHA256 `7d427a2221221b5a6dff101ad681f6c965128925082ae4d9b8c8ed242590968b`）的正式版本读取、原生事件/用量、输入拒绝和初始化基线已按实际 API/编译器源码核对。只读流程通过正式 HTTP 同步执行并持久化，不称为可执行工作流队列验证。F04 先封存非法输入的 422、字段错误和零副作用，再提交合法对照；未知写入不重放。

发现 P1：清理只比较当前与保存的副作用快照是否相等。稳定快照仍可能含活动任务或未知操作，任务判为 FAIL 却被标 CLEAN，进而允许回收数据库。

修复冻结 `fixed-workflow-review-fix/manifest.json`（SHA256 `4da73561fc4a182afb2731c620c7a9b726e3e5b2e18115cde2772a1bb7392908`）中 `fixed_workflow_evidence.py` SHA256 为 `851f79d11cc70a1296fb0c0b8e6be476519b81a5fc792c885967fa0f470c0a2f`。复审关闭：`verified` 先检查完整、归属匹配的 provider/sandbox 原始事实；`cleanup_ready` 要求活动运行/沙箱、未知操作及 PENDING/UNKNOWN 模型用量为零，provider 无未决调用，沙箱预约具有会话登记。在线清理同时核当前快照相等和该门禁；离线 Stored CLEAN 使用同一门禁。已结束、已知的业务失败仍可回收，不把业务 FAIL 改为 PASS。

共享接线另由独立角色复核：W01 Stored CLEAN 和执行前 namespace 归属保护，两项关闭；9 个最小独立探针通过。随后完整客户端独立 QA 为 204 通过、0 失败、0 跳过（10.52 秒），含本修复 20 个回归及 Work 39 项，不与旧批次或 9 探针相加。17 个测试文件和 46 项选定源码/测试/契约摘要前后一致。证据 `services/evaluation/.local/shared-workflow-independent-qa/run_6e09861bc5e5/`。该工程集使用协议替身和受控本机连接，没有运行真实三例、数据库、Work、页面或付费服务。

能力清单据此仅新增 W01/F02/F04，与新 initializer 摘要绑定。每例沙箱保守预约上限仍为 2，与 `describe` 一致；两个只读 Workflow 实际预期创建 0 个沙箱，不把预约数当作实际资源消耗。真实三例须提交候选后另行验证。
