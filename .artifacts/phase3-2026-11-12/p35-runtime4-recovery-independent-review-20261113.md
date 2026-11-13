# Runtime 4 恢复辅助独立审查

2026-11-13 18:37 Asia/Shanghai。Reviewer：`/root/p35_recovery_review`。范围为未提交 Runtime 丢失归档、恢复优化轮的新增路径、Runtime 4 admission/advance 辅助及其原接口。未修改代码，未打开或写入真实 Store，未调用 Colab/TPU。

在已审范围内未发现未关闭的 Critical / Important 问题。此结论不是硬件准入或 P3.5 验收结果。

- 归档固定原四个 job、intake、Context、Runtime 和 revision；原 Journal 锁内先确认各阶段没有 durable backend request/receipt。仅已本地启动项调用原 `integrate_result(cancelled=True)`，保留 unknown correctness/timing、空 receipt、无 latency；其余三个 Run 保持 not_run/result=None。原 operation 留存，已扣预算不退款，Task 不全局取消。
- 新观测检查允许其他 Runtime 已在 assignments 内，但必须严格通过已知脱敏字段类型检查，且旧 Runtime 3 不得出现。观测真实性仍来自调用者实际执行的发现命令和进程检查，函数本身不联网证实。
- Runtime 4 的四项来源先经过原 `require_gates`；A/B 附加固定源码 SHA 检查。四个新 job 全部通过 detached `_ReadmissionPreview` 和已存在文件一致性检查后才开始写入。新环境必须与 allocated intent、observation pin、同步 140.0 probe 完全一致；旧 Context 和 source/config/build 身份保留。
- 原 advance 的新增 guard 在 Journal 锁内，先于 backend 创建和预约/提交。原真实阶段逻辑与既有 operation 查询语义继续复用。
- 恢复轮使用独立目录和 round ID。ABBA 校验明确使用原 Runtime 3 Context，并要求新 Snapshot 包含其结论及归档 intent/result；原 `prepare_round` 在新 Context 选择父 benchmark，原 `_parent_latency` 再检查父 Run Context 一致，不将旧 ABBA 计时作为新性能分母。
- Runtime 4 gate 通过后，调用者必须显式切换 active binding 或写入新目标目录的 immutable binding，再执行 prepare。恢复 wrapper 的 OS driver lock 与诊断 wrapper 共用；其他旧 driver 不使用该附加锁，运行仍须遵守现有单驱动约定。

实际独立测试：

| 范围 | 结果 | 日志 |
| --- | --- | --- |
| 恢复轮 5 项 + 归档原 10 项 | 15/15，65.973 秒，exit 0 | `p35-recovery-independent-tests-20261113.txt` |
| 观测 guard 修改后的三项 | 3/3，4.631 秒，exit 0 | `p35-recovery-observation-independent-tests-20261113.txt` |
| Runtime 4 admission 与 verifier | 13/13，2.280 秒，exit 0 | `p35-runtime4-independent-tests-20261113.txt` |

测试仅涉及临时 CPU fixture Store 与必要 mock。未重跑根级完整套件、原 NPZ 或原始 XPlane 解码，也未核查最终真实 Runtime 4 回执。

关键审查末版 SHA256：

```text
p35_archive_unsubmitted_runtime_loss.py 9814a535cb3913ee4eee19d15d66aa1935d25162e323cb729e55db9e4bf0ce6a
p35_recovery_round.py 43e9aa76af0a8d3dd1e373ff58d7b8ef9ac7fd05f1cd5c555898490dc5d23738
prepare_real_round.py 81f2141f552302c146b6329757bf3535fe86e035f8d83a1b9405f8e65266e087
run_real_round.py 33f66c8c7dc6afbc58f3de588bcc85f7a693e9dd6b606783cbee8e12cf53ef5e
admit_runtime4.py e5876ff707be0bb0e4a537820dc1f5d99d0fe0d5679bc8b3edbe639ecfd3061b
advance_runtime4.py 298e435f998dceabd9a237dc0e5e4bcbe9f935e3c9eda93b558a8c3fe1c7ca6f
advance_runtime2.py d77b5ce966ec013ed05f4bfa1c03656e6cc761e90c07dafe92d052a31e667c3a
runtime_readmission.py f171fc1698411684e9c9801987f85f2a78fdbdb6a6cdc47db6fb19f774549471
```
