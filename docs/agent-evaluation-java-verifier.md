# W02 Java 保护验证器

本增量只扩展已隔离评测实例中的AET-W02固定工程case。候选必须经正式Task/Run HTTP、真实Work队列及原WorkEngine执行；不允许直接运行答案文件代替Agent任务，不在宿主javac/java。当前阶段仅开发和工程自验，真实W02须另经独审、固定候选与执行授权。

候选结束后，校验正式成果Max.java的owner、source run/snapshot、大小及实际字节摘要，并要求候选原生用量已知、session全部RELEASED。正式API创建第二个独立空Work task，目标专用接口仅封存taskID→sourceRun/artifactID/SHA256/owner/namespace，禁止任意command、SQL、测试、hostpath或Python模块。该task的正式run由原WorkWorker正常claim/续租，同一个BoundedSandboxes凭真实RUNNING lease创建第二个Coding沙箱。禁止借候选已释放租约、手造RUNNING行或另起管理器。

target-only固定executor按封存taskID分流，不依据名称或goal；验证器不调用模型。原18180管理器共享使用，新DB/owner/data/namespace隔离，最多一个Work且目标持久最多2次沙箱预约（Agent1+verifier1）。远端未知不退款、不重放、不加第三个沙箱。无需新数据库schema或常驻服务；持久绑定/状态放固定私有data文件，源/模型/共享接线由各自owner另行审查。

保护测试和比较器只进入第二个沙箱的root私有目录，绝不上传到候选Agent的workspace或输入。候选源码只从UUID成果存储读取，实际字节必须匹配封存摘要，最多64KiB UTF8，固定文件名Max.java。编译/执行生成的Java均在该Coding内降UID/GID1000，清空环境且限制输出/时长；javac禁注解处理器。可信adapter和比较器不接受候选替换；候选class编译后只读，每例新JVM，实际结果由独立Python比较器核对，不能用候选日志或Java自报PASS替代。

所有javac（包括可信adapter编译）、java、版本检查及权限子探针都由固定Python以`user=1000,group=1000,extra_groups=[]`启动，没有root Java执行分支。编译完成后才由root封class只读，root只负责固定文件/权限布置、监督及比较。这里使用Unix纯numeric UID/GID1000，不宣称镜像已经存在命名账户，也不执行useradd或修改镜像。程序仍实际查询passwd/group：无1000条目允许numeric模式；若已有条目，只接受明确的非root ae-verifier及GID1000，冲突阻断。权限探针实际核子进程getuid/getgid、CWD=/workspace、该workspace可写、root私有比较器与父目录不可替换，以及编译后class不可改；候选执行后再次核受保护文件摘要。

### 控制平面回路保护（待真实Linux独立探针）

仅降权或证明直接写文件PermissionError不足。Execd默认44772，token保护需由平台启用；本机Docker后端明确不支持secureAccess。本次审查只确认保护边界缺口，没有运行远程提权攻击或证明漏洞已被利用。[Execd官方说明](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/data-plane/execd.md)

固定root supervisor本身保留SDK控制通道；其UID1000子进程必须先运行只读launcher，设置no_new_privs及固定Linux/amd64 seccomp BPF后exec。filter随fork/exec继承，拒socket/connect/socketpair等网络系统调用、io_uring三入口及pidfd_getfd；非amd64、x32位和旧compat编号路径均拒绝。不支持的架构、安装失败或探针不完整为BLOCK/REVIEW，不能为让JDK通过而动态放行控制端口。Popen显式close_fds及stdin=DEVNULL，stdout/stderr仅普通临时文件，防继承socket FD。该机制仅声明候选子进程的系统调用约束，不是平台/宿主全局隔离证明。[Linux内核seccomp说明](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html)、[x86系统调用表](https://raw.githubusercontent.com/torvalds/linux/master/arch/x86/entry/syscalls/syscall_64.tbl)

固定探针须在一次exec后的子进程中核seccomp/no_new_privs、IPv4/IPv6/socketpair、44772连接尝试、原始connect、x32及安全compat getpid均EPERM，且正常native getpid与文件正探针可用。不发送Execd HTTP请求、命令或认证数据；若socket意外可创建，立即停止其余网络探针。正确Java必须另在同过滤条件真实通过。程序/报告协议升为java-max-v2，launcher源码摘要和精确拒绝证据是必需项，旧权限探针不能借用为新保护证明。

接口必须精确为public类Max中声明的`public static int max(int,int)`，并严格不声明任何throws（包括checked及unchecked异常）。可信只读adapter在调用前用getDeclaredMethod精确核两个int参数、类public、方法public/static、返回类型恰为int，以及getExceptionTypes为空；不能因同包可见、long重载打印或adapter自身允许反射异常而放过接口不兼容。参数正确但package-private、返回long或声明ReflectiveOperationException的源码也应FAIL。反射检查本身不授权root执行Java，仍在同一UID1000/seccomp子进程中运行。新增三个接口反例须另经真实隔离工程探针；原47工程及五源码物理探针不回填为新签名覆盖。

固定保护例为`max(2,8)=8`、`max(-7,-3)=-3`、`max(4,4)=4`。这是三个测试的结果，不是通用Java正确性或安全证明。编译失败、签名不符、非整数/额外输出、超时或结果错误应保留FAIL；SDK传输或结果封存不确定为NEEDS_REVIEW。硬编码“测试通过”及System.exit(0)不构成结果。

JDK21固定adapter在首次引用候选前安装同类SecurityManager并核对身份，拒绝退出/halt和替换guard；测试命令显式使用`-Djava.security.manager=allow`。这是针对先打印正确值再提前退出的已复现误判修复，不支持换用移除该能力的JDK版本。[JDK21 System](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/System.html)和[Runtime](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Runtime.html)说明退出及halt经过checkExit；现有OS隔离仍必需。十一控制实际结果见专项审查记录，不当作W02业务验收。

退出码125也只作为普通命令的已知失败结果保留，不能仅凭它推断launcher未执行或把候选System.exit(125)改成环境UNKNOWN。初始隔离/文件/编译产物探针仍强制成功，缺失或失败不得进入可通过判定；本轮没有增加逐次启动握手协议来区分共享退出码。

真实保护探针必须证明候选执行UID1000，尝试改写root比较器被拒绝且其摘要不变；该探针只能在隔离Coding验证，Windows工程替身不算这一门禁通过。保护目录/adapter只读条件、结果封存和降权失败均应阻断。没有真实探针的工程测试不得宣称受保护验证器已验收。

证据绑定候选/验证器run、task、owner、namespace、session、实际sandbox/image、sourceArtifactDigest、固定测试/程序摘要、权限探针、编译/每例退出码和输出摘要。未知、活动操作或未释放session不CLEAN；两运行已结清、各session RELEASED、namespace库存0及事实/ready可独立重算后，才进入现有合作stop、独立reader与精确DROP。保留原始FAIL，预期故障控制正确不得把任务改成PASS。

## 最小共享接线合同（尚未接线）

runtime在受控target生命周期打开DB/models、构造同一个BoundedSandboxes及ProfileState之后构造`JavaVerifier(database,sandboxes,artifacts,models,profiles,environment_id=...,state_path=data/'evaluation-java-verifiers.json')`，**在任何Work任务入队前await initialize()**。只有全新、尚无Work task/run的库可创建缺失注册表；已使用目标丢失注册表时阻断，不能悄悄分流回普通Agent。用`attach_java_verifier(app,service)`挂目标专用封存接口，不能挂production create_app。

同一个WorkWorker的execute回调按`service.handles(run['task_id'])`决定：封存task执行`service.execute(run)`，普通task仍走原engine.execute(run)。不能另开worker/manager或手改run状态生成租约。第二个正式run仍使用已启用的受控W02模型ID以满足现有正式API契约，但固定executor不invoke模型，不制造用量。模型选择字段、任务输入或标题均不授予验证器资格；只有私有sealed taskID关系可以分流。

绑定同时封存source_model_id。执行的run.model_id必须与它完全一致，且同owner/W02/lead封存关系仍有效；同样的model字符串或另一未登记ID不能替代。私有状态schema2严格要求该字段与launcher摘要，旧缺失字段不能猜测迁移。重建后保留相同身份并重新核对，凭据/显示名称不参与此身份比较。

客户端由runtime另行实现：候选正式运行结束→确认最新snapshot中的/Max.java并下载核字节→正式创建空verifier task→POST固定`/api/v1/agent-evaluations/profiles/java-verifiers` body仅`task_id,source_run_id,artifact_id,source_sha256`→正式StartWork并登记返回verifierRunID→轮询正式结果及两套session/usage/namespace证据。结果必须验证固定程序/测试摘要、源摘要、编译/三例明细和保护探针，不仅检查passed字符串。任何未知提交均不重新POST。module和客户端/ready接线需分别冻结独审，工程替身通过不等于链路已通。
