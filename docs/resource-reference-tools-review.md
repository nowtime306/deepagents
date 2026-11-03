# RDC MCP / Skill 独立代码审查

日期：2026-11-03。角色：独立审查者，未编写本报告涉及的MCP/Skill实现。依据：RDC-04、06、10、13及需求第3.3/3.4节。

## 范围和结论边界

最终状态：T-01、T-02、T-03均已由独立审查者逐项重放原探针并确认修复，CLOSED。最终15文件冻结摘要核对通过，详见末节。本结论只表示该范围代码缺陷复审清零，不表示独立QA、真实沙箱、外部服务或实际页面已验收。

审查 `resource_tools.py`、`capabilities.py`、`mcp_service.py`、`tool_transports.py`、`browser_skill_work.py`、`browser_skill_api.py`、`operation_journal.py`、`tool_protocol_bridge.py`，以及前端 `browser-skill-approval.tsx`、`work-run-panel.tsx`。为确认公共接口实际调用方式，追踪了 `workflow_runtime.py` 的MCP节点和 `work_backend.py` 的审批收据参数传递；未修改这些实现。

作者初始manifest为 `services/api/.local/team-workspace/rdc/index/mcp-author-evidence.json`，摘要 `ed94bef5cdc16460f86385452082117038a1b8fcdb3503afcaee4fe9e8770f6b`。作者46通过/3跳过仅是作者自验，不是本审查者的QA结果。

审查中先发现两项P1并交作者修复，随后追加核查发现第三项P1。作者同时按根任务要求，将MCP快照revision从connection_identity改为configuration_revision，避免以语义摘要冒充配置修订。审查期间manifest暂时未更新，因此不得把旧manifest当作包含追加修复的最终冻结证据；最终摘要一致性和复审状态见本报告末节。

未访问页面、生产数据库、真实MCP、浏览器或外部服务。独立定向探针只使用内存数据库/会话/执行器替身，调用实际服务函数；没有启动stdio程序或浏览器，不能代替隔离数据库QA、真实沙箱/协议集成和页面验收。

## 原发现 T-01 [P1] resolved_server 绕过运行冻结，工作流可在同一轮换目标

当前状态：CLOSED，见最终复审。

初始位置：`services/api/app/mcp_service.py:130` 的 `resolved_server or await frozen_server(...)`；直接调用方为 `services/api/app/workflow_runtime.py` 的MCP节点分支（读取当前server后传给 `MCPService.tool`）。

`CapabilityService.for_owner` 会先冻结server，但工作流MCP节点直接从数据库取当前server；`tool` 将该原始值作为resolved_server传给invoke。invoke信任这个非空值，不经过freeze_resource。因此同一工作流两次使用同一MCP，可以读取不同的URL和key，没有运行快照约束。

独立复现：在一个实际resource_run内调用两次实际WorkflowRuntime.execute，外部会话替换为内存Session；两次之间只修改数据库替身中的server.url。初始输出：

- same_run_targets = `https://original.invalid/mcp`、`https://new-target.invalid/mcp`。
- snapshot_inserts = 0。

影响：已有运行悄悄改投新接收方，且运行追溯缺少该MCP配置，违反RDC-04/06/10。

追加修复方向已读到：有current_scope时，invoke始终从当前live行经frozen_server取得该运行固定server；无scope才允许沿用resolved_server。不能以调用方声称“已解析”代替服务层运行边界。

## 原发现 T-02 [P1] 只检查live工具启用，忽略管理员变更读写性质/Schema

当前状态：CLOSED，见最终复审。

初始位置：`services/api/app/mcp_service.py` 的live_tool检查及随后的远端list_tools核对。

初始实现只确认当前数据库工具存在且enabled；readonly与Schema只比较远端定义和旧冻结定义。若管理员把当前工具声明由只读改为写入，但远端仍返回旧只读声明，旧闭包仍能无审批调用。

独立复现：冻结lookup的read_only=True，将live数据库声明改为False，远端替身仍返回原Schema/readOnly=True；调用实际MCPService.invoke，call_tool计数为1，未发生审批。此问题不需要真的向外发送请求。

影响：当前管理员改变的具体操作安全边界没有生效；仅远端声明相同不能覆盖本地写审批要求。Schema改变也会被旧声明掩盖，违反RDC-10/13。

追加修复方向已读到：具体live_tool的read_only或schema与运行固定项不同时，在进入session前返回 `MCP_TOOL_INTERFACE_CHANGED`。描述、名称维护和其他无关工具变化不参与当前操作的失效判断。

## 原发现 T-03 [P1] 等待远端工具定义期间撤权，只读操作仍继续发送

当前状态：CLOSED，见最终复审。

位置：`services/api/app/mcp_service.py:124`～`:160`，重点是会话前的live读取、`:146`的`await session.list_tools()`和`:160`的实际`call_tool`。

只读工具在工具闭包进入时检查账号权限/绑定，在invoke建立会话前读取server/tool状态；远端初始化和list_tools都可能耗时。若这些await期间管理员停用server、停用具体工具、改变读写/Schema或撤销使用绑定，返回后只核对远端定义，未重新核对当前本地授权。写工具有before_write回调的二次检查，只读路径没有对应边界。

独立复现：实际MCPService.invoke，Session.list_tools替身返回前将数据库server.enabled改为False，并返回仍相同的远端只读定义；实际call_tool替身仍被调用。输出：`live_enabled_at_actual_tool_call=False, actual_tool_calls_after_disable=1`。

影响：撤权已经完成后，新的实际工具请求仍向外发送参数。远端定义发现属于先前已开始的操作，不代表之后的实际调用已获得撤权后的授权。违反RDC-10/13即时生效边界。

建议：在实际call_tool前重新核查具体server/tool的当前启用、Schema/readonly、使用权限和绑定；仅增加会话前的比较无法关闭这个可控await窗口。二次检查应与操作语义/单次审批衔接，但不把普通名称/密钥维护升级为重新审批。

## 冷/热凭据与审批绑定核查

### 运行目标与凭据

最新 `resource_tools.frozen_server` 的持久字段不包含token_ciphertext原值；凭据只保存在进程内，持久部分为无原值marker。热运行沿用固定URL/命令与缓存凭据；冷恢复在采用当前凭据前比较connection_identity，不同目标要求确认。OAuth授权标记和内网访问撤销由调用时live检查另行处理。

对追加修复代码做独立定向探针：实际WorkflowRuntime.execute在一个resource_run中首次采用原目标/key，更新当前目标/key后第二次仍采用原目标/key；退出并重入相同运行、模拟冷缓存时，目标变化返回 `RESOURCE_RECOVERY_CONFIRMATION_REQUIRED`，会话调用总数保持2。快照revision记录为字符串形式的配置修订1，而非connection hash。

该探针证明所构造时序的服务层行为，不证明实际SDK、OAuth供应商或服务端日志的处理。

### HTTP与隔离协议审批

HTTP写工具名称的语义摘要覆盖connection_identity、具体工具名、Schema、readonly；操作收据还绑定具体arguments。普通名称/密钥更新不改变该操作身份，目标/结构/读写变化不能复用旧语义名称的收据。

stdio/ACP的工具名称绑定目标命令摘要与bridge内容摘要；调用参数进入单次审批收据，实际桥接代码改变会拒绝旧执行。stdio调用在容器内重新list_tools，并核对审批提交的Schema/readonly，再发送tools/call；不接受远端任意客户端权限请求。

独立内存执行器探针：在command_json中放置测试秘密标记，构造并调用实际task_tool的discover路径；工具metadata和传给backend审批收据的args均不包含该原始标记，收据参数仅有operation。本探针未验证真实沙箱平台对编码命令行/输出的日志行为，不能据此宣布所有日志脱敏通过。

## 浏览器Skill审批核查

- 审批详情按owner、approval、Skill绑定和启用状态授权，读取运行中保护的Skill内容；缺少快照不以当前Skill代替。
- 前端显示完整路径前核对版本、名称、步骤数、参数与允许的操作。校验状态按approval ID、digest、action index和args区分；无法核对时批准按钮不可用。此结论来自源码，未实际浏览验收。
- OperationJournal为run_browser_skill的收据附加发布trajectory的语义摘要；执行端比较冻结trajectory与发布版本、参数和摘要，不能只替换数据库中的发布脚本后沿用旧操作收据。
- 执行前和步骤间检查任务租约、允许本机浏览器的显式选项、管理员权限、Skill启用及绑定。状态未知记录UNKNOWN，不自动重放。
- 页面只承诺顶层导航域名约束，不宣称浏览器存在网络硬隔离。真实专用浏览器、人工接管、弹出新tab及路径执行均未验证，且当前浏览器访问拒绝不得绕过。

## 初稿时的待冻结状态与后续验收

截至本报告初稿，作者正在追加修复T-01/T-02，T-03新发现已通知作者，旧manifest与resource_tools.py、mcp_service.py及相关两份测试摘要不一致。本报告保留这三项P1发现；在作者重新冻结并完成摘要核对前，不宣布最终审查通过。

下一步必须：取得追加修复后的manifest和提交SHA；复核上述服务层更改；在隔离数据库中验证工作流/团队/Ask/Work所有MCP入口固定同一目标、冷目标/凭据隔离、具体Schema/readonly变更拒绝、批准参数与脚本不可替换、撤权即时生效和UNKNOWN不重发；真实沙箱/页面/外部供应商分别保持独立gate。作者自测通过数不替代这些验收。


## 2026-11-03 最终冻结独立复审

最终manifest：`services/api/.local/team-workspace/rdc/index/mcp-author-evidence.json`，摘要 `d1c48f0017b629f214239c099c538e2bc36365913cd5b8e89a909349e3661f1c`。独立重算sorted files映射的SHA256与manifest字段相同；15个文件实际SHA256全部匹配，无例外。

独立重读最终invoke、tool的before_call路径以及frozen_server实现，并重放原先由审查者构造的纯内存探针。探针使用实际MCPService、WorkflowRuntime.execute、resource_run和frozen_server；只替换数据库、凭据封装、远端会话及权限状态，不执行网络、沙箱或浏览器。临时探针脚本位于 `services/api/.local/rdc_mcp_rereview.py`，不是业务或作者测试修改。

- **T-01 CLOSED**：实际工作流MCP节点在同一scope先调用原目标，更新当前目标/key后再调用，两个实际call_tool都指向原目标且使用原key标记。确有运行快照，revision为配置修订7，不是连接摘要。清除该scope进程缓存后重入相同运行，当前目标不同返回RESOURCE_RECOVERY_CONFIRMATION_REQUIRED，actualcall总数仍为2，未把新key送往旧/错误目标。最终实现不再信任resolved_server替代canonical快照；写操作目标仍受具体语义校验。
- **T-02 CLOSED**：先固定工具后分别更改数据库具体工具read_only和Schema，调用实际invoke均返回MCP_TOOL_INTERFACE_CHANGED，实际call_tool次数均0。不是只读到新增if就关闭问题。
- **T-03 CLOSED**：远端list_tools替身在返回前分别完成六种状态变化：server停用、tool停用、Schema改变、readonly改变、tools.use撤销、绑定撤销。使用真实tool闭包及before_call路径，六种情形分别返回对应拒绝错误，实际call_tool次数全部0。原先“await发现完成后仍发送”的探针已被关闭。

本节通过的是9组缺陷定向场景，不采用作者52通过/3跳过作为独立QA结论。未修改实现；初次启动探针时因缺PYTHONPATH无法导入app，补齐当前包路径后上述9组完成，未将该环境错误混记为业务失败或通过。

后续交独立QA前仍由根任务确认其他范围和迁移legacy专项也已审查清零；之后按test-plan逐RDC执行隔离验收。真实沙箱/浏览器页面/真实外部服务继续单列gate，不因本报告CLOSED而解禁或计为通过。
