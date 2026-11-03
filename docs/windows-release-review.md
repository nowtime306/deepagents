# Windows 发布源码换行兼容性独立审查

日期：2026-11-03。范围仅 `scripts/platform_release.py` 及 `services/api/tests/unit_tests/test_platform_release.py`，依据 `.local/team-workspace/workflow-version/windows-release/delivery.json` 冻结的两项摘要。本次只作静态增量审查，不改业务代码、Git 配置或 Git 状态，不运行部署。

结论：**审查通过，可交独立单元回归。** 没有发现可证实的新阻断问题；此结论不代表页面验收或正式发布通过。

## 问题与最小修复

原假设“纯 CRLF 检出必然失败”已由 `first-failure.log` 的 1 passed 反证，不能继续当作故障证据。有效反例是启用 autocrlf 的仓库同时存在已检出 CRLF 文件和新增 LF 文件，旧 complete 对已冻结的合法工作区产生摘要不一致；`genuine-mixed-failure.log` 保留 1 failed 的原始失败。

本次 prepare 仅对自身的 `git archive` 子进程传入 `-c core.autocrlf=false`，避免归档时 Windows 检出转换影响规范包；不写仓库、全局或系统配置。complete 保持归档摘要和 QA `source_sha256` 全量字典严格相等，另读取 QA `worktree_sha256` 的完整原始文件摘要。

每个实际文件首先必须与 QA 原始字节摘要完全一致；之后只有原始字节等于归档，或无 NUL、可严格 UTF-8 解码且包含 CRLF 的文本仅将 CRLF 替换为 LF 后等于归档，才能通过。没有进行 trim、BOM 删除、任意字符编码猜测、正文改写或通用二进制规范化。缺少新 raw 清单时回退到规范摘要，意味着原先字节完全相同的旧证据仍有效，不能凭缺项放行有换行差异的文件。

## 门禁保留检查

- RS-01 保留：规范 QA 与归档完整文件映射相等；当前 tracked/untracked 非忽略业务文件集合相等；新 raw 清单也必须完整且集合精确相等。逐文件仍检查存在、链接、归属与原始摘要。额外文件、漏文件、QA 后修改不放行。
- RS-02 保留：QA status 和 browser 均须 passed；部署须 full/rebuilt/complete/deployed，时间不得早于准备记录，正式 API/Web/迁移健康状态仍逐项检查，标签仍须固定到归档提交。
- RS-03 保留：ZIP 路径、符号链接、大小写冲突成员、私密文件与扩展检查不变；正式包之前的私有 staging 校验和失败清理机制保留；旧发布记录不得覆盖。
- 新增 Windows 用例覆盖混合换行合法场景、raw 清单缺失/缺项/额外项、QA 后改动、正文、尾随空白、BOM、含 NUL 二进制与非 UTF-8 内容。后二者即使同步修改 QA raw 摘要也不能靠换行归一化绕过归档校验。原有拒绝部分 QA、后端单独部署、旧部署、额外源码、篡改 ZIP 和危险路径的测试仍保留。

## 冻结与验证边界

独立摘要留档：`.local/team-workspace/workflow-version/windows-release/reviewed-source.json`。脚本 SHA256 `ffbb34ce0e376a02eca0cdebefbac3b382ddbb9de8b6829f3e65874a29498f5e`，作者测试文件 SHA256 `89588ba3ba5c0a8bdb8f57424ac2ed8358ced431f08dcf34c8987937300a01b0`，均与 delivery 一致。

作者提供 26 passed；本次审查不将作者结果算作独立 QA。下一步应由测试运行该 26 项与原 5 项独立用例共 31 项。没有为此执行 Git 命令、真实服务、页面、生产发布、正式 tag 或部署。
