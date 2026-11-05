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

## 9. TEST API 更新后的独立运维核验（2026-11-05 21:27 北京时间）

此节更新前文的准备阶段状态：主协调者已单次冻结候选 `9f1c53f2d7ed2f0f897ed405ff6ffe55e059f60e`，并完成 TEST API8100 替换；正式3131/API8101此时仍是原C85应用，未进入本轮正式更新。测试Web3100是已独审的新XQ构建，正式候选Web为Cq/8101包，不能用旧I1或8b源证明替代。

第一次TEST stop在创建marker前因原data/services父不存在失败，原claim/现场保留。主协调者核验原UUID租约、目录身份后仅建空TEST父目录，再用独立v3包的同字节controller/host/合同执行新的stop/launch；没有重跑旧claim、重新进入维护或恢复旧数据库。独立运维收据为 `.local/team-workspace/remaining-cases-20261105/ops/test-api-v020-v3/ops-independent-postlaunch.json`。

- 新TEST wrapper41536/实际23308的原生创建FILETIME、CIM参数和唯一8100监听均与启动收据一致；源清单414文件、manifest摘要 `c373d8b46febe04256160f0bbf63b09f71f085fc480b3672034c88b55e36897b` 与已审合同一致，schema85，注册服务仅该候选API。受限Settings绑定保持Ask inline（独立Ask worker=false），Work/自动任务/评测/media均false、测试cookie独立。该配置证据来自不可变宿主/Settings与真实进程参数/注册信息，不声称读取了进程内存。
- 原3个模型、独立key及原3次KNOWN调用的逐call归属与输入142/输出0保持；新旧停止前全部业务快照相等，launch收据在代码强制启动后全表等值检查通过后生成。产品并行授权登录随后合法改变auth_sessions和auth_rate_windows，单独记为登录元数据；其他受保护业务表仍全部等值，不删除登录或用量记录以追求零变化。
- 测试维护状态已通过原UUID的精确CAS释放，lease/job均NULL；8100 ready、3100页面及代理注册配置GET、8101 ready、3131页面均200。本运维检查没有登录、模型调用或浏览器操作；产品页面及真实Ask SSE验收另行记录。
- 正式原5个进程的原生身份及原库模型/用量/schema/key与冻结时一致。正式data/services父目录已存在且无链接，api.stop/ask.stop均无；其中17个既有控制.lock/.log/.json文件必须保留，目录并非空目录，也无需为写本轮exclusive stop marker而清空。此事实避免重复TEST父目录缺失问题，不授权删除原文件。

正式维护、停止、离线备份/clone验证、新源发布及最终版本登记仍须等待TEST页面与后续准入。以上当前快照不能替代实际正式维护窗口的GATE drain、原生身份及数据重新核验；本次文档更新未提交。

## 10. 正式新版本启动后的独立运维核验（2026-11-05 22:01 北京时间）

主协调者已按独审合同完成正式维护准入、五个旧进程合作退出及真实离线备份，再单独完成runtime复核和新版本启动；本节替代前述“正式尚未更新”的阶段状态。候选仍为 `9f1c53f2d7ed2f0f897ed405ff6ffe55e059f60e`，原owner_agent/schema85/data/key继续使用，未恢复旧数据库覆盖新数据。

原备份验证第一次因65字节clone名称超过PostgreSQL的63字节限制、被截断而失败。原失败claim/intent/截断库和真实备份均保留；独立审查后的最小恢复脚本仅复用原dump，创建57字节的新clone并实际核对current_database、全部manifest表数量、精确85迁移名称和模型密钥解密后，写明恢复过程的真实收据。没有再次备份、迁移、drop或伪造旧失败为成功。新clone为 `owner_agent_release_restore_c727ea610b3e4f6fa65d4ea5_test`，真实备份manifest SHA为 `6d637f7d8fd960ac5c927f403b04da9a5548b3faeff11220a39ec877da8c7454`。

独立收据 `.local/team-workspace/remaining-cases-20261105/ops/production-release-v2/ops-independent-postlaunch.json` 已核：新API36732/wrapper37296、Ask39768/wrapper14028及Web20928的原生FILETIME/参数/父子关系，8101/3131唯一监听；API/Ask唯一新鲜注册均绑定该候选，414文件冻结源和冷入口import-origin守卫、Cq实际构建、1137运行资产加2配置及18必需文件均匹配。受限开关保持Ask独立worker=true、Work/自动任务/评测/media=false，原生产cookie保留。

正式登录前另封存142个保护表的原始快照 `ops-raw-postlaunch-business.json`（SHA `f07ee2d39eb16a292fa4877a819cf9a31613d3ae9399c1e75112178b943a5558`）：仅ask_worker_instances因新Ask心跳注册改变，其余141表完全同停止前。主协调者后续授权登录触及auth_rate_windows，单独记作认证元数据；当前其余保护业务表、3模型、61用量、原key均不变。正式维护已释放、lease/job均NULL，备份文件重新校验通过，新旧clone均保留。

TEST正常保留3模型和4次KNOWN调用，合计输入202/输出17（含主协调者另行批准的真实SSE调用60/17）；原3条RAG调用与两套key保留。独立运维GET核正式ready/首页/代理注册配置/真实Cq静态manifest和TEST ready/首页均200，没有登录、模型调用、浏览器操作或服务/数据库写入。

此为部署与数据保护实证，不替代最终正式浏览器、业务质量或版本登记；模型字面响应质量、限定受控案例与完整业务覆盖须分别说明。最终上线验收和版本记录由主协调者依据实际证据完成，旧故障与未覆盖项继续保留。
