# 工作流版本与平台Release独立测试记录

2026-11-03，依据workflow-version-requirements.md的WVR-01～08及workflow-version-tests.md的VT01～30。状态：**版本API与定向兼容通过，真实界面已获授权待验收，未批准正式发布**。

## 审查与隔离

后端经独立复审后冻结5文件，见`.local/team-workspace/workflow-version/review/backend-reviewed-source.json`；发布工具经产品独立增量复审冻结2文件，见同目录`release-reviewed-source.json`。正式执行前后均校验其SHA；前一版作者169项结果不算本次最终源QA。

API测试使用随机`owner_agent_workflow_version_qa_<随机>_test`与独立数据目录、假账号/模型、纯transform流程。内存TestClient的Origin头沿原项目配置，不发生对生产3100的网络访问。child首次连接数据库被本机旧工具权限10013阻止，发生在建库前；已交root用相同隔离runner代执行。这是执行环境问题，不计产品bug。

## 已执行：Release完整性

独立QA执行21项通过，11.58秒：已有16项加独立新增5项。仅操作pytest私有临时伪Git仓库，无真实仓库Git改动、服务调用或部署。

- 已提交源归档、排除runtime环境/密钥、敏感文件大小写、越界与ZIP成员重名拒绝、失败prepare无不安全包。
- 完整验收源集合、额外/变化/缺失业务源拒绝、backend-only/旧部署拒绝、只有fake完整部署证据才可通过算法门槛。
- 独立新增：虚拟标签漂移不能给旧归档盖章；缺失或改写成员后即使重算ZIP总摘要，仍按逐文件清单拒绝；工作区源被删不能标部署；重复prepare保持原记录与ZIP字节不变。

证据：`.local/team-workspace/workflow-version/qa/release-qa-61adbe057f4c.{log,json}`。发布脚本SHA为`3e984f0db13b2f5dfa54929f9205f98535b775b63fdb3878427db50eab32507e`，执行后reviewed_source_unchanged=true。永久独立用例`services/api/tests/unit_tests/test_platform_release_independent.py`。

fixture中browser/status/deployment JSON都是虚构输入，用来验证工具拒绝/接受规则。**本次真实浏览器未运行、平台未部署**，不能拿这些输入给正式release标为deployed。VT25～29的归档算法边界已测；VT30实际部署/健康追溯仍待最终发布时另外验证。

## 已执行：版本API及定向兼容

根协调者使用独立QA的同一冻结runner代执行：独立7项加作者13项，**20项通过，17.77秒**。覆盖固定历史/明确version、恢复原子备份与重测、INSERT后人工故障事务回滚、inactive历史复制独立性、旧layout/current不猜测、真正只读角色拒绝、旧raw不可变与canonical画布/restore响应。数据库`owner_agent_workflow_version_qa_1d2ad8b950_test`已清理，JSON记录reviewed_source_unchanged=true。

随后9个模块定向兼容：原工作流、外部固定Key、Ask工作流授权、Work预算、输入Schema、Studio、节点调试、原节点与嵌套编排，**35项通过，2项跳过，32.79秒**。数据库`owner_agent_workflow_version_qa_713df99520_test`已清理，reviewed_source_unchanged=true；未把skip算pass，也未据此宣称所有Ask SSE路径均回归。两个批次的日志与JSON在`.local/team-workspace/workflow-version/qa/`，以数据库名为文件名。

## 保存权限仍拒绝：真实界面待执行

用户随后明确回复“已允许访问独立测试站”，3131旧基线真实体验已完成。但根角色在两次用户授权/改设置回复后，重试IAB2仍被工具明确拒绝：saved user permission/managed policy denies 3131。不再换浏览器、别名、CDP或其它角色绕过；旧基线截图不能代表新WVR。生产3100同样不在本次浏览器授权内。

QA已准备原地升级隔离站，保留假数据/端口，根完成安全归属核对后执行080迁移并构建；当前构建状态另见私有station-upgrade.json与日志，未作为页面通过证据。待工具实际允许后先作者自验、再独立QA，页面操作仅使用Cua。VT21～23及FE01～04、390px及无脚本错误仍待实测，构建或8项未执行UI mock测试源码不能替代此结论。

WVR各需求的完整层级映射见workflow-version-tests.md。当前没有把30条设计全数标通过；最终API结果、界面授权及验收、实际部署都应分别收口。
