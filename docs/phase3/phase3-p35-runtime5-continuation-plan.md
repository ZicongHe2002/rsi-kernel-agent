# P3.5 Runtime 5 继续执行计划

> 历史计划。后续 Runtime 7 已完成原第二轮与诊断；当前状态见[最终验收](phase3-final-acceptance-report.md)。本文中的持续迭代授权是当时的记录，后续最终验收请求明确要求交付后停止自动优化。

2026-11-13，Asia/Shanghai。用户已要求完成原恢复流程，第二轮成功后继续自主迭代；没有可用 TPU v6e 时持续申请。最新[继续指令](../../knowledge-site/manifests/phase3-2026-11-12/sources/p35-continuation-request-2026-11-13.txt)更新了早先请求中的有界申请限制。

本计划是 Runtime 4 消失后的必要兼容工作，尚不代表新的硬件准入或实验成功。继续使用原 Store、Journal、Controller、Evaluator、Oracle、计时规则和 Context 隔离。

## 已确认的起点

- 20:39 对 Runtime 4 原 correctness operation 的查询返回桥接 `Exit`，未取得最终回执。
- 20:40:45 原控制面查询成功，`assignments=[]`；不能从本地缺回执推断远端从未执行。
- 20:40:46 已启动唯一原 `keep_requesting_v6e.py` 申请循环，沿用原 notebook 身份与 `reconcile_allocation.py`，失败后间隔 30 秒重试。每次网络调用本身也需要时间。
- 原 Runtime 4 attention 负例保留 generate/compile 成功回执、correctness durable request；其余三项准入 Run 为 not_run。
- 原探索限额保持 16 executions / 28800 声明秒；此前已用 10 / 18000，余下 6 次用于四个恢复候选和两个诊断 Run。真实执行前再次读取原 Journal 核对。

## 必要改动与检查

1. 新增 `p35_archive_runtime4_prerequisite_loss.py`：在原 Journal 锁内绑定失联证据、四个 job、durable request 与已有回执。attention 以原 Decision / RunEvent 记录 unknown / incomplete，其余三项保持 not_run；不伪造 correctness 或 Evaluation，不调用会把缺失回执记作 not_requested 的取消评估路径。归档保留原预算、Task 和完整身份，支持中断后幂等恢复。
2. 新增 Runtime 5 准入入口及核验辅助：复用 Runtime 3 已通过的四个源 Candidate，要求新实测环境、新 remote root / nonce、新 Context / Run。`real_round_aux.py` 仅扩充明确的 generation 支持。四项实际通过并核验后才切换 active binding。
3. `p35_recovery_round.py`、prepare/run 和 author/package 入口增加显式 runtime5 参数，保留 runtime4 默认行为。新目标为 `real-round-2-runtime5`，round 为 `real-runtime5-recovery-round:2`；新 Snapshot 必须含 Runtime 3 未提交归档、Runtime 4 未知结果归档和新准入结果。
4. 增加只读恢复轮核验入口，复用 `verify_job`、原始 NPZ 比较及 Journal 绑定检查，避免把旧第二轮状态文件当成新轮验证结果。
5. 独立单 tile 诊断 schema 2 增加 Runtime 5 选择；固定源码、workload 与 Oracle 不变。必须先有实际完成的恢复轮，不能用旧未提交归档满足该条件。

每项先建立失败 fixture，再实施最小兼容改动，运行相关准入/恢复/诊断回归。归档拒绝已有最终结果、身份错配、预算变化和失联证据不符；验证崩溃边界可恢复。保留旧 Runtime 4 和诊断 v1/v2 测试。真实 Store 变更前独立审查相关修改。

## 在线执行顺序

1. 保持原申请循环；只有实际 allocated 结果才能建立新 observation。若出现已有加速器分配，先核实归属，不重复申请或停止用户任务。
2. 用已有 `setup_runtime_environment.py --generation runtime5` 准备固定隔离环境，先 poll 原 setup，不重复提交；执行 `probe_isolated.py --generation runtime5` 获取同步探针和环境。
3. 核验并归档 Runtime 4 已提交而结果未知的旧准入；在新环境推进四项准入，逐 stage 按原 operation lookup / submit / collect 协议恢复。
4. 完成准入原始工件核验，切换 binding，重新读取完整新 Snapshot，实际消费 Analyzer packet 和工具结果后发布多 Plan。已准备作者脚本不能替代会话对新历史的读取与判断。
5. 执行四个恢复候选及两个独立诊断 Run，保存所有真实结果、失败、非晋升 Decision 和原始样本。新环境父计时来自新 Context。
6. 核对并回写 Memory、Best、Frontier、Current Parent、Latest Attempt、Last Correct 与预算，交付第一轮依据—第二轮修改—真实结果—后续判断的完整记录。

原六次执行完成之后，用户已授权继续迭代，但必须先形成可审计的追加预算记录；不得覆盖原不可变限额、清零 spent 或把重复请求伪装成新预算。后续每轮仍由真实反馈驱动，并遵循相同硬件证据与恢复规则。
