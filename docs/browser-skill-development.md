# BrowserSkill 平台集成开发说明

后续实现/真实专项的最新状态以 [后端接口与运维交接](browser-skill-backend-contract.md) 为准。本文保留初研方案背景；bundle路径和v3 redact行为已依据实际扩展录制更正，原“待验证”清单是初研时点，不能用于替代最新验收记录。

状态：2026-11-03 技术调查与依赖准备完成；正式 API、UI、migration 尚未实现。工作流版本功能使用 migration 080，本功能暂预留 081，须由集成负责人确认后才能落库。所有现有账号、数据、配置和密钥保留。

## 最小交付及验收

用户在明确选择的 BrowserSkill 独立浏览器环境中开始录制，人工完成网页操作并结束，平台显示真实动作预览；用户确认参数和目标后生成 Skill 草稿，再显式发布至现有 Skills。Agent 通过平台受控 adapter 执行发布版本，继续经过 Work 浏览器审批。发布本身不执行网页动作。

1. 固定并校验官方 CLI/扩展 → 验证版本、发行资产哈希、上游源码。
2. 管理录制生命周期并规范化语义轨迹 → 验证真实扩展产出包含目标描述和动作值，敏感值不进入平台持久化或日志。
3. 预览、编辑参数、草稿、发布和撤回 → 验证 owner 隔离、不可变版本与现有 Skill 选择。
4. 受控重放 → 在新 fixture origin 和独立 profile 验证输入、选择、点击及页面结果；审批拒绝、目标歧义和人工介入必须停止。

不把 MP4、operation audit 或缺少输入/目标的调试摘要称为可复现轨迹；不调用外部模型完成验收，不访问真实用户登录动作或生产网页。

## 官方来源与固定依赖

官方仓库：[Tencent/BrowserSkill](https://github.com/Tencent/BrowserSkill)。源码研究快照 `7840c46e956ffca61372d5bd2ad674cd85403ddf`；实际 CLI/扩展均选择官方 0.4.1。`cli-v0.4.1` 与 `ext-v0.4.1` 解引用后的 commit 均为 `b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5`。研究快照与发行 commit 的 record CLI 和 v3 protocol 文件无差异。

- [Windows x64 CLI 发行包](https://github.com/Tencent/BrowserSkill/releases/download/cli-v0.4.1/bsk-v0.4.1-x86_64-pc-windows-msvc.zip)：SHA256 `f4a62af2dde31508188ea0c80feee11dbb62d9e7b7bf388f3f3a9856cfffb37d`。
- [Chrome 扩展发行包](https://github.com/Tencent/BrowserSkill/releases/download/ext-v0.4.1/browser-skill-extension-v0.4.1-chrome.zip)：SHA256 `75e38414e732679b7fe7616de9f3bb80dbb9cfcc55d9cab2566dbcebb4cd9b51`。
- 两包已下载、与 GitHub Release API `asset.digest` 比对一致并解压到 `.local/team-workspace/browser-skill/backend/` 下 `bin/`、`extension/`；原包位于 `artifacts/`。CLI 实际执行 `--version` 输出 `bsk 0.4.1`，`record start --help` 与源码接口一致。这里的校验是官方发布摘要比对，不声称有独立签名认证。
- Windows 10 / Server 2016 及以后，x64；扩展 manifest 最低 Chrome 125、MV3，需要 debugger、tabs、scripting、storage、webNavigation 等权限及 `<all_urls>`。只在任务专用 profile 加载，不能自动复用用户日常 profile。
- 无需本地 Rust/Node 编译即可使用上述发行包。源码构建采用 Rust stable（workspace MSRV 1.85）、Node.js 22、pnpm 10.17.0、锁文件；此次未执行源码构建。
- 上游 [MIT License](https://github.com/Tencent/BrowserSkill/blob/b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5/LICENSE)，Copyright (c) 2026 Tencent。分发 CLI、扩展或复制源码时携带原版权和许可，不把 `.local` 二进制提交至源代码。

## 真实上游机制

权威实现：[record CLI](https://github.com/Tencent/BrowserSkill/blob/b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5/crates/bsk-cli/src/cli/record.rs)、[录制协议](https://github.com/Tencent/BrowserSkill/blob/b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5/crates/bsk-protocol/src/tools/record.rs)、[v3 数据类型](https://github.com/Tencent/BrowserSkill/blob/b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5/crates/bsk-protocol/src/tools/record_v3.rs)、[扩展事件捕获](https://github.com/Tencent/BrowserSkill/blob/b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5/apps/extension/src/content/record-capture.ts)。

`bsk record start --browser <instance> --url <explicit-url> --output <private-directory> --json` 自行创建聚焦 Agent session，发 `tool.record_start`，随后阻塞等待 `tool.record_await`，最长 24 小时。人工点击扩展“结束”或另一进程执行 `bsk record stop --output <same-directory> --json` 才导出。结束后 CLI 自行停止 session。没有 `bsk record await` 子命令，`record start` 也不接受现有 `--session`；`record stop` 从相同 `BSK_HOME/record-session.json` 找 session，不能把它当无状态并发命令。

底层 RPC 可分开 start/stop/await：start 参数为 `session_id, tab_id?, url?, purpose?, max_page_tokens?, redact_values?, trace_version?, supports_tab_switch_steps?`，返回 `{tab_id,recording}`；stop 参数为 `{session_id}`，返回 `{trace}`。CLI 请求 `trace_version:3`、`supports_tab_switch_steps:true`，省略版本的协议调用默认 v2。旧扩展可降级 v2，平台 MVP 应明确报告版本不支持，不能悄悄假装 v3。CLI 的 `--tab-id` 仍受新 session 所有权检查，不代表任意已有用户 tab 可直接借用；MVP 选新 session 的专用 tab，借用已有 tab 后续另议。

CLI/daemon 通过本机 IPC（Windows named pipe）通信；扩展通过本机 WS 连 daemon，默认端口 52800。新功能的 daemon 必须受平台监督，固定私有 `BSK_HOME`、私有端口、`BSK_AUTO_START=0`、`BSK_AUTO_UPDATE=off`，先运行 foreground daemon 再连接专用扩展；不接现有用户 daemon。扩展 UI 需在专用 profile 明确启用本机连接。多 owner 部署不得共用同一 profile/record-state；MVP 每个已注册本机连接仅一个活跃录制，第二个返回 busy。

### Trace 与 bundle

Wire v3 envelope：`version:3, purpose?, started_at?, recorded_at, stopped_by, entry:{start_url}, recorder:{bsk,vom}, states:[], steps:[]`。

- State：`id,url,title?,body,truncated?`，body 是 VOM 页面观测。
- Step：`id,op,state,result:{state}`，`state` 和 `result.state` 引用前后观测。
- `navigate` 含 `to,cause`；`click/hover` 含 target；`fill` 含 `target,value,commit,redacted?`；`select` 含 `target,selection:[{value,label?}]`；`press` 含 `key,modifiers?,target?`；`scroll/switch_tab` 无可直接重放的距离/tab 目标参数。
- Target：`ref?,role?,name?,ctx?,unmatched?`，没有持久 CSS selector。ref 只对录制时观测有效，不能拿到新会话直接执行。role/name/ctx 是不可信页面内容；不能成为系统指令或授权。
- CLI 磁盘导出不同于 wire：`<output>/trace.json` 中 state 使用 `page:"s1.txt"（相对 states 目录）` 代替 `body`，实际 VOM 外置文本。Importer 只能读取服务端生成的目录和严格 `states/sN.txt`，禁止客户端传路径、相对上跳、绝对路径、链接和任意文件引用。
- password fill 在扩展层变成 `"***"` 并标记 redacted；实际 v3 reducer 在 `--redact-values` 开启时也把普通 fill 值替换为 *** 并标记 redacted，同时省略 select.selection；为区分普通输入与密码，本方案不传此 flag，原值只允许私有临时 bundle/内存存在，导入参数化后清理，绝不持久化至平台。上游恢复导出失败会暂存完整 `BSK_HOME/record-recovery.json`，不应遗漏清理。

### 调试、审计和录像的区别

[Website debugging](https://github.com/Tencent/BrowserSkill/blob/b8f2d4f0fa35c8c7c866229e1cf3b3329ae39de5/docs/website-debugging.md) 有 `debug start/operations/operation/request/stop/export`，可关联 manual operation、字段值、请求、console 和前后页面，目的是问题证据。它保存于扩展 profile，可能保留敏感网络数据，不是本功能默认录制接口。operation audit 排除输入值、页面正文和截图；video 是 MP4。二者均不能代替 semantic record。最小方案不额外开启 debug/audit/video。

## 平台架构与拟定 API

新增小型 `BrowserSkillService` 和固定版本 CLI adapter。API 身份从现有 account/session 获取；每次读取、停止、预览、发布、运行都以 owner 条件查数据库。前端只使用平台 ID，不能指定 executable、BSK_HOME、profile 路径、端口、命令或输出目录。已有 `skill_library.py`、Skills 存储/权限、`capabilities.py` 选择机制和 Work journal 是复用点，不另建全功能 Skills 市场。

以下为待产品冻结的协议，不表示已有这些 endpoints：

- `GET /api/browser-skills/status`：依赖版本、已注册专用连接、在线/忙碌与不敏感错误；不枚举用户其它浏览器。
- `POST /api/browser-skills/recordings`：`{connection_id,start_url,title}`；返回 202 `{id,status:"starting"}`，后台管理 `record start` 子进程。start_url 显式、http(s)、无凭据并符合管理员与任务允许 origin。生产策略不因 fixture 允许 localhost 而放宽。
- `GET /api/browser-skills/recordings/{id}`：状态 `starting/recording/stopping/review/failed/cancelled`，规范化步骤、参数定义、阻塞原因；不返回原始输入、VOM、磁盘路径或进程日志。CLI 无即时 start JSON，因此 recording 状态必须通过受控状态检查确认，不因进程存活就声称已开始。
- `POST /api/browser-skills/recordings/{id}/stop`：202，幂等；仅停止本记录管理的进程/session。导出成功并规范化后才进入 review；导出失败保留 failed 原因，不捏造空轨迹。
- `PUT /api/browser-skills/recordings/{id}/draft`：仅允许标题、描述、受支持步骤选择、参数名/说明、经过校验的定位描述；带 revision 防止覆盖。所有 fill/select 动态值默认变为参数，原始值不作为默认值。缺失/敏感输入必须执行时重新提供；password/OTP 等步骤标注人工处理，不能储存录入凭据。
- `POST /api/browser-skills/recordings/{id}/publish`：`{expected_revision}`；原子生成现有 Skill 的明确版本及只读 trajectory snapshot，返回 `{skill_id,version}`，重复请求不得复制版本。草稿不可被 Agent 选中。
- 删除草稿/录制清理平台规范化证据和其服务端生成的暂存文件；发布 Skill 的撤回复用现有机制，运行中冻结版本不随修改漂移。

预留 migration 081：owner-scoped recordings、受控连接标识、状态/revision/error code、规范化 trajectory、draft 与现有 skill/version 引用、时间字段；不保存临时绝对路径到客户端可见字段，不保存密码或未脱敏原始 trace。正式字段应在读完现有 Skills 写入和版本逻辑后确定，避免重复造表。

## 规范化与受控执行

不能把任意录制直接当脚本执行。首次只承诺可验证的一小组：navigate、具有明确目标的 click/fill/select、受限 key；hover 仅在真实 fixture 验证后启用。scroll、switch_tab、target.unmatched、截断导致无法确定目标、无法唯一重定位的步骤必须阻塞发布或标记需要人工修复，不能无声跳过。录制动作的重放前提必须完整，不以“删掉不支持步骤”声称流程成功。

参数化后的标准步骤仅含 op、允许 URL/origin、role/name/ctx 定位描述、参数引用和审核说明；不保留录制 ref 作为执行 target，不保留任何原始 fill 值。原始 trace/VOM 不进模型上下文。删除临时 bundle/recovery 时必须验证绝对路径位于本服务独立暂存根，只清理本任务文件，不删除其它会话或现有数据。

执行入口应为固定 tool（例如 `run_browser_skill(skill_id,version,parameters)`），从 owner 已发布 snapshot 载入动作；不能接受用户自由传入 CLI 参数/脚本。使用参数数组的子进程调用，不使用 shell=True、PowerShell/cmd 拼接、eval 或任意 JS。每次解析 fresh `cooperate sync` 观测，将 role/name/ctx 与当前 VOM 匹配；必须唯一匹配才使用当前 ref，带该次 context 和 `--expect` 执行动作。scope/context 失效、人工接管、页面跳转后重新同步。

平台审批仍由现有 Work journal 授权实际 browser operation；录制/发布不授予执行权限。待审批展示具体发布版本、目标 origin、参数摘要和预期动作，敏感参数遮盖。拒绝审批时不得调用 bsk。提交、付款、删除等高影响动作需要原有明确授权，不能因为“已录制”自动通过。出现 effect_state unknown 先查看当前页面结果，不能自动重试可能已生效的写操作。取消、异常和任务结束均清理自己创建的 session；不得关闭/改变用户其它 tab。

## 已验证与待验证

已验证：官方仓库和源码、release/tag commit、官方 CLI/扩展下载摘要、CLI 实际版本与帮助、manifest 版本/权限、语义录制数据结构、wire/bundle 差异、CLI 阻塞生命周期、恢复文件及脱敏限制。没有执行真实外部模型。

待验证，不能冒称已通过：Chrome/Edge 独立 profile 加载官方扩展；扩展与本机 daemon handshake；真实人工输入产生 fill/click/select trace；UI stop 导出；新 session 通过 adapter 重放获得真实页面结果；平台鉴权、发布、审批和迁移。协议替身/单元测试不能代替这些 E2E 项。

测试分层：

1. 无网络 importer/参数化测试：真实形状 wire/bundle，v2 拒绝、state 引用损坏、路径上跳、超限、敏感原文不入库/日志、无法定位不发布。
2. API/数据库：owner A 无法读/停/发布 B；并发 start busy、stop 幂等、断线/CLI 失败/restart 明确失败、发布事务/版本快照、撤回、审批拒绝零副作用。
3. 真扩展 E2E：独立 fixture 服务新 origin（不得使用被权限明确拒绝的生产 localhost:3100），专用新 profile，加载已校验0.4.1扩展、显式本机连接；人工或 OS 输入事件完成姓名输入、select、按钮提交；检查 trace 目标和值，规范化移除值，review/publish 后新 session 重放，断言 fixture 最终结果。不得读取用户其它 tabs/cookies/history，测试用临时数据和虚构字符串。
4. 目标重复、密码字段、SPA 导航、人工接管、取消、后台进程结束、扩展离线、写操作未知结果等失败分支。测试结束只清理专用 session/profile和fixture数据。

## 实际限制

BrowserSkill 不是现成的“演示一次就可靠生成任意网站自动化”编译器；没有通用已验证的 trace replay 子命令。语义目标可能歧义、VOM截断、动态列表复用节点，scroll无距离、switch_tab无持久身份，canvas/原生对话框/受限制页/复杂跨域 iframe 也不能仅凭现有语义数据承诺稳定重放。MVP 对这些明确报 unsupported/review_required，不生成看似成功的 Skill。新增定位器、复杂条件/循环、多 tab、上传下载和后台网站调试不属于此次最小交付。

