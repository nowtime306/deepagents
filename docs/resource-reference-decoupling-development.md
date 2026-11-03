# 资源引用解耦与配置追溯实施计划

依据 `resource-reference-decoupling-requirements.md` v1.0，RDC-01～14。2026-11-03，开发基线 `048169073ebb1de3034b88876bbbb63ec857cca3`，分支 `onwer_agent`。用户本轮已明确授权开发与测试，沿用独立审查后交测试的顺序；本文不代表已验收上线。

## 实施语义与边界

资源ID继续表达引用，显式Agent/Workflow业务版本继续不可变；普通外部配置修订不再充当跨资源可用性判断。实际执行时解析资源，首次使用固定本次配置，同一运行/阶段复用，不用更新后的修订伪造历史。启停、权限、租约、取消、实际接口/向量兼容和具体操作审批保留。

旧发布快照中的revision/pins保留为历史，不改写成当前值。运行配置仅保存脱敏公开摘要和加密的非凭据执行配置；不新增明文或密钥历史。原进程保留已解析凭据；重启仅在凭据接收范围安全相同时采用当前凭据并留痕，服务目标/协议等边界改变时待用户确认新执行阶段，不静默改投、不重放已完成写操作。

## 开发分工与依赖

1. RDC-01～04：公共运行上下文、模型解析、Agent/团队/自动任务/各工作流入口；迁移083记录运行资源配置与阶段。先确定接口，再接各调用链，校验与调用使用同一配置。
2. RDC-05/07/11/12：迁移082、同事务脱敏变更历史、模型/MCP/路由/索引设置/仓库/通知；维护变化与接收方/身份/目标变化分类。隐式默认切换关联两侧历史，CAS失败不产生成功记录。
3. RDC-08/09：迁移084、统一向量兼容指纹、文本/记忆/图片/视频与评测适用性；不以维度或通用修订单独判断兼容，旧数据无证据明确待核对。
4. RDC-06/10/13/14：Skill/MCP运行快照及具体操作摘要，既有详情中的中文修改记录、运行资源配置、当前差异和安全恢复入口；根角色集成main及权限、各资源详情和运行详情。
5. 开发自验→按模块独立代码审查→修复复审→独立QA与需求编号覆盖→实际页面体验及剩余建议→版本提交/推送与部署门禁。测试文档可并行准备，独立验收等待已审源码冻结。

模块并行读写分工，公共接口先对齐。082由变更历史模块维护，083由运行解析模块维护，084由索引模块维护，不并发修改同一迁移。跨模块公共调用链必须同步适配，P1不能成为P0已知回归的借口。

## 验证与交付

- 原AGENT_MODEL_CHANGED反例；模型普通更新后旧发布版可创建并执行任务，删除/停用/类型不兼容仍拒绝。
- 排队期间更新采用新配置；同一运行多次调用保持原配置，新运行使用新配置；团队/嵌套多模型分别追溯。
- 工作流旧v1切换和执行保留原节点/明确子版本；外部Key、Ask、Work入口一致。
- 记录原子性、默认联动、删除保留、并发冲突、跨账号权限及秘密标记不进入历史/快照/错误/日志。
- 凭据轮换、目标变化的恢复阶段、具体审批摘要、通知接收人、仓库身份与推送内容边界。
- 向量同维不同模型、普通维护/兼容迁移/未知旧数据，以及严格评测门禁与已存在历史结果。
- 中文详情及只读模式、窄屏、返回保留选择；TypeScript、生产构建与对应后端回归。

全部数据库验证使用单独随机 `_test` 库、假账号和协议替身。真实外部模型/第三方通知继续暂缓，实际执行环境只使用明确创建的测试资源并核对回收。生产数据、密钥、历史发布与用户操作均保留，不自动恢复旧备份。

上一轮浏览器工具仍明确以保存权限/管理策略拒绝3131/3100。本轮不以其它浏览器、CDP、命令或代理绕过读取页面。体验与界面验收分别标注静态路径检查和实际操作，待工具真正允许后才执行真实页面验收；构建不能替代此门槛。SSE范围不扩大，语音/图片理解独立导航继续隐藏。


## MCP JSON Schema 兼容比较的有限规范化

RDC-10 的工具接口检查与具体操作审批摘要共用 `tool_protocol_bridge.canonical_schema`。HTTP 读取同一纯函数，stdio 桥接脚本直接包含该实现，避免两条路径各自删字段。原始工具 Schema 仍保留在运行配置中；这里只规范化兼容比较和 operation_identity 的输入，不替换提供方 Schema，不填默认值，也不新增工具权限。

官方 [Annotations](https://json-schema.org/understanding-json-schema/reference/annotations) 将 title、description、examples 用于描述；[Comments](https://json-schema.org/understanding-json-schema/reference/comments) 说明 $comment 不承担校验行为；[Object / Properties](https://json-schema.org/understanding-json-schema/reference/object#properties) 明确 properties 的键是实际字段名，其值才是子 Schema。因此只在已知 Schema 位置移除有效的 title/description/$comment/examples，并将空 properties={} 与省略 properties 视为等价。properties 下名为 description/title/examples 的真实字段不删除。

递归位置限定为 properties、patternProperties、$defs、definitions、dependentSchemas/dependencies 的子 Schema 映射，allOf/anyOf/oneOf/prefixItems 和旧版 items 数组，以及已知的单子 Schema 关键字。default、enum、const 和未知关键字的值均原样保留，不把其中的数据对象当 Schema 递归处理。required、类型、枚举、常量、附加字段策略和其他约束不删除、不排序重写。default 虽在规范中属于注释，但可能影响调用端补值，因此本期继续作为行为差异；readOnly/writeOnly/deprecated/format 等也保留。

这不是通用引用解析器。只有目标存在、名称为简单 ASCII 标识的根 #/$defs/name 或 #/definitions/name 引用可以参与规范化；引用文本保持原值。外部引用、未知路径、指向注释/examples 的路径、URI/JSON Pointer 转义、动态或递归引用、改变引用基址的 id/$id/anchor 等情况整份保留原文。格式错误的注释也不通过删字段修复。复杂引用场景不承诺注释修改自动兼容，继续保守核对；改显示名称或密钥仍不参与操作身份。

作者验证覆盖空 properties 的真实 MCPService.invoke 正例、真实字段/约束变化拒绝、HTTP 首次及调用前声明比较、具体操作摘要，以及通过受控 stdin/stdout JSON-RPC 替身调用实际 stdio execute 的接受/拒绝路径。替身不启动宿主命令、真实沙箱或供应商，不能替代外部协议验收。独立复审和 QA 在下一次冻结后执行。
