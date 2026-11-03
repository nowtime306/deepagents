# BUG-EXP006内部回调隔离独立审查

2026-11-02。作者expansion_tools，独立审查expansion_review_tools。审查冻结的config、ask_automations、ask_resources、work_resources、ask_worker、runtime、work_engine、work_server及test_internal_api_routing.py和修复文档；必要追踪四类客户端所有构造方。未修改业务源码、未启停生产或QA站点、未恢复用户删除的工作流/修改绑定。

**结论：独立代码审查通过，可交QA配置非默认站点后复测。** 未发现本次修复新增阻塞问题。默认生产8100兼容不等于QA8139无需配置；后者必须由测试在API/Ask/Work同一profile中设置PLATFORM_INTERNAL_API_URL，再启动自己的服务。

## 调用链与安全核验

- Settings新增服务器内部配置，默认http://127.0.0.1:8100。构造及from_env均校验，env为PLATFORM_INTERNAL_API_URL；仅HTTP、IP字面loopback、显式1024～65535端口、空或根路径。拒绝userinfo、非本机域名/内网、区域标识、query/fragment及控制/空白歧义；错误固定文案，不回显传入URL或凭证。
- AskWorker把同一Settings URL传AskKnowledge；Runtime把self.settings URL传automation_tools；WorkServer把同一Settings URL传WorkKnowledge和WorkEngine，后者传WorkWorkflows。客户端工厂再次校验，不依赖Settings单次校验；默认构造签名仍兼容。
- 四客户端均trust_env=false、follow_redirects=false。Ask/Work知识、自动任务targets/drafts、Work流程创建与状态轮询均沿配置地址构造；lease仍在POST JSON中，未把管理Cookie/模型Key换作内部认证，也未从模型工具参数接收服务器URL。自动任务和工作流tool暴露的schema保持业务参数，基础设施地址不向模型提供。
- 原租约有效期/当前状态/撤权/归属和预算检查未因路由修复删除或放宽。全app定向检查残余8100：只剩DEFAULT常量和managed_services的固定默认健康探测；后者不携run租约，属于既有默认部署健康管理，不冒称是本次敏感回调遗漏。
- WorkEngine原有WEB_ORIGIN仍通过Settings.from_env取得；所有实际服务应按文档使用同一站点环境，WEB_ORIGIN与本站API设置匹配。本轮未顺带重构既有origin传递。

## 独立实际执行

本审查者创建新随机`owner_agent_review_internal_<8hex>_test`数据库和独立`services/api/.local/test-review-internal-<8hex>`目录，PYTHONNOUSERSITE=1。运行以下6模块：internal_api_routing、ask_automations、ask_worker、ask_workflows、work_workflows、knowledge_usage。**40 passed in43.97s，无跳过**。finally精确删除本次测试数据库，未复用作者库或生产向量/密钥目录。

证据包含：非法地址拒绝；旧默认端口MockTransport兼容（不发真实8100）；非8100四条路径和流程轮询都使用指定地址，代理/重定向禁用；重定向不二次发送租约；两个真实本机受控接收站点分别接受本站随机租约、交叉租约拒绝，站点明确不是8100；旧Ask自动任务/独立worker、Ask/Work工作流及知识用量/预算/撤权回归通过。真实知识模型请求全部由本机协议替身处理，没有外部付费调用。

这些测试未访问当前生产8100接口，不写原用户任务/工作流/绑定，也未用旧生产配置启动任何服务。

## QA交接范围

QA应在自己的8139 profile统一设置URL后，重启其API/Ask/Work，回归实际页面中的知识检索、自动任务草稿、Work知识和绑定工作流执行/轮询，确认本站执行记录与用量、租约错误仍拒绝。测试可创建自己的受控资源，不能恢复用户已确认删除的工作流或改回用户绑定来迎合旧测试。

本报告通过范围为本次内部路由修复及上述隔离回归；真实页面、非默认profile服务启动/实际调用和最终全系统验收仍由QA负责。不得据此宣称生产已重启或真实外部模型已联调。
