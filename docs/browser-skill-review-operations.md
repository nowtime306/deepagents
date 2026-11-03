# BrowserSkill 运维脚本独立审查

2026-11-03，根协调者审查，作者为browser_ops。范围：专用启动/停止/检查、typed launcher、部署准入、安装及可选健康状态。未访问生产页面或个人浏览器资料。

## 审查结论

通过当前静态与作者定向运行证据，可交独立QA。最终10文件SHA见 `.local/browser-skill-ops-qa-e3e15ac979b64890bbc2894029f55ec4/ops-freeze-manifest.json`；不把本结论当界面验收或正式部署成功。

固定回环端口、空profile标记、CLI/扩展版本及协议核对；独立连接文件不改已有环境密钥。停止核对helper/父PID/可执行路径/命令/user-data-dir/daemon记录/端口归属，不按浏览器名称泛杀。可选能力未配置不阻止普通聊天；录制活跃时部署准入拒绝。

## 发现与修复

- 新profile会先出现Chromium组件worker。作者独立runner复现错误选取第一个worker导致初始化失败；现按官方manifest的service_worker路径并核对扩展name/version，bounded等待再写专用端口。保留首次失败记录。
- 根审查指出CDP对象尚未建立就失败时，旧shutdown只等Chrome退出，未主动关闭；作者故障fixture证实该路径，并发现signal退出时exitCode仍null。现优先正常关闭，失败仅终止自己创建的child handle，退出同时核对signalCode，daemon使用本home和PID记录。未知归属仍拒绝操作。

最终作者完整runner通过真实官方连接、重复启动、伪造PID拒绝、正常退出释放端口、外来端口冲突拒绝、CDP建立前失败后自有进程退出。`qa-result.json`/`qa-transcript.log`保留，未做业务页面动作。独立QA仍须验证最终SHA和必要运行，不借作者结果冒称全系统已验收。
