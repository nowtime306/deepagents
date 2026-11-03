# RDC Runtime / Index / Trace 独立QA报告

日期：2026-11-03。冻结提交：`833a4ac212b2103d05e3a73e352d27af74d52e07`。

## 1. 当前结论

本轮已按根任务明确开始信号执行，不是作者自测摘要。结果为 **332 passed、5 failed、21 skipped**。当前不能宣布本范围全部验收通过，更不能宣布完整产品/页面/真实供应商/真实Coding专项通过。

5项失败分别涉及新测试前提、旧Schema替身与精确接口校验、与RDC相反的旧revision断言、旧invoke替身签名/旧URL断言以及SSE替身子进程依赖。已将原始证据和具体定位交根任务；没有修改生产代码、测试断言或本次锁定源码来获得绿色结果。Schema等价性是否需要产品规范化仍由根任务核查，不把该项未经确认地归咎于测试。

独立编写并经根任务审阅的4个新增验收场景：3通过、1因前提配置失败。其余329通过、4失败、21跳过来自采用的已有/实现作者测试源码，由本审查者在新隔离环境独立执行。案例来源与执行主体不同：不能把新增4例说成全部覆盖，也不直接把作者先前报告的数量计入本报告。

## 2. 授权、隔离与证据

开始条件由根任务明确确认：工作区对应上述冻结SHA且clean，083/backup独立审查M01已关闭（根任务另有10探针证据），其他阻断审查均关闭；允许本轮默认无Sandbox的7组QA。本人不执行Git；执行前重新计算测试和app/*.py、migrations/*.sql源码摘要，执行后再次核对后端源码，无变化。根任务开始gate、manifest与每组结果均保留。

执行器：[run_runtime_index_qa.py](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_runtime_index_qa.py)。

冻结清单：[execution-manifest.json](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/execution-manifest.json)。源码清点63文件/243个顶层测试函数，parametrize展开后实际结果总计358项；不以源码函数数当通过数。

本轮总证据目录：[run_f96f77276b9e](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/summary.json)。

- 每组新建独立 `owner_agent_rdc_indqa_<组>_<随机>_test`，独立TEST_DATA_DIR、vault及向量目录。
- 仅用本机postgres管理库创建/删除这些测试库，不连接生产业务库；7个自建数据库均在finally校验名称后DROP，result.json记录owned_database_removed=true。
- 子进程禁用所有自动消费者默认开关，具体用例显式调用受控worker/engine；RUN_LOCAL_SANDBOX未开启。
- 模型、向量、重排、MCP/浏览器运行器等用替身；协议测试仅本机受控接收器。子进程另有非loopback连接及3100/3131浏览器表面禁止守卫。
- 没有访问被拒页面、真实供应商或真实Sandbox，没有构建前端/覆盖默认.next，没有复制新UI到3100。PLATFORM_WEB_DIST_DIR显式隔离；根任务另行的真实Coding专项和独立目录构建不计入本报告。

## 3. 分组结果

- **acceptance**：3通过、1失败、0跳过，8.61秒。新增独立场景；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/acceptance/pytest.log)。
- **runtime**：119通过、2失败、3跳过，47.62秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/runtime/pytest.log)。
- **workflows**：87通过、1失败、2跳过，98.96秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/workflows/pytest.log)。
- **index**：65通过、0失败、2跳过，86.18秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/index/pytest.log)。
- **tools**：36通过、1失败、14跳过，21.00秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/tools/pytest.log)。
- **trace_recovery**：13通过、0失败、0跳过，14.01秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/trace_recovery/pytest.log)。
- **legacy**：9通过、0失败、0跳过，14.68秒；[日志](D:/project/my_agent/deepagents/services/api/.local/team-workspace/rdc/independent-qa/run_f96f77276b9e/legacy/pytest.log)。

上述耗时为pytest分组耗时，不含全部数据库管理时间。预期反例的错误日志（预算耗尽、主动撤权等）只有在对应断言通过时才计通过，不能仅因日志出现ERROR就新增失败，也不能因最终exit code为0而忽略skip。

## 4. 五项失败与处理建议

### Q-01：持久Ask启动路由用例在入队前提失败

用例：`test_rdc_acceptance.py::test_ask_worker_queue_reselects_route_at_actual_consumer_start`，第157行；POST返回422 ROUTE_NO_CANDIDATE，未执行AskWorker。

定位：新增测试请求漏掉native_search=false，新会话默认开启原生联网；受控custom/.invalid候选不声明原生联网能力，因此产品正确拒绝。此前test_model_routing的同类helper明确关闭该开关。

根任务已确认这是测试前提问题，并要求本轮结束后再最小修测试、记录新freeze/重跑acceptance，当前失败证据保留。该次结果不能用于宣称实际AskWorker队列路由刷新通过；WorkEngine独立启动边界已通过。

### Q-02：MCP本地/远端Schema的空properties表达不一致

用例：`test_agent_bindings.py::test_agent_skill_and_mcp_are_independent_and_mcp_revocation_applies`，第107行；MCP_TOOL_DENIED。

定位：数据库声明为 `{"type":"object","properties":{}}`，远端替身返回 `{"type":"object"}`；当前实现按结构精确相等拒绝。两种空properties表达的语义等价性、Schema描述/title等注释性变化是否应触发拒绝，需要根任务按规范明确。

根任务已说明不会放宽真实接口/读写安全边界，并拟核查最小语义canonicalization。不能只删掉反例/绕过invoke，也不能在未核查前将其记为产品通过。若修改产品需独立复审、新freeze并重跑runtime/tools及受影响入口。

### Q-03：旧台账用例要求普通revision变化必然阻断

用例：`test_usage.py::test_model_revision_guard_prevents_new_provider_calls`，第99行；预期APIError未发生。

该断言直接与RDC-04相反；两次begin还复用同call_id，不能直接删raises后就宣称多次实际调用已覆盖。

建议新测试在实际resource scope内用不同call_id验证普通维护后沿用旧快照/台账修订，再停用模型验证下一调用拒绝、无新增预留，保留正反边界。由根任务在本轮结束后最小补测试，不修改产品以恢复旧通用revision阻断。

### Q-04：工作流MCP节点使用旧invoke替身契约

用例：`test_workflow_nodes.py::test_agent_and_read_only_mcp_nodes_respect_current_bindings`，第179行；节点返回502。

定位：测试把app.state.mcp.invoke替换为只接受expected_pin的旧函数，当前真实tool闭包传resolved_server/expected_tool/before_call，会触发TypeError。该用例后半还断言修改MCP URL后旧工作流必须409，亦属于旧pins语义。

建议改为session层替身，保留真实invoke/运行冻结/授权检查；新运行应采用当前配置且不重发工作流，解绑仍403。只给替身增加任意kwargs会掩盖其没有执行新安全边界的问题。

### Q-05：SSE受控服务的系统Python依赖不完整

用例：`test_tool_expansion.py::test_real_legacy_sse_discovery_and_call`，第189行；替身服务未启动即失败。

定位：用例通过sys._base_executable启动系统Python；该进程导入Roaming user-site中的opentelemetry，再因缺importlib_metadata退出。尚未进入产品的MCP discovery/call，不能据此判定产品SSE协议失败。

建议隔离替身解释器/依赖（例如使用测试.venv解释器并避免非受控user-site）；保留本轮失败日志，不安装/修改生产环境来隐藏失败。需后续独立重跑该协议用例。

## 5. 按RDC编号的覆盖与限制

### RDC-01：替身API正反例已执行，仍非真实供应商效果验收

原发布智能体模型维护后创建自动任务、普通对话及后台执行；模型停用、非聊天用途和工具能力未知等反例由resource_decoupling/agent_versions/automations等通过。实际AskWorker在定时任务常规路径执行已有通过证据；专门“入队后路由变化”的新增Ask用例因Q-01未覆盖到consumer，另列RDC-04限制。

### RDC-02：新增独立发布团队场景通过

两个成员使用不同模型，父子均发布v1后，只修改研究成员模型地址/key/温度；真实原生委派经受控ToolModel完成。HTTP trace显示父业务发布版与两个成员版本未变、三个实际模型可区分、角色lead/researcher/reviewer与真实修订1/2/1一致，成员提示词仍为v1。其他已有团队撤权/下线/并发预算反例通过。真实Work团队Sandbox路径跳过，不外推该部分。

### RDC-03：版本/多入口大部分通过，MCP节点入口待Q-04修复复验

旧v1设为当前、显式v2不漂移、子流程固定版本、草稿测试门槛、CAS、不可变发布历史、Ask/Work工具及外部API等用例通过；MCP节点串联agent的旧替身/旧断言失败，故不能宣布该入口全绿。真实固定代码Sandbox场景仍跳过。

### RDC-04：模型/检索冻结、Work启动和恢复阶段通过，Ask专项及真实重启链路仍有限制

同一运行两次实际受控模型调用之间维护、新运行读取新配置通过；检索阈值/重排绑定/记忆绑定按阶段固定及撤权通过。新增WorkEngine.execute在真实路由刷新、模型首次factory解析后，由受控sandboxes.create返回None并CapacityUnavailable停止；断言最新路由修订2/模型修订2，trace为resolved且calls空，没有伪造模型/Sandbox执行。

cold cache确认的新phase HTTP记录与同host8443/9443差异通过；明确是清理进程内缓存的模拟，不是完整操作系统进程退出/新进程消费者恢复。该完整真实Coding链路由根任务另行专项，未纳入本报告。Ask专门排队刷新待Q-01重跑。

### RDC-05：不由本人作独立验收结论

本人是配置历史作者。排除了本人history自测文件；部分root trace/legacy用例涉及共享审计依赖，只记录其对运行追溯/迁移的作用，不关闭配置历史产品验收。

### RDC-06：多模型/业务版本、当前差异及分阶段追溯有独立证据

新增团队HTTP trace、已有普通运行/子流程共享scope、未知旧数据不伪造、实际调用与仅解析项、恢复新阶段等通过。对比端口/目标摘要、owner隔离、字段脱敏、业务版本字段均有通过断言。capability-only关联用例通过，但其历史本体仍归RDC-05另一位验收者。

页面“从运行跳历史再返回”的交互没有执行，不能从API字段通过推断该体验已通过。

### RDC-07：仅root新增启动边界作补充验证

本人参与过早期路由实现，不宣称整个路由产品独立验收。root新增at_start的WorkEngine/inline入口及route trace已有通过证据；实际AskWorker新反例因Q-01前提失败待重跑。未运行本人修改过的test_model_routing首例作为独立通过依据。

### RDC-08：替身向量兼容场景通过；真实输出等价性未联调

文本/记忆/图片兼容、同维不同身份、旧向量未知、管理员兼容迁移证据、同身份query维度变化明确关键词降级等通过。新增作者提供的video-snapshot案例由本人独立执行通过：process/index_frame真实调用链配受控解码/向量替身，同job三帧保留原目标/key/参数/修订1；新job采用修订2，新job停用后无第五次向量调用，台账关联各自快照。

这不是实际视频解码镜像或真实供应商输出验证，真实Sandbox视频专项仍未开。

### RDC-09：已有实验/门禁替身用例通过

历史评测保留、配置适用性、严格门禁与关闭门禁、人工引用/拒答标注、来源变化中断、未知用量/结果不算完整成功等通过。不能把合成/替身回归结果当真实供应商质量门禁证据。

### RDC-10：热/冷配置、接口变化/授权、Skill轨迹绑定有证据，仍留Q-02/Q-05和Sandbox空白

MCP快照/热目标与key、冷目标确认、远端发现后实际调用前撤权、本地具体Schema/readonly变化、Skill旧冻结内容及轨迹不可替换等采用的新用例通过；浏览器运行器均为替身。Q-02的Schema等价性与Q-05的SSE替身环境尚待处理。真实stdio/ACP容器生命周期/背压共14项未开启，不计通过。没有实际浏览器操作。

### RDC-11、RDC-12：本人作者范围，不作独立通过结论

未运行仓库/通知作者自测作为独立验收。legacy中旧通知目标保持的反例只作为迁移回归，并不关闭通知产品独立验收；真实仓库写入与外部通知均没有执行。

### RDC-13：边界回归多数通过，不能以恢复旧revision阻断来修Q-03

账户/资源/成员授权撤销、停用、业务版本下线、任务取消、执行租约、预算、显式写审批/UNKNOWN等通过。旧revision guard用例Q-03需要改成新语义的成对断言；MCP接口等价性需保留真实结构/权限变化拒绝，不得为了过测移除安全检查。

### RDC-14：后端中文错误/定位与权限有覆盖，实际页面明确未验收

节点/成员、资源不可用、向量不兼容、待确认恢复与实际配置/当前差异等API字段和错误分支已执行。3100/3131页面访问此前被拒，本轮不绕过；窄屏、键盘焦点、只读按钮、未保存编辑状态、导航返回与新UI体验均仍未验收。没有使用默认.next构建。

## 6. 21项skip与未覆盖gate

- runtime 3项：实际Work团队、自动Work、真实worker审批/成果。
- workflows 2项：真实Coding Dify/固定工作流。
- index 2项：真实Work语义记忆回归。
- tools 14项：stdio/ACP真实Coding镜像2项、背压4项、生命周期8项。

这些skip均由源码显式RUN_LOCAL_SANDBOX门槛触发，不是环境“自动通过”。根任务并行真实Coding专项的失败/后续排查不属于上述21项通过替代；须独立附报告。真实供应商和被拒UI继续保持未执行。

## 7. 结束与复验要求

1. 保留本轮完整失败证据，不修改已有日志或将failed改成passed。
2. 根任务决定最小测试补丁及Schema语义修复；任何产品更改先独立审查并新freeze，再复验受影响组。只修测试也要记录新测试冻结摘要与重跑范围，不覆盖833a4ac本轮结论。
3. acceptance重新执行应真正到达AskWorker；runtime/workflows旧用例需按新语义保留正反断言；SSE用例需先隔离依赖再验证实际协议。
4. 最终RDC覆盖说明继续保留本人作者范围排除、实际页面、外部供应商及真实Sandbox限制。所有必要门槛满足后由根任务按原流程决定交付/部署，本报告不授权上线。
