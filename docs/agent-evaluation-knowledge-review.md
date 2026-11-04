# A03/A04知识评测接线审查

日期：2026-11-04。作者rdc_index，非作者审查与工程执行为root。依据AE-04/08/09/11/14及剩余需求，不以本篇宣称真实模型效果、页面或生产部署。

## 业务与证据合同

正式API创建独立embedding模型、知识库、文档修订及Agent发布版，实际上传触发索引；不另发多余索引或检索来凑计数。A03引用须来自当次实际tool返回的document/chunk/revision/text；A04保留无关候选并准确拒答，不把检索失败或keyword降级当“资料不足”。

独立证据同时核原文、真实分块/修订、向量READY/维数/指纹、索引operation、已用模型与知识快照、完整消息及工具关联、原生用量和provider根。索引1次/7入0出，Ask含两chat及查询embedding共3次/27入10出，总4次/34入10出；初始化计费不遗漏，最大6次/90 Token/2根受target持久预算约束。真实Qdrant使用目标独立data目录，不访问生产向量文件。

只有同环境、原输入/意图和同份有界事实字节可重算；重复call ID、跨根、bool冒整数、非有限JSON数值、伪造引文、旧修订、错误used配置或未知用量都拒绝。证据保存失败立即停止，未知POST不重放。CLEAN需要当前索引/provider与封存一致，外层ready/reader再精确回收；不通过删除历史引用掩盖失败。

## 独立审查问题与修复

- KBR01/02：原v2只各计一次tool.started/completed，倒序或额外tool事件仍可通过。非作者用原MockTransport正例的深拷贝实际复现2失败，不改sealed原件。v3将所有tool事件精确约束为同name/call ID的start→complete两项；原2反例复验通过，正常message.completed/delta仍保留。原红/绿证据在`services/evaluation/.local/knowledge-root-review/`。
- KBR03：正式SSE还含没有原生序号的stream.snapshot，旧collector误按业务事件处理，不能与现有正式接口联通。v4只改events函数：明确解析并核snapshot原root/终态，control不冒充原生事件，error为待核验，未知control或end后data拒绝；展示快照不能替代原消息/计量/原生事件。原服务器没有展示快照时，完整原生事件和终态合同仍可独立验证。

v2/d805、v3/f5e8原件保留；最终v4清单摘要`8582f7ac5a0a7c3ce7c93941f128313c4123047b3e2d0c6efa33fd458d82a4cc`，active模块摘要`900502ab71a1fed72a5ce376a704cecd6cb286f6651866eea75c488f9817924b`。只合小patch，不合私有overlay或整份旧W02共享副本。

## 工程验证与后续门禁

非作者在active路径执行新增客户端61、知识profile22、正式frame格式14项分别通过；均为受控HTTP/DB/SDK替身，不是实际知识库或模型执行。它们与完整回归覆盖重叠，不相加。最终客户端完整457通过、目标侧完整185通过，零失败/跳过（包含既有Java等组）。日志根包括`kb-active-client-10ba61fdcf7f47ddac28350aaedfc9d8`、`kb-active-profile-c718bbecb5d440aea3caad17a5e6854a`、`kb-stream-active-ef3d23ff71d14ed28c84016f2769e362`、`kb-final-client-b26142f034a647789003004e0c9d0bd6`及`kb-final-profile-43150dc93a1e4f11a5bb4ea2432b8354`，都位于`services/evaluation/.local/`。

固定suite仅登记A03/A04处理器及保守沙箱预约，尚未运行它们的真实样本，不增加8/20验收数。仍须冻结候选，在全新独立目标走实际索引、Qdrant、Ask、原事实封存和精确回收，并由另一角色核验；真实供应商、页面、A20全批/B100及发布保持未完成。
