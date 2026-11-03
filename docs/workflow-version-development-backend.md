# 工作流版本管理后端实施

依据workflow-version-requirements.md WVR-01~07。当前Git baseline由根stage，子任务不操作Git；本次文件与原副本在.local/team-workspace/workflow-version/backend。先080迁移→独立历史路由服务→发布/上下架事务增量→隔离自验→独立代码审查→QA。

080为workflows增加nullable current_published_version、独立release_revision默认1；旧记录不猜最高active。workflow_versions增加layout默认{}，仅新发布保存真实坐标。current复合外键限定本流程发布号。active与current不同。

API：GET /workflows/{id}当前草稿；GET /{id}/versions/{n}完整raw历史配置+layout_known/schema_supported/current元信息。POST /versions/{n}/set-current {release_revision}，行锁+CAS、active/当前支持schema/严格pins匹配后只改指针。POST /restore {revision}，先CAS后在同一事务另存原已存草稿为独立revision1未测workflow备份，再还原历史配置到原draft revision+1、tested空；返回draft_backup_id/name。POST /copy {name}，独立未测未发布草稿，不复制运行/key/权限。前端本地dirty先保存成功取新revision，服务端备份这份状态后才覆盖。

历史模型/资源失效时恢复复制允许待修，不替换ID或复制密钥；当前编辑器已不支持的结构/Schema明确422并保持原草稿。历史预览不执行或改数据；无layout不从当前草稿借用，明确默认自动排版。name冲突409整个事务回滚。

新publish记录layout、设新current并推进release_revision；同修订幂等publish只返回原号，不改pointer/active。下线current事务内清指针并推进release_revision，上线不自动设current。原显式version执行、外部key/subflow/Agent pin、已有运行快照完全不重写。不新增current/runs，不改Ask/Work/SSE。

验证：历史完整性/布局、v2→v1current不碰draft与旧显式调用、CAS冲突和并发、原子备份可找回/失败无备份、copy独立和缺资源待修、unsupported schema拒绝、资源改版/inactive不得setcurrent、读写权限/跨owner、旧发布/节点/运行回归。仅隔离库与本机替身模型，不调用真实外部模型。

## 当前实现与作者交接结果

新增workflow_versions.py及080迁移，现有workflow_api.py仅发布/版本列表/上下架加入精确增量，main.py仅注册新router。080旧记录current为NULL、不猜历史最高active，所有旧published内容/active与resource pins原值保留；旧无layout明确为空。新publish存layout并设current，幂等publish返回created=false且不改变指针或active。

新增12用例：历史完整预览、layout/current、独立draft/release CAS、原子备份可找回及名称冲突整事务回滚、inactive复制/恢复、旧布局不借当前图、不支持schema拒绝、资源变更不得设current但可待修草稿、并发CAS、跨owner/只读角色、current复合外键删除兼容、真正API Key/子流程/授权Ask固定v2在current切v1后仍运行v2、管理操作不调用模型/不新增run/usage/key。

默认工具网络沙箱连接localhost PostgreSQL受限，作者首次0用例未执行；未尝试旧escalation，由根在新全权限环境代跑同一隔离runner。首次新10项通过11.35秒；补完12项后相关broad批次169项通过、0跳过，228.49秒（含先前模板/节点/实际Coding审批/Ask/Agent/习惯等）。12项在169内，不相加为181。根代跑结果2adbf7c23b日志与json留存。

该broad全源frozen=false，因为根前端并行开发；根与作者均逐项核验全部services/api/app哈希无变更(backend_app_changed=[]），不声称全源不变。当前5个目标代码/迁移/测试SHA在freeze.json，后续独立QA应等前端稳定后全源冻结执行。定向Ruff通过。

子任务未操作Git/index/提交推送、未改用户数据库/密钥/当前服务，真实外部模型跳过。原有已发布运行及外部凭据固定版本不重写。当前只完成作者开发与代跑自验，等待根独立代码审查放行后QA验证，不把本记录当QA通过或部署声明。

## 独立审查修复 WVR-BE01 / FE03

025 迁移为既有发布补的节点省略 parameters/input；不可变历史保持该原始结构，但编辑器需要完整默认字段。先新增真实 API 反例：以025精确bare定义代入隔离历史，验证详情/列表 raw.definition 不变且 canvas_definition 可直接渲染；恢复与复制后的 GET 草稿补全默认值、不执行模型、不新增运行用量。支持的历史用同一个 editable 核心规范化画布，不支持的历史明确 schema_supported=false/canvas_definition=null。GET 草稿只在响应中补默认值，不回写DB。

恢复成功响应追加完整 workflow（同事务 UPDATE RETURNING * 后规范化），保持原 revision/backup 字段；与后续 GET 相同，前端不依赖第二次刷新来判定恢复成功，避免刷新失败导致重复 POST。本项只改响应投影、不更改历史执行/指针/事务语义。

WVR-BE01 先反例实际复现：隔离API单case失败 KeyError canvas_definition，1 failed in 7.13s、源未变，记录8b93d73063。修复后根代跑完整新版本模块13项全部通过，17.85秒、frozen_sources_unchanged=true，记录fa3438fb75（根会话52356）；定向Ruff通过。当前5目标精确SHA另存freeze-reviewfix.json，原169项broad是修复前记录，不作为最终修复源已跑169的声明。等待根独立复审和QA，不宣称已部署。
