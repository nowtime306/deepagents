# 工作流版本管理前端独立代码审查

2026-11-03。审查者：产品子角色，未参与本批业务前端实现；作者为根协调者。依据 WVR-01～08，审查 workflows.tsx 新增版本动作与 workflow-versions.tsx/CSS，保留所有已有改动。此处状态是独立审查事实；最终作者自验和源码哈希尚待冻结，当前不交QA执行新前端。

## 发现与修复记录

### WVR-FE01 / P2：只读预览仍可保存本地草稿

dirty草稿进入历史预览后，原新增条件未阻止保存按钮，违反只读预览期间不写的交互。作者改为使用完整locked守卫，覆盖versionPreview/previewLoading。静态复核通过；需组件反例确认预览没有PUT，返回编辑仍保留未保存草稿。

### WVR-FE02 / P2：空布局历史复用其它版本坐标

WorkflowEditor内部布局采用layout/previous.position/default；同一实例切换无自定义layout版本会继承当前草稿或另一版本的位置，与默认布局提示不符。作者改为draft/preview-loading/preview-N/published-N分离key，父层草稿definition/layout仍保留。静态复核通过；需组件反例验证旧空布局默认位置及返回编辑原坐标。

### WVR-FE03 / P2：写入成功后刷新失败被提示为整个操作失败

restore的POST已原子恢复并生成备份，但随后GET原工作流/历史任一失败会返回false、保留旧弹窗且不显示备份，可能引导重试。copy也在POST创建成功后因reload失败返回false。作者已改为restore使用响应workflow立即更新与显示备份，copy立即关联新副本，后续刷新错误明确标“已成功但刷新失败”，不返回可重复提交状态。静态增量复核已见实现；待后端完整workflow返回契约及真实作者反例完成后终验。

### WVR-FE04 / P2：历史预览与运行快照没有互斥

预览发布vN后进入运行历史并点某run，旧handler不清versionPreview，而canvasDefinition优先历史预览definition，运行事件将叠加在错误版本画布。已反馈作者：选择具体运行时应中断/清除preview请求和状态，进入该run自身snapshot；保留父层本地草稿。作者已将具体run选择handler改为先editMode()清除预览/中断请求，再选择run；静态复核通过，待结构不同快照反例。

## 持续核查范围

- 历史只读/按钮权限；恢复先CAS保存dirty再事务备份，失败保留编辑；复制不跳走/丢dirty。
- 当前发布版与active分离，运行当前先读取实际pointer并选定原显式version入口，不影响固定Key/Agent/subflow版本。
- 迟到预览Abort+sequence+openedId防护，返回编辑无修改写入；unsupported raw历史只读，规范化canvas_definition只用于投影，不改原快照。
- 窄屏侧栏和弹窗；新动作仅此功能，不改Ask SSE或工作流原运行协议。

## 放行状态

尚未放行：FE01～04静态修复已通过，需最终作者反例测试/生产构建证据，最终3个生产前端文件哈希待记录。此前旧全量测试不能作为本次通过证明。作者完成自验后按最终源码增量复审，再由独立QA运行。
