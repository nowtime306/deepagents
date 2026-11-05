# 3100 测试、3131 正式：环境换位运维方案

2026-11-05，运维交付。当前阶段：经独立代码审查与根Agent准入，端口换位及真实HTTP认证/隔离验证完成；浏览器业务页面验收仍由根Agent接续。本页不把环境准备记为业务案例通过。

## 目标和保持项

- 测试入口 `http://127.0.0.1:3100` → API8100 → 全新 `owner_agent_port_switch_d53e208fefbf44e9ba2ee4862f05e59c_test`，独立账号、data/key/namespace。
- 正式入口 `http://127.0.0.1:3131` → API8101 → 原 `owner_agent`，原账号、数据、加密密钥与配置保持。
- 两端同为127.0.0.1，Cookie不以端口隔离。测试必须使用审查后的实例级 `owner_agent_test_session`；正式保留 `owner_agent_session`。未经此修复与双登录/注销互不影响验证，不切换。
- 正式受限发布仍只运行普通Ask及管理；未验收的Work/Workflow/自动任务/评测执行继续关闭。新测试初始为仅管理的隔离实例；正式业务专项另用独立受控目标和原预算，不能借环境准备复用旧W05批额度。

## 已完成的冻结与准备

私有根目录 `.local/environment-port-switch-20261105/`，`latest.json`指向 `frozen-96d70434385840519012d602ccc8dfd6`。生产351项来源与清单SHA256 `8a649adc50177e38ecb7c77b19c89066012c622b9b0b72732e81cf62a1db0af8`一致，727项F05 Web资产完整复制；不使用Z9旧测试UI替代正式版本。新冷host保留原DB/key读取路径、冻结源码ROOT和精确正式origin，冷 `check` 已通过。

Web运行依赖通过受控node_modules junction绑定原 `apps/web/node_modules`，启动前核固定Node/Next/配置摘要，禁止在这段部署期间安装或更新依赖。正式routes-manifest的两个接口目标保持8101。测试Web派生同一已验UI，只改变测试副本routes-manifest的两个接口目标为8100；原正式文件不动，派生前后摘要单独记录。

新测试库85项迁移已齐全，namespace `fd4aed58-8e21-4dac-9c81-1afcc5009e81`，无业务任务/模型调用；账号在私有 `test/credentials.json`，连接在 `test/server-private.json`，身份在 `test/identity.json`。不得在聊天或Git中输出这些凭据。

## 脚本和审查入口

以上路径均相对仓库根目录，操作脚本在私有运维根目录：

1. `freeze.py`：只读核旧原件并复制到全新隔离目录；重复执行会生成新冻结，不覆盖旧原件。
2. `prepare-test.py`：仅新建空测试库、账号、key与身份；不会启动服务、提交模型或执行任务。已执行成功；不得为重启重复创建相同目标。
3. `stop-owned.py check`：只读核原生进程句柄、创建时间、命令、监听、运行及效果账本；不修改维护状态。
4. `stop-owned.py stop`：须根审查收据绑定该脚本确切SHA。先以既有维护锁710035排除在途写入，再将两旧库置于本轮自有维护状态、二次核活动，随后合作停API、Ask和有token-stop的正式Web。唯一无合作通道的旧测试Web只能按根批准例外，核持有句柄/创建时间/argv/端口/无在途效果后终止该句柄，不按历史PID或端口强杀。
5. `serve-test.py`：仅从已审测试源、私有连接启动8100，精确origin与独立Cookie，执行后台关闭。
6. `launch-new.py`：核根审查绑定、测试源码逐项摘要、冻结正式host和Web/依赖，再依次等待新正式Ask唯一真实进程心跳、API ready与两个Web成功；新Web都有私有token-stop。启动结果仍须 `verify-new.py` 独立核验。
7. `verify-new.py`：真实HTTP验证两端登录、同一Cookie jar同时保持各自账号、退出测试不影响正式、错误origin被拒，以及DB/key/模型用量保持；只读认证18180库存和容量。接口验证不冒充浏览器UI验收。

审查收据 `root-review-approved.json` 由根Agent在独审和独立QA后写入，必须包含stop/launch/test-host确切SHA及 `test_source_files` 全量清单。审查期不能边改脚本边执行；有修改重新出SHA并复审。

## 历史异常和封存边界

- 正式存在2026-11-02已结束的Ask UNKNOWN用量。finished_at明确非空、无活动根/租约/操作时原样保留；不回填Token/状态。PENDING、未结束UNKNOWN或任何活动效果仍拒绝切换。
- 旧测试库唯一通知fixture `91038f83-d221-421b-a475-35a9215ab694` 为PENDING、attempts0、无lease/finished；旧bootstrap明确AutomationScheduler关闭，源码摘要44ac7a1b36acd1557736344c6359abc8118b290627024b80be9abfbcf525e506绑定。根允许仅此从未开始的记录原样保留，任何字段变化、新PENDING、SENDING或在途操作均阻断。旧消费者全部退出后，旧测试库维持本轮自有维护token封存，不能误启动旧bootstrap或重派旧通知。
- 正式维护四字段在全部旧入口退出后CAS原样恢复；旧测试库的原维护字段及新token留私有收据供人工审查。故障时不覆写他人维护或停止标记。

## 回退与中断恢复

尚未停止旧服务时，失败只留诊断与新空测试库，原入口保持。停止过程中任一身份、活动或nonce核对失败立即停步，保留维护gate、全部日志和句柄证据，根Agent核实后再恢复；不得自动清空队列或将UNKNOWN改成功。

新启动失败时，先按 `launch-receipt.json`、实际CIM/创建时间与各自token合作停止新入口；两个新Web均使用 `<角色>-web-private.json` 的本轮token/pid停止文件，不能直接按历史PID终止。API及Ask沿明确data/services停止标记安全退出。确认全部新入口和原生进程退出后，才恢复旧角色。

回退应用使用冻结351项源码，而非可能已被开发改动的共享工作区。将冻结冷host的正式origin从精确 `http://127.0.0.1:3131` 恢复为旧 `http://localhost:3100`，以新文件保存、出差异及摘要并经根审后运行；DATA、DB、key与351项校验仍保持。先启动原生Ask并核真实心跳，再API8101，再冻结F05 Web3100、目标8101，沿原账号验证。旧测试库仍保持封存，不因恢复正式服务重放旧PENDING通知；测试3131可暂时保持关闭并明确报告。

此回退只恢复应用角色和入口，不恢复旧备份，不回滚数据库，不撤销用户新写。若要重新开放旧测试库，必须核其维护token、全部历史通知/任务的原状态以及明确的消费者策略后由根审另行处理。

## 资源和最终交接

Coding维持5Gi/4CPU，Browser维持3Gi/2CPU；创建前使用真实宿主/Docker容量准入并串行测试，不调低资源或停止其他项目容器。18180由根Agent单独恢复，本轮脚本仅认证只读库存，不重复启动、不创建沙箱。

换位后必须同时记录真实端口/PID/创建时间、DB与namespace归属、Cookie与origin验证、浏览器页面实际结果、旧环境封存和正式数据守恒。只在这些结果闭合后，将本页状态更新为已部署。

## 本次实际切换与缺陷修复

已按根批准单次执行 `stop-owned → launch-new`。旧四入口真实退出、正式维护原四字段精确恢复，旧测试库保持本轮维护封存。初次新API/Ask通过真实心跳与ready检查，但正式3131首页404，因此原启动保留为 `STARTUP_NOT_CONFIRMED`，没有宣称完成或重跑一次性launch。

定位到原727资产清单漏列 `required-server-files.json.files` 所要求的两个顶层运行文件。Next读取缺失app-path-routes manifest时默认空映射，导致根路由404。经根和独审核原BUILD_ID及映射，另行审查 `recover-web.py` SHA256 `85f5083fd2d95afac8cc2202c3beb90bef055d9150dd5d01067ff98c02324fd2` 后，只补以下两个原发布文件；原727文件和src/app、API、DB均未改：

- `react-loadable-manifest.json`：`550dd073de790daed1ec1276057da8508e783737c4caf279c20b836f4d62dd0e`。
- `app-path-routes-manifest.json`：`05b05e01fe26d95be40e9b0872ee40a6893b3598c1e258277e75674121950afa`。

先以原生创建时间/CIM/监听/本轮token合作停旧404 Web并恢复正式3131首页200，再启动测试3100首页200。原404日志、首次启动收据和原私有身份原件保留，不用后续通过覆盖失败。

实际HTTP核验 `verification.json` SHA256 `50d05b87e944e2e94c869f2f44465ed1f02403cfbf00cad1b42035abeebe1568`：两首页/注册/登录/me成功；同Cookie jar同时保持正式和测试各自账号；注销测试后正式仍登录，错误Origin两端403；API8100/8101均ready200；正式namespace/key保持、用量仍61；18180认证库存读取成功且无创建。浏览器UI实际结果不在此脚本范围。

首次容量读取Docker总量超时，原预算0失败观察保留；另做一次只读重采样成功，收据 `resource-resample.json` SHA256 `636ad5e0ad18ee7e9256e4a0f09815d4b24bb0ba82d4abdaae5cca8fd5f077d6`：预算6926651229字节，已扣1GiB余量，仍不支持Coding+Browser同时准入，后续每次创建仍须重新采样。

当次真实监听仅作交接记录：正式Web3131=41680、正式API8101=2968、正式Ask=14912；测试Web3100=41980、测试API8100=2832；18180=39412。以后停止必须重核真实身份和创建时间，不能按这些历史PID结束进程。当前Web本轮私有停止身份在 `<角色>-web-private.json`，对应恢复记录为 `<角色>-web-recovery.json`。

旧库封存又经只读验证，`retired-test-sealed.json`：原通知仍PENDING/attempts0/无lease或finished，旧库新鲜Worker为0，维护active=true且token为本次自有值。新测试账号路径仍为私有 `test/credentials.json`；普通正式账号不变。此轮实际模型/业务任务/沙箱新增均0。
