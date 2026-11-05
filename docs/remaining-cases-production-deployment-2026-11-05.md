# 2026-11-05 剩余案例候选正式发布与回退方案

状态：只读盘点及方案完成；**尚未部署、备份、迁移或启停任何正式服务**。最终候选源码、提交、清单和启动入口仍待主协调者固定；本方案不预填当前HEAD为发布提交。20/20为限定受控场景覆盖，不等于20个业务目标全部成功、真实模型质量全部通过或持久评测驱动开放。

## 1. 目标及保护范围

- 正式入口 `http://127.0.0.1:3131`，API8101，原 `owner_agent`、原正式data与主密钥，保留现有账号/模型/会话/工作流及用户手动状态。
- 测试3100/API8100、已同步的3模型和独立测试key保持，不复制主密钥、不更改模型启停、不混用正式/测试目录或DB。正式更新只涉及已经验收、审查并冻结的API/Ask/UI候选。
- 执行开关维持当前：restricted_execution=true、ask_worker_enabled=true；work_enabled、automation_enabled、evaluation_enabled、media_worker_enabled均false。限定A20通过不授权开放Work、工作流执行、自动任务或平台评测队列。
- schema当前85，无新迁移预期。发现候选需要额外schema或后台作用则停止，重新提交迁移/兼容审查，不顺带自动升级。

## 2. 本轮只读生产盘点

实际独立收据 `.local/team-workspace/remaining-cases-20261105/ops/production-release-v1/readonly-inventory.json`，由同目录 `inventory.py` 读取当前CIM/监听、原生创建FILETIME及活句柄、只读数据库和HTTP健康生成。该盘点不登录、不请求模型、不改变DB或停止服务。

- 正式API实际2968，venv启动器40388；独立Ask实际14912，启动器16932；正式Web41680。均当前真实创建身份，不把这些PID当以后可直接操作的固定标识。未来停止必须重新核CIM命令、创建时间、父子及端口，持原生句柄再动作。
- API/Ask源为 `.local/environment-port-switch-20261105/frozen-96d70434385840519012d602ccc8dfd6/source/scripts/production_restricted_c85.py`，当前宿主SHA `e4bfc5751731a663f137b25955278ee7a97d86950bd739e6ca7e946abc4ca52e`；351旧源清单固定。正式Web仍原Uh42构建，正式private stop绑定仅以私有文件引用保留，不公开token。
- `owner_agent`迁移85，users1、models3、conversations14、task_runs23、work_queue_runs0、workflows1、workflow_runs0、model_usage61；新鲜服务API/Ask各1。当前执行中任务、未结束模型用量、非释放沙箱及评测批次均0。历史已结束UNKNOWN保留，不为了发布改成成功或0。
- 正式与测试两个页面和API ready均200。正式key摘要、模型整行加密记录摘要及namespace已在私有盘点封存。此刻盘点不是维护窗口的最终数据冻结，发布前须重新读取，尊重之后用户新增数据。

## 3. 冻结候选与发布前门槛

1. 主协调者固定canonical v2 CLI、Cookie/实例解析、Ask工厂和F07五API覆盖的**最终**源码及Git提交；完成非作者审查、必要工程/真实PG和CLI验证、产品限定签收。保留旧案例失败和UNKNOWN，不重算旧预算。
2. 创建独立不可变发布目录：完整API/Ask/CLI来源清单、依赖版本与实际导入路径、配对生产Web资产及18项required文件；只取明列覆盖，不拷贝开发私有app镜像。新Web包含已体验的UX01/02、评测取消交互修复，生产代理必须8101。3100的8100构建不能未经固定派生直接作为正式包。
3. 冻结独立冷启动入口，显式绑定原正式DB/data/key、3131/8101、原Cookie名和上述开关。入口不自动migrate、restore、重置stop标记或承接旧未知操作；候选SHA/文件集合/schema/设置不一致立即拒绝。
4. 候选在隔离克隆或明确零效果环境完成冷导入、实际schema85兼容和受限入口检查；核心Ask/SSE及管理影响面验证与真实外部质量分别记录。不得拿仅构建成功替代页面/实际业务验证。
5. 停止/启动/回退脚本、维护准入、快照/备份/恢复证明逐项独审，根以确切SHA和动作批准后方可执行。私有骨架 `release-contract.template.json` 的空值必须全部由最终来源替换并重新审查；不能以本方案当作已经批准部署。

版本登记还须绑定实际构建目录：现有 `scripts/platform_release.py:complete` 固定读取 `apps/web/.next/BUILD_ID`，本轮已验测试构建实际位于 `.next-release-20261105-model-empty`，ID为 `8bKgPg4Ln6KByjHAzSsb9`。不能登记旧.next的ID为本轮版本、手改BUILD_ID或伪造qa.browser/全范围通过。后续由根对工具做最小兼容扩展（如默认行为不变的已核web_dist参数），或采用等价受限发布流程，均需把版本化源码归档、构建证明、真实dist文件hash与实际served构建收据绑定并复审；正式8101派生包记录自己的真实构建ID。

最终3100页面回归必须针对新后端真实来源及已验8b界面组合重新验证。若正式3131浏览器工具仍明确权限拒绝，按用户允许跳过保留该未验项；实际代理登录、健康与资产校验可以分别完成，但不能冒充正式浏览器点击验收。

## 4. 最后维护窗口、离线备份

重新核正式当前原始数据/模型密文/key、活跃操作、服务/锁/监听与维护状态，登记本轮独占token和允许变化的维护字段；不使用早期备份覆盖当前用户状态。

待在途Ask及其他已授权操作自然终止，禁止主动重放未知调用。维护准入阻止新写后，只按当前精确身份合作停止Web3131、API8101和独立Ask。Web用当前private token/PID专用stop文件；API/Ask当前宿主用其原data/services/api.stop与ask.stop协议，核定绑定后才操作，等待wrapper/actual双方原生句柄退出。不得裸PID强杀、树杀或关闭其他项目。API stop文件不是Web nonce协议，二者不可混淆。

使用已审 `app.backup.create` 的原项目入口和明确生产Settings执行最后离线备份。该方法要求生产库名owner_agent、710014及710017排他锁、无新鲜API/Worker心跳或运行任务；未满足则等待/停止，不能绕过。注意：**不要从旧frozen源码直接调用该备份模块**，其模块ROOT会指向冻结包，可能错过原项目.env和sandbox文件；使用经审的原项目备份模块/明确原始来源，核备份实际目录和文件集。

备份包含当前一致database.dump、实际model.key及业务data、原项目API/admin/compose配置、sandbox配置/key、SQLite在线backup副本；全部存私有ACL，不进Git/聊天。核manifest及每文件摘要，确认关键目录无链接、完整解密密文兼容。需要恢复验证时仅新独立克隆，不对owner_agent运行restore，不强制drop身份不明库。最后冻结数据再次封存，记录备份本身允许的审计/服务变化，不能以过期计数硬覆盖新用户写入。

## 5. 实际更新与核验

确认离线备份已验后，归档本轮自有已消费stop标记；保留旧不可变源、原启动配置、旧Webdist与原数据。不得覆盖删除旧包或无条件清除他人的stop文件。采用新的本轮身份/日志/nonce，按新冷入口启动独立Ask、API8101和正式Web3131；服务顺序及唯一新鲜心跳、监听归属与真实import/source逐项核证。

启动前后只核schema85，不自动迁移。重新比较保护业务记录、模型密文、key与namespace；新服务心跳/服务审计、授权的最小登录会话变化分项记录，不能谎称每张运行表零变化。API/Ask真实native身份、source和release一致，旧wrapper/actual不再监听；Web全部引用JS/CSS与冻结清单相同，required manifests齐备。

做正式3131代理登录、管理列表、模型可见性、UX01/02文案及受限执行门禁核验；测试3100和API8100/同步模型/key也需原样健康。模型调用若非根明确批准的上线烟测不额外发起；外部费用、已验受控场景和SSE功能分别报告。产品最终核新正式版本的实际证据，失败不标记已上线完成。

## 6. 回退：保留最新用户数据

默认回退仅应用/配置/Web，**不回滚数据库、不恢复旧dump或旧data覆盖**。若新版本已接收用户写入，先再次维护/等待其操作终态并留最新证据，再合作停新实例，以已验schema85兼容的旧C85冻结宿主和配对旧Uh42 Web冷启，继续使用当前owner_agent及原key/data；保留新增题集、版本和所有UNKNOWN。不回退到schema79宿主或主动重投旧任务。

回退必须重新核当前进程原生身份和本轮token，保留失败新包/日志/维护状态。若正式环境身份、最新数据、数据库schema兼容或退出不明，维持维护/停止，通知根；不能为恢复200覆盖新数据、扩大开关或强杀未知进程。最后记录恢复的源码、构建、真实服务身份、健康/代理、保护数据及未覆盖功能。

## 7. 当前交付边界

本篇和私有盘点仅为发布准备。最终candidate commit、API/Ask冷入口、新生产8101 Web清单、维护/停止/新启/回退脚本和最后备份均**待根固定来源并另准入**。此时没有进行正式发布动作，也没有改测试环境模型/主密钥。后续Git推送可沿已验证的单命令proxy设置，由根记录分支/commit/实际远端结果，不改全局代理。

## 8. 发布登记工具的实际构建目录校验

现有 `platform_release.complete` 固定读取 `apps/web/.next/BUILD_ID`，不能用于本轮已独立部署的自定义冻结dist，否则登记的构建可能属于另一旧产物。主根负责最小适配：新增仅complete使用的可选 `--web-dist`，默认旧调用保持。显式目录必须位于项目根内、不是符号链接或junction；读取其真实BUILD_ID，并同时匹配QA的web_build_id、部署的build_id/web_dist和健康记录的web_build_id。缺失或不一致拒绝登记，不复制或改写BUILD_ID伪造产物。

原Git标签/源码归档一致性、独立测试及浏览器验收、完整部署和健康门禁全部保留；新参数不能把未验浏览器、部分部署或未提交源码标成通过。先用隔离假仓库验证旧行为、自定义正确目录、错误ID/目录/链接拒绝，再独审；正式记录仍须后续真实证据。
