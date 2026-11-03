# Work会话中断后reaper取消修复

2026-11-02。全量QA原始Work失败后，首个隔离双中断诊断通过32.39秒，未据此标成flaky。继续沿用原旧ready谓词，独立随机数据库/目录和本机自建守护进程连续8轮kill-all，在第8轮实际复现。

脱敏await证据：`.local/team-workspace/platform-expansion/development/tools/work-host-diagnostic/await-proof.json`，共44个持续快照。主协程停在work_server.py原83行gather(reaper)，reaper任务cancelling=1却处于reconcile的sleep，继续运行，证明等待的是reaper退出，而非run_fenced的job gather。原压力日志stress-result2.log和stress-run2保留。

根因：reconcile依赖取消被底层吞掉/转换普通异常后，except Exception继续执行30秒sleep并再次inventory；当前任务已有取消计数但未再收到CancelledError。主协程只cancel一次，故一直等后台退出。

最小修复只在Work reconcile中检查当前任务cancelling：inventory返回后、普通异常分支和recovery返回后均立刻传播CancelledError，避免新恢复工作、sleep或下一inventory。未改run_fenced、队列重放、服务超时或60秒验收门槛。

确定性测试test_work_reaper_cancellation.py覆盖inventory/recovery各自吞取消正常返回与转换普通异常四种情况，用asyncio.wait观测单次cancel后的结束，不用wait_for第二次cancel掩盖缺陷；检查inventory只一次、取消inventory后不得调用recovery。另实际连续8轮与原双中断守护进程回归，结果完成后记录。生产源码冻结后须独立复审，再交QA。

4个确定性自验通过4.31秒，Ruff通过。修复后原旧ready谓词+连续8轮kill-all实际自验通过202.16秒（另含首次单backend中断，共9次重启），随后双Work实例显式stop完成，测试finally精确删除本次随机数据库。证据`work-host-diagnostic/fixed-stress-result.log`与`fixed-stress-run`；仅在测试目录使用await诊断wrapper，未向产品引入诊断开关。另运行未经wrapper的原双中断用例和4个确定性回归作为最终原生确认。

最终未经wrapper的原Work supervisor双中断、双worker显式停止及4个确定性回归共5项通过（34.09秒），证据`work-host-diagnostic/final-native-result.log`；该批也使用新的随机数据库和专属目录，测试finally已回收自建服务与数据库。此为程序员自验，待独立审查通过后由QA复测，不声称全量589项已重跑。
