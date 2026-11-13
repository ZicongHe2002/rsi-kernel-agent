# P3.5 Runtime 4 恢复实施计划

2026-11-13。继续原 P3.5 请求；Runtime 3 在第二轮远端提交前消失。ABBA 已完成，原第二轮 Analyzer、四个 Candidate 与旧 Context 永久保留。

目标：在原 Store、Journal、预算和执行协议上恢复真实第二轮，随后完成独立 single-tile 诊断。本文是实施计划，不是硬件成功证据。

1. `.artifacts/phase3-2026-11-12/p35_archive_unsubmitted_runtime_loss.py`：持有原 Journal 锁，核验原四个第二轮 job、operation intent、零 durable Colab request/receipt 和 Runtime 消失证据；通过原 `integrate_result(cancelled=True)` 归档已开始的本地作业，其余三个未开始作业保留 not_run。记录完整归档意图和幂等 marker，不取消整个 Task，不退回已经预留的 1 execution / 1800 声明秒。验证拒绝路径及各持久化边界崩溃恢复。
2. `runtime_readmission.py`、`setup_runtime_environment.py`、`probe_isolated.py` 与新 `admit_runtime4.py` / `advance_runtime4.py`：仅扩展辅助层 Runtime 代号，保留旧参数和观察文件。实测新环境、同步探针，再用原 `remeasure`、backend、Evaluator 重新准入两个负例、A 修复基线与 B delayed-store-wait。创建新 Context/Run 前、阶段提交前，在原锁内核验旧作业归档和无其他活动作业；同源码身份不代替新环境测量。
3. 恢复优化轮使用新目录 `real-round-2-runtime4` 和新 round ID `real-runtime4-recovery-round:2`。沿用原 prepare/session/publish/intake/Controller，读取包含旧第二轮未执行记录与新准入的新 Memory。ABBA 仍在其原 Context 核验，只作为新轮历史知识，不作为 Runtime 4 性能分母。新轮父计时必须来自 Runtime 4 准入。
4. 独立诊断辅助仅增加明确的 Runtime 4 选择路径，要求恢复优化轮真实完成，并保留原 Runtime 3 行为。冻结的两组 zero-length workload、源码、Oracle 与输出契约不变。
5. 对必要修改执行本地 fixture 与适当回归，独立审查后才运行真实 Store 变更。硬件执行后回收并核验原始 NPZ、样本、回执、Profile 绑定、Memory、Best/Frontier 和预算，按实际证据更新报告及周记录。

预算：当前已用 10 executions / 18000 声明秒，总限额 16 / 28800；剩余 6 / 10800，可容纳四个新优化 Run 和两个诊断 Run，无额外 repair 余量。重新准入沿用独立 prerequisite reservation 账目。声明秒不是实际计费。

重点拒绝：存在远端提交凭据、身份或预算变化、旧作业仍活动、观察 Runtime 错代、使用旧环境计时参与新排名。申请循环沿用已运行的唯一原桥接流程；只有实际 allocated 结果才能开始环境准备。
