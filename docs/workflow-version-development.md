# 工作流版本管理与软件版本留档实施

> 2026-11-03：资源引用检查已按 [RDC实施计划](resource-reference-decoupling-development.md) 调整。旧版设为当前及各执行入口校验当前可用性与真实兼容，不再逐项比较外部资源通用修订。本文保留WVR阶段的原始实施记录，验收状态分别记录。

依据workflow-version-requirements.md WVR-01～08，用户授权提交并推送origin/onwer_agent。已将此前完整平台作为2818e6f647093a1e8ad1695139f9fdaf4ace8ab7 / zhixing-v0.1.0基线推送；本功能独立提交，不改上游SDK版本。分支/提交/标签见docs/releases/history.md。

## 实施与验证顺序

1. 产品核官方高star项目的草稿/历史/当前发布语义，交互Demo与验收契约冻结。
2. 后端080独立指针/release_revision和历史layout；完整预览、严格set-current、CAS原子备份恢复、独立复制，维护旧显式版本行为。作者在新随机库执行真实API边界与原工作流回归。
3. 前端独立workflow-versions组件，画布完整只读预览与保留本地编辑；取消/迟到请求不污染另一个版本。列表和顶部显示唯一当前发布号。恢复先持久化dirty编辑，后端事务另存可访问备份，再刷新草稿、版本和历史；复制保持原页，保存dirty后才允许打开副本。运行当前发布版读取明确编号，沿原固定版本入口；草稿测试独立。
4. 软件版本采用Git提交/标签、源码包与SHA、报告和部署状态记录，不把未提交工作区归档冒充某个HEAD。源码与用户数据库备份分开；没有密钥/数据进入Git。
5. 作者自验→独立审查→修复复审→QA独立API/实际浏览器→备份当前数据并部署080→健康/数据核对→提交/推送记录。没有通过本阶段不提前上线新入口。

## 改动边界

现有workflow-api只增发布/状态的事务衔接与独立历史router，前端新增组件/CSS和workflows.tsx调用；不改工作流执行协议，不追加Workflow SSE，不重写旧run、外部Key、Agent/subworkflow pins。旧current初始NULL、旧无layout明确默认展示；失效资源只能恢复/复制待修，不能偷偷换资源执行。

BrowserSkill与后续平台优化另有产品需求及开发版本，不能混入本版本的已完成声明。现有已提交基线是共同起点；并行子角色不自行操作Git索引。
