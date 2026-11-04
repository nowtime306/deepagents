# F01/F03工作流评测接线与验收

日期：2026-11-04；分支onwer_agent。实现作者rdc_history，非作者代码审查及独立工程测试evaluation_ops，root负责合入和正式隔离样本。

## 开发合同

F01用真实模型节点实现大小写两个版本，初始化两根各测试后发布；CAS选择current=1，再维护模型revision2/temperature0.4。正式执行前重新读取current并执行对应v1，另显式执行v2。四根总四调用/40输入/20输出Token；初始化与主运行的已用配置、TEXT资源修订和原生整数修订分开核对，不改历史发布定义。

F03固定子版本json_get(name)，子流程及父流程分别成功测试后发布，正式输入空对象触发子节点失败。正式502必须与精确持久根FAILED及原生父/子失败前缀相互印证，否则保持未知且不重发。三根、零provider，不虚构独立child run；原业务FAILED不改写，完整证据证明预期失败控制时才单独标控制验收通过。

初始化、正式执行及清理均复用同环境、owner、namespace、case、原意图和同份事实字节校验；保存失败阻断后续效果。当前指针漂移、额外owner效果、未知用量、缺节点前缀、跨根引用或类型错误均阻断通过/回收。只合入三个已核base的小patch及新模块，不用旧共享副本覆盖KB/Java既有链路。

## 独立审查与工程测试

原私有冻结清单摘要`e641057a94603220b8f97d437ee7aaa6214f12baf51b8aa0cecf9762caeda948`。非作者审查未发现阻止交测试的明确缺陷，审查记录摘要`8c9abb7d1cc01522e270f419506ab83de2bcb1a167b0a6e676b2f525e4f464dd`。

另一角色在等层私有副本复跑客户端31通过、真实编译器/profile10通过，零失败/跳过；清单14文件、三处active base及候选字节前后一致。独立收据摘要`bc1841c0874363da369710f2852e685762240c45d9cba1998744e6302eebdbd8`，位于`.local/team-workspace/agent-evaluation/ops-development/f01-f03-independent-qa.json`。受控DB/计量替身不冒充真实PG、正式目标HTTP、Agent任务、页面或供应商。Windows asyncio自用通信记录保留，不宣称OS零网络。

## 后续验证

合入时只迁移测试导入/路径至active模块，不降低业务断言；固定初始化器新字节摘要并保留原pin换行规则。active增量及既有客户端/profile回归通过后，再冻结新候选，在全新隔离库分别运行F01/F03，独立复核真实版本/维护配置、502持久失败、全部用量/审计和精确回收后才增加案例数。F05/F07仍在单独开发，本篇不是整批A20或生产上线验收。

active合入后，root使用隔离环境再次运行完整客户端488通过、目标侧profile195通过，零失败/跳过；结果位于`services/evaluation/.local/versioned-active-e5fe3cc3773c45d38c537b7c75991c46`。两组含既有457/185及新增31/10，不累计相加。初始化器按原CRLF规则摘要为`79a190b15b667dd31be87389f17e89ee58f4da3c9e39fe0f10bd18c8d9d00dc1`；suite只新增两项保守各2沙箱预约，没有改变既有案例上限。尚待新候选实际样本。
