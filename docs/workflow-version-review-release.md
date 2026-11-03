# 平台版本归档脚本独立审查

2026-11-03。审查范围 `scripts/platform_release.py` 及其直接部署/健康记录格式，未修改业务或运维脚本。审查基于源码SHA256 `1f1666f633097334fc4c6bb27fef9929bd42593f416c2d6edbd022ab71f38d21`；产品角色未参与该脚本实现。独立复现仅使用 `.local/team-workspace/workflow-version/review` 下临时fixture、虚构文字及mock Git，不接生产数据/服务、不变真实仓库Git。证据：同目录 `release-review-reproductions.json`。

## RS-01 / P2：发布验收没有核对完整源码集合

`complete()` 只验证QA manifest中存在的键，因此一个合法文件即可满足已验收源码检查；最终工作区也只校验record中列出的文件，不拒额外业务源码。隔离例：归档含API和Web源码，QA只列API一个文件，工作区额外存在 `apps/web/src/EXTRA.tsx`，仍被标记deployed。真实构建会吸收额外文件，此时发布提交不是实际部署源码的完整描述。

应明确并验证完整业务清单（或正式定义且完整的独立changed-set；不能把任意非空字典当完整验收），在完成前比较工作区业务文件集合与归档一致，同时排除明确的依赖/构建/数据输出，禁止跟随越界链接。反例应分别覆盖缺项QA、未归档新增源码、已删文件与内容漂移。

## RS-02 / P2：后端补丁部署可以给新全平台版本盖章

`complete()` 只检查 deployment status/phase/time，不检查 scope/web。现有 `deploy-platform.ps1 -BackendOnly` 明确写 `scope=backend-only,web=unchanged`，该模式不重建/重启Web。隔离fixture使用此记录和通用ready健康状态仍获得deployed。对于含新UI的WVR版本，旧Web能健康并不等于本次前端已部署。

最小修复为本脚本全平台complete要求 `scope=full,web=rebuilt`；若后续支持后端独立版本，应另设计比较Web源码未变/原BuildID继承的显式规则。本轮不要无依据复用旧Web验收。测试覆盖backend-only/unchanged拒绝，full/rebuilt才可通过。

## RS-03 / P2：源码包私密路径校验存在Windows大小写绕过

`source_hashes()` 对 `.env`、`.pem` 等采用大小写敏感匹配，Windows上 `.ENV` / `.PEM`同样是常见环境/私钥文件但会通过。隔离fixture `services/api/.ENV` 和 `infra/server.PEM`均被接受。此外ZIP同名成员不拒，后一个摘要覆盖前一个，清单不再唯一描述全部成员。

应以规范化casefold路径检查敏感扩展/名称，并拒精确或Windows大小写等价重复成员；`.env.example`等明确样例仍可保留，使用虚构内容测试。prepare在写出archive后才检查禁用文件，检测失败不能留下可误分享的完整未完成发布包；仅清理本操作创建且确认归属的路径，不覆盖已有发布记录。不要通过扫描真实密钥证明规则。

## 其它范围与门槛

- release_directory版本正则与resolve归属、防覆盖已有目录、ZIP绝对/上跳/反斜线/符号链接拒绝、标签提交固定方向正确。
- 记录remote只应保留非凭据定位；本次现origin是无凭据HTTPS，未读任何凭据。
- “QA passed/browser passed”的JSON是验收证据索引而不是替代真实测试；当前浏览器origin授权被拒，不能生成假的browser passed通过门槛。
- 未运行服务、部署、购买额度或改写Git；完整作者测试与正式独立QA由对应角色执行。

## 结论

RS-01～03需作者修复并反例自验后复审，当前脚本不放行给QA作为正式发布工具。上述复现证明当前门槛会误放行，并不表示生产已发生泄露或错误部署。


## 修复复审与冻结（2026-11-03）

RS-01已修：QA与archive完整业务hash map严格相等；业务目录包含editable SDK libs/deepagents；当前Git cached+others非忽略业务集合必须与archive相等，逐文件hash一致且拒外链。缺项QA/新业务文件/修改源均被反例拒绝。

RS-02已修：complete严格要求scope=full且web=rebuilt，backend-only/unchanged拒绝；保留实际部署时间门槛。本次放行是工具逻辑，不代表真实Web已验收。

RS-03已修：ZIP成员casefold去重、敏感名/扩展大小写检查、非规范NUL/backslash及links拒绝；prepare先在自己创建的临时staging校验，全部通过才移动成正式source.zip，禁用包失败自动清该staging。无修改旧发布记录或其它用户文件。

作者提供16项纯单元通过（6.88s）；审查者阅读测试确认是真Git临时仓+虚构凭据/源码/部署记录，覆盖已提反例，并独立运行同一批 **16 passed / 7.88s**。两批不能累计成32个用例。审查者首次默认tmp_path运行因本机隔离临时目录权限在setup阶段16错误，未执行业务测试；指定本工作区专用basetemp后通过，未降低断言或改业务源码。

最终源码SHA冻结见 `.local/team-workspace/workflow-version/review/release-reviewed-source.json`，含脚本及纯单元测试各自摘要。RS-01～03关闭，当前静态/定向复审通过，可交独立QA验证脚本；不是生产部署许可。真实页面因明确origin访问门槛仍待授权，禁止填browser=passed绕过。
