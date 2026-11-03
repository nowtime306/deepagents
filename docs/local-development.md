# 本地开发与验证

最新验收与明确限制见 [implementation-verification.md](implementation-verification.md)。完整目标见 [development-plan.md](development-plan.md)，实际进度见 [development-status.md](development-status.md)。

## 1. 准备和启动

需要已安装 Docker Desktop、uv、Node.js；Windows 使用 PowerShell。在项目根目录运行：

```powershell
.\scripts\start-platform.ps1
```

脚本创建独立的数据库容器和数据卷，首次生成本机数据库密码，安装锁定依赖、应用迁移并初始化管理员。前后端窗口隐藏运行；再次运行不覆盖本机配置、已有账号或数据。若端口已被其他服务占用，先核实并释放，不要停止其他项目。

工作台地址为 `http://localhost:3100`，API 地址为 `http://127.0.0.1:8100`。浏览器写操作要求来源严格匹配 `WEB_ORIGIN`；不要混用 `localhost` 和 `127.0.0.1` 登录。

管理员账号和随机密码在 `services/api/.env.admin`，由用户在本机查看。该文件、API 的 `.env` 和数据库 `.env` 均被 Git 忽略，不提交或发到聊天中。

API 使用单进程并独占嵌入式 Qdrant；Work 使用独立持久 Worker。API 重启时未完成 Ask 短任务明确标为失败，历史和检查点保留；Work 租约失效进入核查状态，不自动重放命令。

## 2. 在页面配置模型

使用管理员登录工作台，进入 **设置 → 模型管理 → 添加模型**。填写显示名称、服务商提供的实际模型 ID、API 基础地址和密钥，然后保存。可配置最大输出 Token 和温度；聊天最大输出留空时使用平台默认 4,096，Work 单次最多 8,192；温度留空沿用服务默认值。

保存后无需重启 API，启用模型会立即出现在聊天页；支持编辑、启停、默认模型、连接测试和删除。编辑时密钥输入框留空会保留原密钥，勾选“清除已保存的密钥”才会清除。没有鉴权的本地服务可不填密钥。

模型名称、地址和调用参数保存在 PostgreSQL，密钥加密保存且不回显。`.env` 中只保留数据库、站点来源等平台参数；原 `MODEL_CONFIGS` 和模型密钥环境变量不再读取。

**备份时同时保存数据库和 `services/api/.local/model.key`。** 这是平台加密主密钥，不是模型配置文件。丢失或更换此文件后无法解密已有模型凭证；系统不会用新密钥悄悄替换已有密钥。普通用户只能使用启用模型，不能读取管理信息或修改配置。

“测试连接”按模型用途选择相应接口；不支持的组合明确说明。聊天检测最多请求 16 个输出 Token，向量/重排不通过聊天接口检测；真实服务尚未准备好时可以先保存。

手工启动 API：

```powershell
cd services/api
uv run --no-sync python -m app.server
```

手工启动前端：

```powershell
cd apps/web
npx --yes pnpm@10.5.1 exec next dev --webpack --hostname 127.0.0.1 --port 3100
```

Windows 使用上述 API 启动模块，避免直接启动 Uvicorn 时默认 Proactor 事件循环与 psycopg 不兼容。数据库地址使用 `127.0.0.1`，避免本机 IPv6 转发等待。

## 3. 数据库迁移与用户

在 `services/api` 执行：

```powershell
uv run --no-sync python -m app.manage migrate
uv run --no-sync python -m app.manage create-user --username yourname
```

创建用户时交互输入密码，最少 12 字符；不覆盖同名用户。业务 SQL 迁移位于 `migrations/`，版本表防止重复执行；Checkpointer 通过官方 `setup()` 管理自己的表。

数据库容器名为 `owner-agent-postgres-1`，端口为 `55432`，与其他本机项目隔离。不删除数据卷作为日常启动步骤。

## 4. 验证

后端离线测试和检查：

```powershell
cd services/api
uv run --no-sync pytest tests/unit_tests -q
uv run --no-sync ruff check app tests
```

集成测试需要独立的、数据库名称以 `_test` 结尾的 PostgreSQL 数据库。设置 `TEST_DATABASE_URL` 后执行 `uv run --no-sync pytest -q`。不要把生产数据库填入此变量。当前测试会创建带随机用户名的测试数据；不清空已有库。

前端运行后在 `apps/web` 执行：

```powershell
npx --yes pnpm@10.5.1 exec tsc --noEmit
npx --yes pnpm@10.5.1 exec playwright test
```

浏览器测试使用已安装 Chrome；聊天测试使用拦截响应，实际登录测试连接本机前后端，均不代表真实模型联调通过。截图输出到 `apps/web/test-results/`，不提交生成文件。

## 5. 来源与依赖

前端基于 Agent Chat UI 提交 `2624b821a4542e115a7e8eb8917e4cfb42397ce6` 导入，原 MIT 许可证保留在 `apps/web/LICENSE`。平台入口复用其 Markdown、代码高亮和基础组件；平台网络传输单独实现，原通用 LangGraph API 透传路由已移除。

新增 FastAPI/Uvicorn 用于平台 API，psycopg 用于 PostgreSQL，官方 PostgreSQL Checkpointer 用于图状态，langchain-openai 用于兼容模型服务，python-dotenv 用于本机配置；这些依赖由 `uv.lock` 固定。前端沿用上游锁文件，不升级整套框架。

## 6. 能力中心

在 [本地平台](http://localhost:3100) 登录后打开左侧“能力中心”。知识库和 Skills 为当前账号私有；MCP 服务由管理员配置，启用后的只读工具供登录用户选择绑定。详细用法见 [capability-center.md](capability-center.md)。

本次新增迁移 `003_capability_center.sql`，启动前执行迁移命令。本机开发数据库已应用。新增依赖包括官方 MCP SDK、PDF/DOCX 解析器及 Qdrant 客户端，均已锁定在 `services/api/uv.lock`。

模型设置新增 `004_model_types.sql`，扩展十类用途及服务商/协议字段，本机开发库已应用。新增直接依赖 `langchain-anthropic`，用于原生 Claude 对话和流式工具协议；沿用维护中的 LangChain 官方适配，避免自行转换 Messages 格式。多类型配置的使用范围见 [model-types.md](model-types.md)。

开发版 Qdrant 使用嵌入式持久化存储，目录为 `services/api/.local/vectors`；无需额外启动 Qdrant 容器。只允许当前单个 API 实例持有该目录，备份时同时保存 PostgreSQL、`.local/model.key` 与向量目录。凭证密钥文件丢失会导致模型和 MCP 密钥无法解密。

## 7. OpenSandbox 本机服务

Windows 11 上启动 Docker Desktop（Linux 容器模式）后，在项目根目录执行 `powershell -File scripts/start-opensandbox.ps1`。脚本安装并锁定的 OpenSandbox 1.2.0 服务依赖，准备测试镜像，在 `127.0.0.1:18180` 启动受管理密钥保护的服务。管理密钥位于 `services/sandbox/.local/manager.key`，目录仅允许当前 Windows 用户、SYSTEM 和管理员访问；不要将该密钥发给浏览器或模型。启动脚本不自动开放任务执行入口。

沙箱执行端口绑定本机回环地址，网络默认拒绝需要由平台创建请求显式指定；`windows.toml` 使用 `dns+nft`。启动管理服务会授予该服务 Docker 管理访问权，不能把管理密钥暴露给任务沙箱。任务执行、审批、跨重启配额与文件安全已通过本地受控验证；执行入口还要求 WORK_ENABLED 和有效 Worker 心跳，不能直接暴露内部生命周期接口。完整进度与验证范围见 [agent-workspace-plan.md](agent-workspace-plan.md)。

使用 `powershell -File scripts/build-sandbox-images.ps1` 构建 Coding 和 Browser 镜像。Coding 包含 Git、Python、Node.js、Java 和 Maven；Browser 使用 Playwright 与系统 Chromium。镜像用于任务工作台的受控执行，不分配 GPU、不挂载宿主目录。


## 8. 任务后台和浏览器

先启动 Docker Desktop 和平台 API/Web；完整启动脚本会一并准备执行环境。单独启动任务后台也会检查并启动 OpenSandbox、验证管理接口并补齐 Coding/Browser 镜像：

```powershell
.\scripts\start-work-worker.ps1
```

现有本机已经通过受控执行验收并启用 Work。其他部署仅在完成同样验证后设置 `services/api/.env` 的 `WORK_ENABLED=true`；模型和密钥仍在页面配置。任务工作台可选择 Coding/Browser 环境、模型/Agent 版本、预算及明确公网域名。默认没有出站权限；页面关闭不会中断后台任务。暂停在安全检查点生效；抢救只保存文件，不重放未知操作。

Browser 工具按审批后的操作列表运行，每组重新打开私有浏览器；需要同一标签页状态的动作放在同一组，最多 12 步。截图、下载和技能脚本输出进入工作区成果。技能资源先复制到工作区，再通过命令审批执行。

## 9. 备份和隔离恢复

先安全停止本项目 API 与 Worker，待心跳过期后，在 `services/api` 执行：

```powershell
.\.venv\Scripts\python.exe -m app.backup create
.\.venv\Scripts\python.exe -m app.backup verify --backup <备份目录>
.\.venv\Scripts\python.exe -m app.backup restore --backup <备份目录>
```

create 返回受保护的本地目录；verify 创建临时数据库，检查后删除该临时库；restore 保留新数据库和恢复目录，返回 `configuration_path`。原库不被覆盖。备份包含加密主密钥和本机配置，只保存在本地受保护目录。

可用独立端口启动恢复副本：

```powershell
.\.venv\Scripts\python.exe -m app.server --env-file <configuration_path> --port 8101
```

恢复副本默认禁用 Work，撤销旧会话及 API Key，不接管原执行环境。完成核对后停止副本服务，再启动原 API 与 Worker。不要同时让两个 API 打开同一个 Qdrant 数据目录。


## 10. 对话内联网和图片

刷新页面后，在智能对话输入框使用“＋”上传图片或文档，也可直接粘贴图片。图片支持静态 PNG/JPEG/WebP，单文件 4 MB，每轮合计最多 5 份附件。发送后可在对话历史预览并继续围绕图片提问；含图片的历史需要支持图片的模型。

当前 DeepSeek Flash 已自动识别图片能力；其他聊天模型可在模型设置中的“图片输入能力”明确开启，或选择已配置的图片理解模型。开启标记不改变模型本身的实际能力。

“联网搜索”默认对已适配模型启用，可随时关闭。Flash 使用 DeepSeek 官方 Search API；OpenAI 和 DeepSeek V4 Pro 使用服务端内置搜索。开关表示允许调用，模型可按问题决定是否搜索，结果需结合来源核对。原有 Agent 自身的联网授权仍生效，未授权 Agent 不会因对话开关获得联网权限。


## 升级前备份的失败停止规则

安全停止 API/Worker 后，备份与迁移必须逐步检查退出码。PowerShell 的外部程序失败不一定阻止下一条语句；不得直接用分号串联备份和迁移并忽略结果。

```powershell
.\.venv\Scripts\python.exe -m app.backup create
if ($LASTEXITCODE -ne 0) { throw '备份失败，停止升级' }
.\.venv\Scripts\python.exe -m app.manage migrate
if ($LASTEXITCODE -ne 0) { throw '迁移失败，检查后再恢复服务' }
```

只有存在完整 manifest.json 且隔离验证通过的目录才作为恢复依据；未生成清单的中断目录不是有效备份。044、045 增加私有会话项目与知识库关键词证据门槛，应用后共 45 项迁移。

## 11. 持久对话与账号管理（2026-11-01）

046 增加 Ask 队列租约，047 增加注册、角色、邀请和会话标识；本机共 47 项迁移。生产默认独立对话后台，API 持久入队后由后台执行：

```powershell
.\scripts\start-ask-worker.ps1
```

start-platform.ps1 会在 API 就绪后启动 Ask、Work 和运营后台；start-work-worker.ps1 也可独立、重复运行，已有本项目服务会保留。页面关闭或 API 重启不结束后台 Ask；后台丢失租约时标记结果未知，不自动重试模型或重放工具。下一轮显式提问会把上次未闭合工具消息标记为结果未知。API 暂时不可用时知识检索明确失败。

备份前须安全停止 API、Ask 后台和 Work 后台，等待心跳过期；共享维护锁防止备份期间后台继续写入。保留的恢复副本会关闭注册、撤销邀请码并终止待执行 Ask，不会自动运行旧请求；原数据库不受影响。

管理员在“账号管理”审核注册、分配功能角色、创建邀请码、重置临时密码和撤销会话。默认提交注册后审核；普通用户通过账号旁的设置按钮改密与管理自己的会话。修改密码后全部旧会话失效，.env.admin 只是初次管理员凭证文件，不随用户之后的密码修改自动更新，也不应据其覆盖现有密码。
