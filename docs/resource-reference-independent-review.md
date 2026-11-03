# RDC 独立代码审查：索引与运行配置

审查日期：2026-11-03。审查角色未编写本报告涉及的 index/runtime 实现。此报告是代码审查与定向复现记录，不是测试验收、部署批准或真实供应商验证。

## 范围与证据边界

依据 `docs/resource-reference-decoupling-requirements.md` 的 RDC-04、06、08、09、13 及 3.3/3.4 节审查。

索引范围采用 `services/api/.local/team-workspace/rdc/index/author-evidence.json` 的 20 个冻结文件，已逐项计算 SHA256，与清单全部一致。重点读取向量兼容判定、文本/记忆/图片/视频调用、资源记账、实验与质量门禁。

运行范围读取 `resource_runtime.py`、`ModelRegistry.get/from_row`、`usage.py`、团队及工作流资源解析/授权、已发布智能体/自动任务解析、模型能力、原生联网与模型构建调用链。排除 root 正在开发的运行开始路由刷新、rdc_runtime 新恢复确认逻辑、root API/UI，以及 index 新 MCP 任务。未审查本审查者自己编写的配置历史/通知/仓库实现。

未访问浏览器、生产数据库、真实模型或外部工具。未修改实现和测试源码。作者自测报告仅用于定位冻结范围，不作本次验收通过证据。以下复现由独立阅读后使用纯内存数据库/模型替身调用实际服务函数完成。

## 结论

首轮发现 1 项 P1 和 2 项 P2。追加修复经独立定向复审，三项均已关闭；仅表示这三项缺陷的修复复审通过，不表示全量RDC验收通过。独立QA仍须等待全部审查清零后由根任务安排。未发现可以直接证实的 API key 明文写入运行快照问题：模型执行字段排除了 api_key，公开摘要限制了服务地址，恢复凭据限定了协议/服务商/目标；该观察不代表未审查的读取 API 已通过安全验收。

## 原发现 R-01 [P1] 同一运行的检索设置未固定，后续调用悄悄改变行为

当前状态：CLOSED，见末节独立复审证据。

- 位置：`services/api/app/knowledge.py:292`、`:305`，关联 `services/api/app/memory_vectors.py:70`。
- 每次 `_search` 都读取当前知识库行，并直接采用当前 `keyword_min_coverage`、`embedding_model_id`、`rerank_model_id`。外围虽进入 `resource_run`，但只固定模型配置，没有固定知识库/记忆检索设置。记忆 `config()` 也每次读取当前 memory_settings，可以在同一运行内选择新的模型资源 ID。
- 重现：在一个 Ask `resource_run` 内，两次调用真实 `KnowledgeService.search`，知识库无向量/重排，文档内容为 `apple warranty`、查询为 `apple absent`。第一次阈值 0；两次调用之间仅将数据库替身的当前阈值改为 0.9；第二次没有新执行阶段、确认或配置切换记录。
- 独立复现输出：`first_threshold=0, second_threshold=0.9, first_results=1, second_results=0`。
- 影响：同一助手或工作流重复检索会无提示地改用新策略；更换重排绑定会在一轮内改用另一个资源。模型快照无法解释当时阈值/检索绑定，也不能证明整轮采用同一检索配置。违反 3.3 节及 RDC-04/06。
- 修复方向：首次使用时按资源 ID 固定非敏感检索设置并记录；后续读取当前行只作存在、启停和授权检查。普通设置变化供新运行采用，必要切换按新阶段处理。

## 原发现 R-02 [P2] 模型主体和工具能力分事务读取，冻结出不存在的配置组合

当前状态：CLOSED，见末节独立复审证据。

- 位置：`services/api/app/models.py:126`、`:129`、`:134`、`:142`。
- `model_configs` 与 `model_capability_declarations` 经两次 `Database.fetch` 读取，属于两个短事务；合并后直接持久化为一个 revision 的运行快照。
- 重现条件：读取模型主体后、读取能力声明前，另一管理员保存同一模型，将 revision 1 的旧地址/不支持工具，原子更新为 revision 2 的新地址/支持工具。读取方得到 revision 1 + 旧地址 + revision 2 的 supports_tools=True。
- 独立定向复现以两次 SELECT 的返回值模拟该提交时序，实际 `ModelRegistry.get` 返回：`revision=1, endpoint=https://old.invalid, supports_tools=True`。
- 影响：运行快照把新能力标为旧修订的配置，工具能力校验与实际端点不属于同一成功配置提交；后续本次运行会持续复用这个混合快照。违反 3.3 节“按能力读取同一份配置并完成实际调用”和 RDC-06 的准确追溯要求。
- 修复方向：同一条 SELECT JOIN 获取主体与能力，或在同事务内锁定/复查模型修订后组装；不要仅在写入快照时记录先前读到的 revision。

## 原发现 R-03 [P2] 记忆查询向量维度不兼容时返回“混合检索正常”，没有重建提示

当前状态：CLOSED，见末节独立复审证据。

- 位置：`services/api/app/memory_vectors.py:116`～`:131`、`:135`～`:144`。
- `eligible` 仅检查 fingerprint，不读取/比较已记录的 vector_dimensions；查询得到不同维度时，`cosine` 返回 None，旧向量被静默跳过，最终仍返回 `mode='hybrid', warning=None`。
- 重现：保持相同模型身份/地址/预处理，已有一条 2 维记忆向量，受控向量服务对查询返回合法 3 维向量。通过实际 `MemoryRecall.recall`，将权限与父运行检查替换为成功、关键词结果替换为空；未替换维度比较和结果合成逻辑。
- 独立复现输出：`{'items': [], 'mode': 'hybrid', 'warning': None}`。
- 影响：不会跨维度点积，但用户把维度不兼容造成的结果丢失误认为正常的“没有记忆”，也没有核对模型输出或重新索引的入口提示。违反 RDC-08/14 对真实不兼容和降级原因的明确呈现。
- 修复方向：对比查询维度与保存维度；不兼容时明确拒绝语义混查或返回带核对/重建原因的关键词降级，不标为无告警混合检索。

## 修复后验证要求

1. 同一 Ask/Work/工作流阶段内更新关键词阈值、重排绑定和记忆检索绑定，旧运行配置与记录不变，新运行采用新值；撤权仍立即拦截。
2. 用受控并发屏障在模型主体和工具能力读取间提交保存，确保返回完整旧配置或完整新配置，历史修订与能力一致。
3. 记忆查询同身份但维度改变时展示明确不兼容/关键词降级原因，验证不混查及历史证据保留。
4. 对修复另做独立代码复审和隔离环境验收；本报告的纯内存复现不能替代数据库并发、完整运行入口或真实供应商测试。


## 2026-11-03 追加修复独立复审

本次复审只核对R-01～03，不执行完整独立QA，不纳入仍在修改的MCP T-01～03、页面或外部供应商范围。没有修改实现。以下定向检查由独立审查者重新执行，不直接采用作者“25通过/2跳过”或“13单测”的通过结论。

在随机独立 `owner_agent_rdc_rereview_<随机>_test` 数据库从空库执行迁移，使用独立TEST_DATA_DIR及测试vault；结束后按限定名称校验并DROP该测试库。未连接生产业务库、未访问页面、未调用真实外部服务。

独立阅读 `test_rdc_retrieval_policy.py` 与 `test_model_snapshot_atomicity.py` 的数据、并发屏障和断言后，针对原复现条件独立运行：6项通过，8.37秒，无跳过。此处的通过只支持下述缺陷关闭。

- **R-01 CLOSED**：knowledge_policy首次使用固定阈值、检索模型绑定和处理参数，保存知识库配置修订/兼容摘要；memory_policy固定检索模型绑定，live启用状态仍单独检查。实际KnowledgeService.search分别在ask/work/workflow同一scope内，将阈值0改为0.9并增加重排绑定，重复查询仍保留原阈值和候选；新run采用新阈值/重排；原scope删除知识库仍被拒绝。记忆同scope由A切换B仍用A，新run用B，live disabled立即返回不可用。保存快照中的阈值、绑定与修订断言通过。
- **R-02 CLOSED**：ModelRegistry.get改为单条SELECT JOIN获取model_configs与model_capability_declarations，不再分短事务组装。实际PostgreSQL并发探针在首次读取SQL返回后、get后续处理前，用另一事务原子更新revision/temperature/supports_tools。返回与持久运行快照均保持完整旧revision1/toolsTrue；下一次get得到完整新revision2/toolsFalse。原混合配置复现条件已被关闭。
- **R-03 CLOSED**：eligible读取保存的vector_dimensions，recall在融合前对比实际query长度、保存维度和缓存vector长度。实际HTTP索引一条二维记忆后，受控向量替身将query改为三维；响应为keyword且warning明确包含维度不兼容与重新索引提示，不再返回无告警hybrid。

policy作者随后提供冻结清单 `services/api/.local/team-workspace/rdc/index/policy-author-evidence.json`，manifest摘要 `62219b2faf7ae8e4cb243b210ef8670e1fe4d96846360a9451bef6fdf1894a17`；独立核对5文件摘要无不一致。

本次核对的实现SHA256：

- retrieval_policy.py：25db2bc5696d2ca30f18e7db6468b9c88f32fcaa78c06ac5d9a96b989e73e5f1。
- knowledge.py：c62c43578625ac1405153e1573345c55e7b7ffbdbd92eb221ba5121866217bce。
- knowledge_usage.py：e7dcafb52931e6a94f7cd4b368f4efff598b2fecaf409ffdf0652a8536d60e88。
- memory_vectors.py：e9df9fc71827fc0a335077d96814923df1806372620a5219532c51dc23377fc0。
- models.py：c1e04561ac74e6e43c45e6e0ede23370580c3aa3aad3c98468098ef083ce25d5。

剩余gate：MCP追加修复最终冻结/独立复审、全RDC用例的独立隔离QA、此前被拒的实际页面访问，以及另行授权的真实外部联调。任何一项均不能由本节6项定向复审替代。


## root安全投影小改只读核查（未执行QA）

按根任务追加指派，只读核对safe_origin保留端口/IPv6、model服务目标摘要以及trace当前agent/memory/knowledge投影。源码中safe_origin使用parsed.hostname排除userinfo，将IPv6主机加方括号并附加有效端口，不带path/query；freeze_model和current model投影均用同一raw base_url计算service_target_digest，同host不同port/path可通过digest区分。current_configuration的agent/agent_version、memory、knowledge均限制owner，响应比较前调用jsonable_encoder，避免当前UUID/数值类型与历史JSON类型不一致造成伪差异。

本轮静态阅读未发现需要另报的具体问题；不是HTTP/页面通过结论。新增test_rdc_acceptance.py的cold阶段用例准备在获准QA后验证8443/9443的trace差异，尚未执行。所有后续前端构建必须使用独立PLATFORM_WEB_DIST_DIR或独立source；本轮未运行构建或读取被拒页面。
