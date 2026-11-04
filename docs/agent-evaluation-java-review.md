# W02 保护验证器审查（进行中）

## 当前阻断：JV05 输出正确但方法未返回

2026-11-04 独立物理诊断确认：候选 `max` 先打印正确数值再 `System.exit(0)`，原验证器把三例均判通过，但方法没有返回结果。原证据 `.local/agent-evaluations/java-oracle-independent-e7019ad4dece45dbb168a249b3103ce3/receipt.json` 保持 `passed=false`、`gate=ORACLE_FALSE_POSITIVE_CONFIRMED`，不回填历史八控制或计入 W02 业务验收。一个自有 Coding 沙箱已释放，同命名空间容器及侧车末次清单为零。

最初修复候选在私有目录 `services/evaluation/.local/java-oracle-fix/`，清单摘要 `fac685157882ff1da0c0058cd53ccdd3f3d72af27b73a66a65ccf32a93895b59`。非作者审查发现探针在封存前断言会丢失失败证据（O01/P2），第二版先保存原报告/摘要/预期与实际值后再断言。原候选、原错误与审查记录保留。

第二版冻结 `java-oracle-fix-v2/manifest.json` 摘要 `fece23f251389623ab05ce8825b04899498da71f1eeedb438b9f9013b312242f`，O01关闭，独立工程12项通过；包含旧8项，不相加。实际新 Coding 中十一组固定源码按预期：正确方法通过；数值错误、假日志、签名错误及退出/halt/移除guard企图均失败，后五种终止企图编译成功且每种三次真实退出1。限定 JDK21 在候选初始化前安装guard，拒绝exit/halt和替换guard，保留原UID/seccomp/文件保护；不宣称通用恶意Java安全。

实际原件 `.local/agent-evaluations/java-oracle-fixed-host-9e2feff3067f445880ab78dee0999b48/receipt.json` 摘要 `d3acba90544d51983234e29026623bce72eb4dba10ed8cfbdf811422743bf624`，gate PASS、十一控制、一个4 CPU/5 GiB Coding及其侧车已释放，精确本次命名空间库存为零，源码/素材/脚本摘要前后一致。最初受限运行未写任何创建意图，核对精确进程后停止，空诊断目录保留；实际验证使用正常审批的本机服务访问权限，没有重放创建。

active程序已精确更新为实测字节 SHA256 `2c91f045fd9bdd7713255efed8b52588464c07bddad190d21d0b743008f14a62`；通用命令原始125仍保留，guard拒绝的exit(125)在本新策略下实际为异常退出1，不回写旧125探针。**W02仍未完成正式Agent→成果→第二leased验证任务验收，不进入READY，也不增加8/20任务覆盖。**

日期：2026-11-04；审查者非实现作者。范围为固定目标模块、Linux 保护程序、launcher、对应测试和需求，不包含共享接线或真实 W02。

1. **JV01 保护边界**：仅降低 UID 不能证明生成代码无法通过容器内控制接口修改保护测试。补充固定 amd64 ABI 的继承式 seccomp/no_new_privs，拒绝网络及相关旁路，关闭继承 socket；root SDK 控制通道不受此子进程过滤影响。强制 post-exec 的 UID/GID、网络拒绝、文件/父目录/编译结果保护探针；缺失不能 PASS。Docker 出站规则本身不被当作此证明。[OpenSandbox 官方 Execd 文档](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/data-plane/execd.md)说明了可选 token 与控制接口边界。
2. **JV02 模型身份**：相同 `eval::AET-W02::v1` 字符串不能替代封存模型 ID。job 记录 source_model_id，执行前与 source row、真实 run、owner/W02/lead 密封绑定逐项一致，不符时零沙箱创建。
3. **JV03 退出码分类**：候选可自行退出 125，该值不能自动证明 launcher 未执行或属于环境故障。普通命令保留原退出码/输出与事实失败；最前的必需保护探针仍须通过。修复及反例待最终冻结核验。

叠加冻结 `java-verifier-review-fix/manifest.json` SHA256 `5d31324b6a976c3e9cfec0093d55d4cb2736f865cf7ee6bab867fe9439e6dfb2` 的 JV01/JV02 实现已静态核对：ABI/x32/兼容路径和网络、io_uring/FD 入口过滤，NNP、关闭额外组/继承 FD、父子校验及 typed 报告均存在。numeric UID 1000 不假定镜像已有命名账户；冲突账户拒绝，不修改镜像或宿主。

作者 45 项反馈不是独立 QA，也不证明 Linux 内核过滤或真实 JDK 已运行。需要最终修复冻结、独立工程测试、单独隔离 Coding 保护探针，然后再接真实 Agent→成果→第二 leased 验证任务的业务链路。探针最多一个 Coding 沙箱，管理器自动侧车单独记录；沿用原服务和资源限制，不建立第二管理器。

本页尚不关闭物理保护门禁、不宣称全部候选代码安全、W02 任务验收或生产上线。原冻结、失败日志和后续收据分别保留。

## 最终增量与独立工程结果

JV03 最小修复冻结 `java-verifier-exit125-fix/manifest.json` SHA256 `47a20a4798eccb268c936b8b605e5c0a8fe2301d12b93f2bfe960b66fbc893ae`：移除以退出 125 推断隔离未执行的分支，最初必需保护证明仍不可缺。JV01/JV02/JV03 代码审查关闭。非作者使用 API 独立环境执行指定文件 **47 通过、0 跳过**（0.92 秒），五项产品摘要一致；原始 summary 中为 Windows 标准库认证 socketpair 的 54 次本机连接，其他网络事件0，无宿主 Java/内核操作。证据 `.local/agent-evaluations/java-independent-4461a2dd6d3148db96d0515f0fb2eb5c/`。首轮私有审计错误拦截 asyncio 自管连接导致 26 失败、20 通过、11 错误，原日志/JUnit保留，不作产品失败或累加通过数。

随后非作者实际执行独立 Linux/Coding 工程探针，最终 `.local/agent-evaluations/java-linux-final-019e8789346d416a8f51456e4ee76403/` 五组固定源码的预期判定全部符合：正确修复三例全通过；原错误源码、伪造“tests passed”、System.exit(0)、System.exit(125) 均是实际失败。后两组原退出码保留，不借日志或退出0通过。每组均实际核 JDK21、post-exec UID/GID1000、NNP/seccomp、网络/控制端口拒绝和私有比较器/编译产物的不可改写探针。

一个 Coding 沙箱配原管理器自动 egress 侧车，4 CPU/5 GiB、镜像 `sha256:459420e199a24347bed6091ca0683cc570e7fd4c3737f086c3b43533c89c7ce9`。sandbox `d42c3761-e9c9-4c37-b8e0-652ce111eb6c` 及本次 namespace 的管理器/Docker/侧车清单均精确回收为0；五产品文件、脚本和五素材摘要前后一致。完成收据 SHA256 `50dee9703123588cb724f9145fd3ebe43ab448001b17130da697bd7dd0f1f51d`。此范围物理探针通过，不是通用安全证明或真实 W02 Agent 任务。

前两次独立探针失败原样保留：`java-linux-independent-c78d689d936f4749bfad5e2404737d2c` 在私有 Docker 信息模板缺括号处停止，0 个 Java 控制；`java-linux-recheck-32af09cc24164f7085b8d495c9d54896` 仅正确控制通过后归档失败，不能算五组通过。仅修私有脚本并本机核模板/精确归档后，用新 namespace 重测；产品五文件未改。三次各自至多创建一个 Coding，均已清理，没有重放旧创建或新管理器。

下一步仍需候选 Agent 正式执行、下载源码差异、第二个已租约验证任务、两根用量/会话和 reader/CLEAN 的完整共享接线验收；当前 W02 不在 READY 清单，没有生产部署。

## JV04：公开 Java 签名保护

根角色发现数值比较不能证明保留原 `public static int Max.max(int,int)` 接口：同包调用可能容忍非 public 方法、long 返回值；保护 adapter 声明 throws 后也不能放过新引入的 checked throws。最小修复在调用前反射检查 public 类、精确两个 int 参数、public/static 方法、int 返回及无声明异常。按原无 throws 接口保守拒绝声明异常，不重写候选源码或信任候选自己报告签名。

新五文件冻结 `services/evaluation/.local/java-verifier-signature-freeze/manifest.json` SHA256 `bc2f150650d7995a78f7adac95981ecb6346d4df03e74228232dd8a6fba38eda`，PROGRAM SHA256 `2c7f26f3022590445b2dde70ef2ad3f110c1487e9646cc51e4b045283e0874c1`。service/launcher 与旧冻结字节相同。非作者先代码审查关闭，再独立指定工程文件 **48通过、0跳过**及6个协议反例；48含旧47，不相加。协议替身不证明实际 Java 反射行为。证据 `services/evaluation/.local/java-signature-independent-qa/run_8b448f1564b6/`，完成收据 `8e3a7643848745501353a3bcbde64cf2b2d2946f36172da7346aaab22eea96ac`。

随后根角色在一个全新 Coding 中实际运行八组源码，保留原五组并增加 package-private 方法、long 返回和声明 ReflectiveOperationException 的反例。三反例的候选/adapter 编译均成功，九次调用实际由签名 guard 拒绝；数值正确不能掩盖接口错误。八组结果、JDK21、UID/GID、继承隔离及私有测试保护均按预期，八次归档核对通过。一个 Coding＋管理器自动侧车，原固定镜像4 CPU/5 GiB、同 namespace/session，已精确释放且末次三类库存0；程序、素材、脚本前后摘要一致。

原件 `.local/agent-evaluations/java-signature-independent-d705aca917424f11b07c52285423e2a7/receipt.json` SHA256 `43371a82abaddb89c010458f33d91b6c911ea47141a2bdc5b1e0923e90f2b586`。另一角色只读交叉核验原八报告和签名异常摘要、冻结与资源收据，没有重跑 Java 或调用目标；结果 `services/evaluation/.local/java-signature-real-independent-review/review_071933e7260d/verification.json` SHA256 `87cf202ca5843d5347753db6f5e0122f399be41b09101af20598c369e6e07674`。最初一次根 shell 启动因变量名不存在，在启动 Python/创建资源前失败；改用脚本实际变量后才执行这一新探针，不计为 Java 产品失败或额外通过。

JV04 在此限定工程范围关闭。旧五组原件与历史失败不回填新签名证明；W02 的真实 Agent/两根 Work 链路仍未验收，未增加 A20 已验收案例，没有新部署或正式标签。
