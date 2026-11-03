# BUG-EXP007 固定部署控制器的实例隔离

2026-11-02，作者expansion_ops。当前生产数据、用户已确认的工作流删除和知识库绑定均保留；本次未部署、未启停生产或修改用户.env。

## 问题与修复

ManagedServices接收独立Settings，但原来始终调用项目固定PowerShell控制脚本；这些脚本的API PID、就绪端口与运行目录针对默认部署。独立8139/test数据库若发起在线备份，就可能控制默认生产后台，不能仅在文档注明不支持。

新增ServiceControlProfileError及统一require_profile。snapshot、command、control、stop、start在读取manifest/stop标记、创建目录/control.log或启动控制进程之前检查：

- 使用dotenv_values(interpolate=False)读取固定scripts所属services/api/.env，不载入或修改全局环境。
- 必须有明确DATABASE_URL；通过conninfo_to_dict规范化比较完整连接参数，包含显式host/port/dbname/user。不在异常中回显DSN。
- 数据目录必须绝对路径，比较resolve后的持久目录。相对目录明确拒绝，避免PowerShell工作目录导致错指向。未配置时沿现有默认data_dir。
- 持久内部端点必须是当前固定脚本支持的默认http://127.0.0.1:8100，Settings及进程继承环境的DATABASE_URL/PLATFORM_DATA_DIR/PLATFORM_INTERNAL_API_URL同时匹配该持久profile。不能仅让显式Settings匹配、却允许子进程继承另一测试配置。
- 缺失/非法/不匹配统一抛SERVICE_CONTROL_PROFILE_MISMATCH，隐藏解析配置与凭证，不尝试猜测或创建多站点控制器。

OperationsWorker.claim只捕获这一特定错误类型，将已排队job持久标为FAILED、phase=ADMISSION、reason=SERVICE_CONTROL_PROFILE_MISMATCH并结束，不设置maintenance、不生成resume_services或启停标记。不吞其它数据库/文件错误，也不令job永远PENDING循环报错。原services=Services()替身注入保留，无新增构造参数。

ready改沿Settings.internal_api_url探测，明确trust_env=false、follow_redirects=false；302不会转向另一个站点。该探测不携凭证或租约。备份页面新增上述reason的中文说明，不改布局：独立数据库/目录/端口不能使用固定部署脚本，已在准入停止且未启停其它站点。

## 作者验证

- 新随机owner_agent_ops_<id>_test数据库、独立tmp数据目录和8139 Settings；真实FastAPI TestClient通过管理员登录/备份POST得到202，再用真实ManagedServices的worker.claim处理。
- 最终API查询job为FAILED/ADMISSION/SERVICE_CONTROL_PROFILE_MISMATCH；maintenance=false，第二次claim不再拿到该job。Popen sentinel调用0，数据目录文件树无新增、无*.stop标记；未连接或调用生产控制脚本。
- 单元覆盖全部五个入口（包括空列表）的副作用前拒绝、数据库/目录/端点各自独立不匹配、继承环境污染拒绝；原默认持久profile用临时持久.env核对，人工停止保护及真实Windows控制器返回/子进程句柄用例保留。
- 非默认ready请求固定留在8139，代理与重定向关闭，第一次302不会请求8100 decoy。
- 18项定向回归通过；Ruff、TypeScript及备份组件ESLint通过。证据 `.local/team-workspace/platform-expansion/operations/managed-profile-final.log`、managed-profile-types.log、managed-profile-eslint.log。

本次作者自验完成，尚待独立审和QA实际独立站点复测。中文文案属于Web代码，需实际构建才上线；不能只重启后端就声称文案已更新。默认生产的真正在线备份正向链路由后续受审、受控的本机验收执行，不以本文隔离负向结果冒充。
