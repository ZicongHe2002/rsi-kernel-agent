# P3.5 最终验证记录（进行中，ABBA 已完成，Runtime 4 准入待恢复）

<!-- p35-upload-status -->
**2026-11-13 19:07（Asia/Shanghai），上传前核验**：本地准入驱动已于 18:53:28 退出（exit −1），19:05:41 的进程观察确认没有原驱动或诊断包装驱动。Runtime 4 attention 负例的 generate/compile 成功回执已归档，correctness 有 durable 请求而没有本地最终回执；其他三项为 not_run。最后保存的 running 观察来自 18:53:03，原远端 worker 未停止，本次核验没有查询远端最终状态。恢复需先查询同一 operation，不能重复创建 Run 或盲目重做。

当前 Store 835 records / high-watermark 860；Controller revision 146，已用 10 / 16 executions、18000 / 28800 声明秒；已保存 Snapshot version 11 仍为 818 / 843。Runtime 4 恢复第二轮尚未创建，active binding 仍为历史 runtime3；必须四项新准入通过后才可切换。ABBA 与旧未提交轮次的归档保持有效，但不构成第二轮硬件验收。

最新完整根回归 **321 项：318 通过、3 跳过、0 失败/错误，423.235 秒，exit 0**。Runtime 4 相关辅助回归 26 项、诊断 v1/v2 完整回归 47 项通过；独立诊断复核另跑 39 项通过（147.742 秒），均为本地测试。[上传前事实](../../knowledge-site/manifests/phase3-2026-11-12/p35-upload-progress-facts.json)、[根测试日志](../../knowledge-site/manifests/phase3-2026-11-12/p35-root-tests-runtime4-20261113.txt)、[退出码](../../knowledge-site/manifests/phase3-2026-11-12/p35-root-tests-runtime4-20261113-result.json)、[诊断独立审查](../../.artifacts/phase3-2026-11-12/p35-diagnostic-v2-independent-review-20261113.md)。P3.5 仍未完成，稳定端到端收益未确认。
<!-- /p35-upload-status -->

<!-- p35-runtime4-progress -->
**2026-11-13 18:40（Asia/Shanghai）更新**：ABBA 结果已完整保存，结论见[专项报告](phase3-p35-abba-results.md)。第二轮 Analyzer 已读取 Snapshot version 10（776 records / high-watermark 801），完成四个新 Plan、四个独立 Candidate 源码及打包，但 Runtime 3 在任何第二轮远端提交前消失。该轮没有 durable Colab request/receipt，不能计作硬件完成。

四项原作业已通过严格身份、原锁及零提交检查归档；首项保留已预留成本并由原 Evaluator 记 cancelled / incomplete，其他三项 Run 为 not_run、无伪造 EvaluationBundle 或阶段回执。原 Task 继续开放。归档 Snapshot version 11 / 818 records / high-watermark 843；探索预算仍已用 10 / 16 executions、18000 / 28800 声明秒。[归档原件](../../.artifacts/phase3-2026-11-12/real-round-2/runtime-loss-archive.json)、[只读复核](../../.artifacts/phase3-2026-11-12/p35-runtime3-loss-readonly-verification.json)。

18:28:57 原分配流程确认新 Runtime `tpu-v6e1-s-euw4a0-1xqzeyr91qwmg` 后正常退出。隔离环境 ready，Python 3.11.17、JAX/jaxlib 0.8.1、libtpu 0.0.30、TensorFlow 2.20.0、xprof 2.22.1；单 TPU v6 lite／单进程，实测同步探针 140.0。四项新准入正在执行：attention 负例、cache 负例、A 修复基线、B delayed-store-wait。尚未宣布 Gate 通过，未重复执行旧 ABBA。恢复轮将用独立 Runtime 4 Context、新 Run、新 Snapshot 与新父计时；旧 ABBA 仅作为历史证据。[环境](../../knowledge-site/manifests/phase3-2026-11-12/environment-isolated-runtime4.json)、[探针](../../knowledge-site/manifests/phase3-2026-11-12/isolated-probe-runtime4.json)、[准入进度](../../.artifacts/phase3-2026-11-12/runtime4-progress.jsonl)。

本次必要改动仅在辅助脚本：原轮次恢复回归 11/11、Runtime 4 相关回归 26/26；归档 10/10 及观察变更专项 4/4，恢复门禁 5/5。独立审查另跑恢复/归档 15 项、观察 3 项、Runtime 4 准入/核验 13 项，均 exit 0；这些是本地 fixture，不代替硬件验收。[独立审查](../../.artifacts/phase3-2026-11-12/p35-runtime4-recovery-independent-review-20261113.md)。

P3.5 仍未最终验收：恢复优化轮真实执行、single-tile 硬件诊断与最终 Best/Frontier/Memory 核验尚未完成。此前带时间段落仅为历史状态。
<!-- /p35-runtime4-progress -->

<!-- p35-abba-complete-progress -->
**2026-11-13 17:28（Asia/Shanghai）更新**：ABBA 四个 Run 全部完成并通过原始 NPZ、回执和工件核验，确认结论写回原 Store 及两条策略 Memory。program 两对 median 降低 6.49584% / 5.92180%，通过冻结判据；host 为 inconclusive_drift，联合判定 inconclusive。完整数据和限制见 [ABBA 专项结果](phase3-p35-abba-results.md)。第二轮正在冻结包含此结论的新 Snapshot；单 tile 硬件诊断尚未执行，P3.5 尚未最终验收。

最新完整根回归：321 项、318 通过、3 跳过、0 失败/错误，567.523 秒，exit 0。确认恢复完整专项 18/18 通过，695.708 秒；诊断派发完整专项 11/11 通过，80.039 秒。以下较早时间的状态保留为历史，不代表 ABBA 仍在等待。
<!-- /p35-abba-complete-progress -->

<!-- p35-admission-complete-progress -->
**P3.5 更新｜2026-11-13 16:21:07（Asia/Shanghai，进行中）**：新 TPU v6e 的五项重新准入已完成并独立复核，**5 verified、0 pending、0 failed**，核验 198 个去重工件、517,816,053 bytes；原始 NPZ attention/cache 已重算。两个故意错误的 Candidate 均被 Oracle 拒绝；A 基线、delayed-store-wait 与独立 B control 均通过三组正确性及五阶段执行。三项准入 small host median 分别为 0.232400、0.217205、0.228915 ms；这些单次准入值不构成稳定收益结论。

确认轮已通过原 Analyzer：2 Plan / 4 变体，review 无结构性降级、winner_selected=false；输入 Snapshot version 7 / 579 records / high-watermark 604，B 路线完整历史含依赖共 315 条。已冻结 C–T–T–C 顺序与 2%/5% 判据。最近保存的 Controller revision 86 状态为：p35-control-before：blocked/compile；p35-treatment-first：queued/generate；p35-treatment-second：queued/generate；p35-control-after：queued/generate。原探索预算已用 6 / 16 executions、10800 / 28800 声明秒。桥接 unknown 按原 operation 查询恢复，不盲目重跑。ABBA、第二轮与独立单 tile 硬件诊断尚未完成。

本次根级测试 321 项（318 通过、3 跳过），625.063 秒，退出码 0；完整 P3.5 辅助专项 68 项通过，103.970 秒。新增诊断派发专项 7 项通过，48.541 秒，退出码 0；均为本地测试，不代替硬件验收。

[五项准入核验](../../.artifacts/phase3-2026-11-12/runtime3-prerequisite-verification.json)、[确认轮](../../.artifacts/phase3-2026-11-12/confirmation-f686274a57bb7ebb/selection.json)、[确认轮状态](../../.artifacts/phase3-2026-11-12/confirmation-f686274a57bb7ebb/controller-status.json)。

以下 15:15:54 的审计保留为历史状态，不代表准入仍在等待。
<!-- /p35-admission-complete-progress -->

审计时间：**2026-11-13 15:15:54 Asia/Shanghai（07:15:54 UTC）**。本文件记录正在执行的工作，不能作为 P3.5 已完成或优化收益已确认的验收结论。后续硬件结果须追加实际完成时间与原始证据。

## Runtime 与真实探针

[分配日志](../../.artifacts/phase3-2026-11-12/allocation-attempts.jsonl)在 `2026-11-13T07:08:13.020320+00:00` 返回 `allocated`，Runtime 为 `tpu-v6e1-s-euw4a1-39sjw4nm2h4b9`。本次重新取得的 Runtime 与原 Runtime 2 分离，没有把旧计时改标为新环境结果。

[隔离环境准备结果](../../knowledge-site/manifests/phase3-2026-11-12/isolated-environment-setup-runtime3.json)为 `ready`。[新环境观测](../../knowledge-site/manifests/phase3-2026-11-12/environment-isolated-runtime3.json)与[准入环境副本](../../.artifacts/phase3-2026-11-12/runtime3-admitted-environment.json)一致：Python 3.11.17，JAX/jaxlib 0.8.1，libtpu 0.0.30，TensorFlow 2.20.0，xprof 2.22.1，单 TPU v6 lite、单进程。[独立探针](../../knowledge-site/manifests/phase3-2026-11-12/isolated-probe-runtime3.json)记录 exit code 0、`synchronized=true`、结果 `140.0` 和 parser 可导入。这是设备连通性与环境证据，不是 MLA 正确性或性能证据。探针保留 transparent hugepages 未启用的原警告，未据此修改系统配置。

## 五项重新准入的实时边界

五个独立新 Run 已写入原 `experiments.sqlite3`，目前由原 backend / Evaluator 顺序执行。审计时点的状态如下；该表不随正在运行的进程自动更新。

| 准入项 | 当前观察 | 结果边界 |
| --- | --- | --- |
| attention 错误负例 | 新 Run 存在；最新 RunEvent 为 running / active，seq 482；generate 日志于 15:14:34 显示 completed | 尚无最终 incorrect 结论；不得称 Gate 通过 |
| cache 错误负例 | 新 Run 存在；尚无 RunEvent | 尚未取得新硬件正确性拒绝结果 |
| A 修复基线 | 新 Run 存在；尚无 RunEvent | 尚未取得新五阶段结果 |
| B delayed-store-wait | 新 Run 存在；尚无 RunEvent | 尚未取得新五阶段结果 |
| 原 B 基线独立 control | 新 Run 存在；尚无 RunEvent | 与 delayed-store-wait 分别重新测量；尚未取得新五阶段结果 |

五项 job 见 [attention](../../.artifacts/phase3-2026-11-12/oracle-negative-attention-runtime3-job.json)、[cache](../../.artifacts/phase3-2026-11-12/oracle-negative-cache-runtime3-job.json)、[A 基线](../../.artifacts/phase3-2026-11-12/baseline-repair-runtime3-job.json)、[B 候选](../../.artifacts/phase3-2026-11-12/baseline-b-runtime3-job.json)、[B control](../../.artifacts/phase3-2026-11-12/confirmation-control-runtime3-job.json)。运行日志见 [runtime3-progress.jsonl](../../.artifacts/phase3-2026-11-12/runtime3-progress.jsonl)。审计时五项 `*-runtime3-result.json` 均不存在；准备成功、generate 完成或进程仍运行都不等于五项 Gate 通过。

## 原 Store、Snapshot 与预算

使用 SQLite `mode=ro`、`PRAGMA query_only=ON` 和只读事务核对 `mla-decode-v1`：当前任务历史为 **462 records，high-watermark 487**。最新已保存的 catalog Snapshot 仍为 **version 5，443 records，high-watermark 468**，ID `snapshot:8228f411623d48cf87eae743f155c7f3`，digest `748bb0cdecb03602daf11430b87189e103ba6d006dcf506229567c96dfed3e00`。因此旧 Snapshot 只覆盖第一轮完成时点，不能用其条数说明当前 Store 没有新写入。

原探索 Controller 为 revision 78、5 个历史作业；已使用 **5 executions / 9000 声明秒 / 0 预留 tokens**，总限额 **16 executions / 28800 声明秒 / 100000 tokens**，剩余 11 executions / 19800 声明秒。重新准入沿用单独的 prerequisite reservation 记录，不应混作探索 Controller 消耗。后续 ABBA 必须由原 Controller 扣 4 次探索／7200 声明秒；single-tile 诊断若准入，必须另扣 2 次探索／3600 声明秒。换 Runtime 没有重置预算。声明秒不是实际 TPU 账单或 wall-clock，0 预留 tokens 也不是模型推理零成本。

## 本次本地验证

根目录全套测试于本次重新执行，日志结尾为：

```text
Ran 321 tests in 625.063s
OK (skipped=3)
```

即 **318 通过、3 跳过、0 失败／错误**，退出码 0，结束时间 `2026-11-13T06:48:53.3446607Z`。见[完整日志](../../.artifacts/phase3-2026-11-12/p35-root-tests-2026-11-13.txt)与[退出码记录](../../.artifacts/phase3-2026-11-12/p35-root-tests-2026-11-13-result.json)。命令为现有 Python 的 `-B -m unittest discover -s tests -v`。本地 CPU/Synthetic 结果不能代替真实硬件证据。

[ABBA 统计助手](../../.artifacts/phase3-2026-11-12/p35_confirmation_statistics.py)的单项最终运行 **19/19 通过**；初始 RED 为缺失助手的 7 项失败，数值溢出与精确阈值回归分别观察到 1 项、2 项失败后修复。阈值判断使用报告中 Run medians 的十进制表示构造精确有理数，避免二进制舍入把恰好 2% 或 5% 的边界错误分类；没有引入 epsilon 或放宽政策。[单 tile 诊断准备助手](../../.artifacts/phase3-2026-11-12/p35_single_tile_diagnostic.py) **8/8 通过**，初始 5 项缺失助手失败后实现。包含完整辅助链路的最终专项重跑仍在进行，本文件不预先宣告其结果。

## 尚待完成的硬件与闭环工作

1. 完成并复核五项新 Runtime 准入结果、原始回执、source/config/build、环境与 Oracle 绑定后，才能切换 active binding。
2. 按[预先固定政策](../../.artifacts/phase3-2026-11-12/p35-confirmation-policy.json)执行 C–T–T–C 四个独立 Run，保留每 Run 的 20 个 host 样本及同 Run profile program/prepare/attention 原始数组。各维度要求两对 median reduction 均至少 2%、所有 treatment medians 低于 control、两角色内部 spread 均不超过 5%；任一角色 drift 超限则 inconclusive。Run 是独立统计单位；两个 Run／角色不能扩张成 40 个独立实验，不输出显著性或置信区间结论，不覆盖原 Store promotion。
3. 单 tile 零长度诊断目前只有合法独立 Workload/Context 准备与静态分支检查：small 长度 `[0]`、capacity/T=1024；boundary 长度 `[0,1,129]`、capacity/T=256，保留 opaque NaN cache。需要真实 Analyzer 解释、原 Intake 与 Controller 各创建 baseline/single-tile 的独立 Run；尚未执行。接口不暴露 LSE，明确不测。见[准备说明](../../.artifacts/phase3-2026-11-12/p35-single-tile-diagnostic-notes.md)。
4. 读取完成后的最新 Memory/Snapshot、第一轮全部结果与 ABBA 结论，再产生不同于第一轮的第二轮 Plans、执行新候选并归档完整成功、失败、更慢与非晋升结果。
5. 最终核对 Branch Best / Task Best / Frontier、父选择、完整历史和证据后，才判断 P3.5 是否达到系统闭环验收；当前稳定性能收益仍未确认。

本次文档编辑保留总报告旧正文，编辑前原字节备份位于 `knowledge-site/manifests/phase3-2026-11-12/before/implementation-report-before-p35-2026-11-13-74509e62bec275b278ad98d85e9792e890ee25bdf2ec1fc3c09a4d24258e3548.md.original`。本次只读审计不改变 Store；正在执行的真实准入流程已经写入新 Context/Run 与相关操作记录，应与文档维护动作区分。
