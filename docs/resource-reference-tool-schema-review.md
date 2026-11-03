# 工具 Schema 规范化独立审查

审查对象为 `services/api/.local/team-workspace/rdc/index/schema-author-evidence.json` 中五个冻结文件。逐项文件 SHA256 全部独立吻合：bridge `f2f30579b82f7ead620a17ff43168ae026f3283f2ca58d974222abd3b9bb16f0`，resource_tools `a1b5f1d9cdb17e59b33f227e8e1cbccbb763b146bbebdf9aa9a2736de4689e29`，mcp_service `f5c8b63013b31b9c1b3a67b067bd5b9367e1bd2f070ec3ff28bc32cfe48d66fb`。清单文件实际 SHA256 是 `c5e6da8907aa89ff636b02a8600651ad09ca292e3e04d0ab8f4c3a594b8d3994`；其自含 manifest_sha256 字段及作者消息的 c579... 不是最终文件摘要。本审查采用实际文件摘要及逐项 SHA，不据此推断实现发生漂移。

依据 RDC-10/13：无行为差异的工具描述维护可继续；工具真实参数、约束和读写性质变化不能沿用旧接口或旧审批。

## [P1，待修复] 接口比较将布尔值与数字约束当作相等，允许修改后的写接口被调用

位置：`tool_protocol_bridge.canonical_schema` 返回普通 Python 对象；`mcp_service.py` 的 live/remote/final Schema 比较，以及 `tool_protocol_bridge.execute` 的 expected_schema 比较，使用对象的 `==/!=`。

Python 把 True==1、False==0。因而即使 canonical_schema 保留了 const/enum/default 字段，以下两个有效 JSON Schema 在实际 gate 中仍被当成等价：

- properties.description.const 从 true 改为 1；
- properties.description.enum 从 [false] 改为 [0]。

独立使用本地 Draft202012Validator 验证：原参数 `{description:true}` 或 `{description:false}` 符合原 Schema，不符合修改后的 Schema，是真实约束变化，不是展示注释。operation_identity 的 JSON 摘要可以区分两者，但运行前的接口比较没有使用能保留这种 JSON 类型区别的比较方式。

独立调用真实 MCPService.invoke 函数，但数据库与 HTTP session 均为受控替身：数据库及已批准接口保持原布尔约束，模拟 tools/list 返回对应数字约束。当前代码仍执行 before_write 授权回调，并进入替身 session.call_tool。stdio 探针执行真实 bridge.execute 函数，但 subprocess.Popen/Process、os/select、ctypes 及 RPC I/O 均为受控替身；桥构造并发送模拟 tools/call，没有抛出 PROTOCOL_TOOL_INTERFACE_CHANGED。该证据证明函数内校验边界，不是实际子进程、容器或协议服务联调。没有启动真实进程、网络调用、供应商请求或实际写操作。

建议：接口比较须同时采用同源、保留 JSON 布尔/数字区别的规则，覆盖 HTTP 的所有 live/expected/remote/final 比较及 stdio 桥；不能通过 operation_identity 单独正确来替代运行前 gate。保留真实字段名及 default/enum/const/未知结构，补充布尔与数字的真实调用反例。修复后需重新冻结并独立复查。

## 已核对的其余边界

- 规范化仅遍历已知 Schema 位置，properties 等映射中的真实字段名没有被按注释键名删除。
- default、enum、const、未知结构的字面内容保留；复杂或未证明的 ref 使用整份 raw Schema。
- HTTP 与 stdio 使用同一实现；operation_identity 对确定的注释维护保持稳定，对真实内容变更保留不同摘要。
- 独立正例证明空 properties 与省略 properties、顶层注释差异可以通过 HTTP gate；实际 description 参数改名为 title 会被拒绝。

独立定向结果：3 通过、3 失败；失败分别为两个 HTTP 布尔/数字约束反例及一个 stdio 反例。脚本 `services/api/.local/team-workspace/rdc/runtime/test_schema_independent_review.py`，记录 `services/api/.local/team-workspace/rdc/runtime/schema-independent-review.log`。没有修改产品实现或作者测试；这不是完整 QA、真实服务联调或部署结论。


证据措辞更正：前序协作消息中的“无进程替身”表述有歧义；准确范围是“stdio 真实 execute 函数 + 受控进程/RPC I/O 替身，HTTP 真实 invoke 函数 + session 替身”。P1 反例的约束差异与校验放行已复现，但真实子进程、Coding 容器和 stdio/ACP 服务联调需要另行验证，不能据此计为通过。

## 强类型修复后的独立复审：P1 已关闭

新冻结清单文件实际 SHA256 `999ed88f4410a5908aafc4c324fcf2cad8dbb57c07e47a5d56e1b6c8366abe07`；逐项文件均独立核对吻合。bridge 新 SHA `c86cfd152b9e03708a99dd0b20d98e4d366664c7542c23e91d274edfaaff5baf`，MCPService 新 SHA `170a6146aa5c1d176865404710d76f8fd11c5f7bdf8363643814fb9b14c71b60`，resource_tools 保持原 SHA/强类型 JSON 操作摘要。

独立读取确认：新增 canonical_schema_key 对有限规范化后的对象作稳定 JSON 序列化，保留布尔与数字区别，拒绝非 JSON 的 NaN；所有 HTTP live/remote/final 和 stdio expected 接口比较统一使用该 key。expected_tool 继续使用现有强类型 JSON operation_identity，未将真实默认值、枚举、常量或参数名当注释删除，也未扩大 ref 解释范围。

重放原反例并追加 HTTP live 变化、最终调用前数据库变化两个边界，独立 8 项全部通过（首轮 0.66 秒）。包含：普通注释/空 properties 正例、真实参数名反例、const/enum 的布尔数字远端差异、stdio 布尔数字差异、HTTP 初次 live 与最终复核差异，以及 literal/复杂 ref 保留。变化后的接口不进入替身远端 write；许可内的等价展示变化仍可调用。

独立复查记录 `services/api/.local/team-workspace/rdc/runtime/schema-independent-recheck.log`。stdio 证据仍是实际 bridge.execute 函数配受控进程/RPC I/O 替身，HTTP 是实际 invoke 函数配 session/数据库替身，不能计为真实子进程、Coding 容器或 MCP/ACP 服务联调。本项代码复审关闭，当前没有本报告范围内未解决的已确认发现；后续完整 QA 与真实环境验证由主任务另列。
