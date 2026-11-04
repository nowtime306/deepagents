# 当前稳定核心运行说明

2026-11-04 核对。当前访问地址：<http://localhost:3100>。

## 当前版本与可用范围

生产恢复源为 `2818e6f647093a1e8ad1695139f9fdaf4ace8ab7`、数据库 schema79。登录、已有模型/智能体/知识库等管理，以及独立 Ask/SSE 已恢复。Work、工作流、自动任务执行及新版 DeepEval 消费者仍暂停。当前开发分支的提交不是生产版本，不能直接运行通用部署命令替换现服务。

独立语音、图片生成/理解、OCR 和视频作业等尚未恢复执行。Ask 对话内图片附件通道保留，实际处理取决于所选模型能力，真实外部模型质量尚未联调。

本次恢复保留原数据库、加密密钥和用户手动变更；新备份实际独立恢复校验通过。详见 [恢复验收](agent-evaluation-report-recovery-qa.md)。

## 服务与已固定材料

- Web：3100；实际构建 ID `HyGL44qVqpT7tJ3XzOTyS`，服务目录为 `.local/team-workspace/rdc/production-repair/baseline-source/apps/web`，产物目录 `.next-login-recovery-20261104050114`。
- API：8101；固定入口 `.local/core-restoration-ops-v2/production_host.py serve-api`。
- Ask：同一固定入口的 `serve-ask`，独立消费者，无监听端口；以数据库实际新鲜心跳核对存活，不能只看启动器进程。
- 环境管理服务：18180；沿用 `services/sandbox/windows.toml`、现有密钥、SQLite 和已安装解释器，不生成新密钥或管理实例。
- 8100/3131 属于已有独立测试服务，不接管或停止。

固定入口清单 `.local/core-restoration-ops-v2/manifest.json` 的 SHA256 为 `dfbb021bb032aeef38f5c1f483e2531089d6bc34733e56126d2637b49250577c`。入口会核验原 API 源清单和 598 文件快照，禁止以当前 HEAD 替代固定源。关键材料目前位于 Git 忽略的 `.local`，不能清理；依赖环境也必须保留，源码摘要不代表任意依赖升级都安全。

## 日常检查和重启边界

日常只读检查首页、API ready、管理服务认证响应和 API/Ask 心跳。本批前后均为 200，原进程身份、端口归属及活动构建所有文件摘要不变。此状态记录不保证以后永远在线。

需要恢复服务时，先核当前进程完整命令、创建时间、端口及实际心跳；已健康则不重复启动。原 `start-stable-core.ps1` 是一次性恢复编排，并非幂等启动器，盲目重跑可能产生第二个 Ask 消费者。完整重启须先封入并排除旧活动/未知任务，保留现场；只按本次维护归属处理停止标记，不批量清空队列或重放未知操作。

固定 Ask/API 解释器为 `services/api/.venv/Scripts/python.exe`，工作目录为项目根，后台启动应隐藏窗口并使用新的独立日志。启动顺序及维护锁应沿本轮已审流程；需要停止时只操作核实身份的进程和本次维护标记，退出须用持有句柄及心跳独立核实，不能按端口杀进程。Ask 合作停止的原生 `SERVICE_RESTARTING`/退出 1 已有专门测试；不能把任意退出 1 当作正常。

当前通用 `start-platform.ps1`、`deploy-platform.ps1`、`start-managed-service.ps1` 会面向当前源码、迁移或 8100，不能用于本恢复形态。通用停止脚本也不能仅凭旧 PID 登记证明固定 wrapper 已退出。新构建 `Uh42uwaj3WMQ4pK5b9ri7` 已通过构建，尚未服务或部署；不得误当活动 HyGL 产物。

## 后续运维待办

目前没有注册新的 Windows 服务、开机任务或自动看护，电脑重启后不会据此自动恢复。受管幂等启动器和固定发布材料的长期保存仍待单独开发、审查和隔离重启测试；本记录不把它们标为完成。新版本的迁移及回滚还需独立验收，不能承诺把 schema79 wrapper 当作迁移后的自动回滚器。

详细只读运行手册及来源摘要位于 `.local/stable-core-operations-handbook-ops-v1/`，该清单 SHA256 为 `f7908ef9343af2ebd70ffc670c6f68bd82614b5d67757bf257805633d5b91a49`。本批仅检查和记录，未再次启停生产服务。
