# P3.5 Final Acceptance & Evidence Audit

**RSI-Kernel-Agent 真实 TPU v6e 两轮 Multi-Plan 闭环最终验收｜2026-11-16（Asia/Shanghai）**

> **本次验收结果：P3.5 System Workflow = PASS_WITH_LIMITATIONS；Stable End-to-End Improvement = NOT_CONFIRMED。** 原始证据审计与修复后的完整软件回归均已完成。首次辅助回归失败、最小夹具修复及红→绿日志保留在本报告的 Software Tests 中。

## Executive Summary

P3.5 在 **MLA Decode contiguous BF16 的声明范围**通过两轮真实硬件、历史反馈驱动的系统闭环验收，结论为 **PASS_WITH_LIMITATIONS**。**稳定端到端性能提升为 NOT_CONFIRMED**：第二轮的两个更快候选各只有一个独立 Run，已有 ABBA 的 host 结论为 `inconclusive_drift`。

本次重新读取原 Store、Snapshot 和 Journal，重算原始工件、NPZ、host 样本与 XPlane；没有把历史报告当作当前验收结果。第一轮完成 **4 Plan / 5 Candidate**，第二轮在 Runtime 7 分 A2、B2 两批完成 **4 Plan / 4 Candidate**，另有 **4 个 ABBA Run、2 个独立 single-tile 诊断 Run**。第二轮使用了第一轮与 ABBA 历史，B2 还读取 A2 和诊断反馈，结果经原 Evaluator、`Store.consider` 回写 Memory。

最新实测状态仍为 **Snapshot 26 / 1505 records / high-watermark 1530、Journal revision 247**，探索预算 **17/18 executions、30600/32400 声明秒**，剩余 **1 / 1800**。当前没有非终态 Controller job；Runtime 4 的历史 correctness 结果仍为 unknown，保留 incomplete 归档，不算成功。

本次是离线验收，新增 TPU 执行、设备申请、Candidate、Run、grant、原 Store 写入和依赖安装均为 **0**。验收后不自动开始下一轮优化。原始任务见[用户验收要求](../../.artifacts/p35-final-acceptance-2026-11-16/request.md)，执行步骤见[审计计划](../superpowers/plans/2026-11-16-p35-final-acceptance.md)。

## Scope

| 维度 | 本次验收范围 |
| --- | --- |
| Kernel / 接口 / dtype | MLA Decode / contiguous / BF16 |
| 真实环境 | 已保存 Runtime 2、3、7 的 TPU v6 lite（v6e），单设备、单进程；Python 3.11.17，JAX/jaxlib 0.8.1，libtpu 0.0.30，TensorFlow 2.20.0，xprof 2.22.1 |
| 原正确性矩阵 | `decode-small`、`decode-boundary`、`decode-default`；attention `atol=rtol=0.01`，完整 updated cache 按 BF16 原始位比较 |
| 正式性能 | 仅 `decode-small`；`mla-contiguous-host-sync-v1`，5 warmup、20 samples、无异常值剔除、完整输出同步 |
| 单 tile 诊断 | 独立 workload/context，baseline 与原 single-tile 各一个 Run；small lengths `[0]`，boundary `[0,1,129]` |
| 本地审计环境 | 原 Windows 环境与既有 Python 3.13.14；不等同于远端 TPU 软件版本 |
| 未覆盖 | Forward、paged、其他 dtype、更广 mask/window/soft-cap、其他形状的正式性能、LSE、完整 DMA/VMEM/MXU 硬件计数能力 |

正式 small 的 Batch/Heads/capacity 为 1/16/1024，latent/RoPE/block 为 512/64/1024；boundary 为 3/16/256、128/64/128；default 为 8/16/16384、512/64/1024。诊断 boundary 将 block 固定为 256，使 capacity==block，直接覆盖单 tile 分支；这不是修改原已冻结 workload。

原计时是 **host synchronized public-call latency**：包含 prepare、attention、完整 cache 输出及主机派发/等待；不含预先输入准备、host→device 传输和编译。它不是纯设备 Kernel 时长。program/prepare/attention 是另一次 Profile operation 中的 trace-derived duration。

## Evidence Inventory

本次原始证据审计 **28/28 Run verified、0 failed_verification**，重新散列 **925 个去重工件、545,089,987 bytes**，重算 **76 个 correctness case**，直接解码 **22 份 XPlane**。完整 Strategy、Plan、Parent、Candidate、Run、source/config/build、实际加载路径、环境、Context、operation、回执、Evidence、Evaluation 与 promotion ID 见[逐 Run 原始审计 JSON](../../.artifacts/p35-final-acceptance-2026-11-16/raw-audit-results.json)及[审计说明](../../.artifacts/p35-final-acceptance-2026-11-16/raw-audit.md)。

权威 PallasKernels 原清单的 **390 个文件**重新散列后全部与原摘要相同，含原 Kernel、Reference、benchmark 和 parser。Analyzer 谱系另核对 398 个源码/build/bundle 工件；这与 925 个原始执行工件存在交集，不相加为去重总数。[源码与官方资料核验](../../.artifacts/p35-final-acceptance-2026-11-16/source-reference-audit.json)限定于保存清单，不声称外部仓库工作树干净。

| 证据组 | 本次原始复核数量 | 原始完成证据 / 独立输出 |
| --- | ---: | --- |
| 第一轮 `real-runtime2-round:1` | 5 Run | [完成归档](../../.artifacts/phase3-2026-11-12/real-round-1/completed.json) |
| Runtime 3 C–T–T–C | 4 Run | [ABBA 结果与索引](phase3-p35-abba-results.md) |
| 第二轮 Runtime 7 A2 | 2 Run | [完成归档](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/completed.json) |
| 第二轮 Runtime 7 B2 | 2 Run | [完成归档](../../.artifacts/phase3-2026-11-12/continuation-e4b6a0fcf34a6c37d083/completed.json) |
| Runtime 7 single-tile 诊断 | 2 Run | [完成归档](../../.artifacts/phase3-2026-11-12/single-tile-diagnostic-e18e6e9bf4a98216/completed.json) |
| Runtime 2/3/7 父版本与负例 | 13 Run | 每环境两个父/基线、两个负例；Runtime 3 另有独立 control。逐项 ID 与工件在原始审计 JSON |
| Memory / Snapshot / Journal | 全部 1505 记录、2925 关联、247 修订 | [Memory 审计](../../.artifacts/p35-final-acceptance-2026-11-16/memory-audit-report.md)、[完整结果](../../.artifacts/p35-final-acceptance-2026-11-16/memory-audit-result.json) |
| Analyzer 冻结输入与输出 | R1、原中断 R2、Runtime 7 A2/B2 | [溯源报告](../../.artifacts/p35-final-acceptance-2026-11-16/traceability-review.md)、[机器事实](../../.artifacts/p35-final-acceptance-2026-11-16/traceability-facts.json) |

成功 Run 的生命周期为 **generate → compile → correctness → benchmark → profile → evaluate**：前五项是 Backend 执行阶段，evaluate 是本地固定 Evaluator 与 Store 集成。六个负例均停在 correctness 失败后，没有 benchmark/profile 或有效晋升。不能为了凑“阶段数量”虚构负例的性能回执。

### Correctness 与 retained artifacts 的独立复核

保存的 candidate/Oracle NPZ 重新逐元素比较 attention，检查 shape/dtype/finite 和误差，并对完整 cache 原始位比较，保留 opaque NaN tail/padding 契约。本次使用保存的 Oracle 输出，**没有重新执行 CPU Oracle**。Runtime 2、3、7 的 attention/cache 两类负例共六个 Run 均重现拒绝，`eligible=false`、branch/task promotion=false。

此前未补齐的 cache finite metadata 已通过 BF16 指数/尾数位扫描独立重算，9 组去重 cache 数组均与回执一致；扫描器核对了全部 65,536 种 BF16 位模式。包含 opaque NaN 的 boundary cache 是预期契约，不能把其 finite=false 改写成 attention 正确性失败。

本次用已存在的 protobuf 7.36.2 纯 Python 实现和 TensorFlow 生成的 schema 独立解码 22 份原始 XPlane，按 operation-derived program 名称重建 module/custom-call 的 duration、offset、step、run 样本，逐项匹配保存 parser 的结果。没有安装 protobuf、导入 TensorFlow 或升级环境。此项提升了 retained trace 的可复核性，不增加 DMA、VMEM、MXU 或因果机制测量能力。

**设备执行后的输入 cache 未单独保存，仍为 `not independently verifiable from retained artifacts`。** 原 runner 确实检查过设备回读与原输入字节；保存的执行前输入和输出 cache 不能代替执行后的输入状态。该项沿用原 runner 回执，不冒充新增独立复证。

单 tile 两个诊断 Run 均正确，原分类 `correct_inconclusive`，独立 Context 不替代正式优化性能分母。它们直接覆盖的是原 single-tile 源码；**不构成 B1 fusion 新分支的独立零长度、多 tile 或 opaque-NaN 覆盖**。B1 fusion 在本轮正式矩阵中仅由 B=1 small 进入融合分支，其他两组走 B>1 父路径。

| 独立诊断角色 | host median ms | program median μs | 正确性 / 性能解释 |
| --- | ---: | ---: | --- |
| baseline | 0.219115 | 6.604375 | 固定两 case 通过；correct_inconclusive |
| 原 single-tile | 0.256860 | 6.620000 | 固定两 case 通过；correct_inconclusive |

这些是独立诊断 workload 的单 Run 数值，不与正式 workload 合并，也不作为新 ABBA 或稳定性能结论。

## Round 1 → Round 2 Traceability

审计实际读取原 manifest 完整字节、冻结 Snapshot、参与策略完整历史、Knowledge 版本、tool calls、三次 responses、PlanSet publication、Intake 和独立 source diff。每份参与历史均重新检查 owned records 与依赖闭包；不是通过后来编写的脚本推断 Analyzer 当时看过什么。

| 实际会话 | 冻结输入 Snapshot | 完整参与历史 | Plans / Candidates | 工具调用（source/history/profile/Knowledge/calculate） |
| --- | --- | --- | --- | --- |
| R1 / Runtime 2 | 4：198 / 223 | A 97、B 130 | 4 / 5 | 2 / 2 / 2 / 6 / 8 |
| 原 R2 / Runtime 3，后中断 | 10：776 / 801 | A 268、B 501 | 4 Plan；本批未执行 | 2 / 2 / 2 / 5 / 7 |
| R2 A2 / Runtime 7 | 19：1199 / 1224 | A 414 | 2 / 2 | 1 / 1 / 1 / 5 / 3 |
| R2 B2 / Runtime 7 | 25：1400 / 1425 | B 718（含 A2/诊断依赖） | 2 / 2 | 1 / 1 / 1 / 6 / 4 |

全部会话为 `conversation_session`，各有三次原响应、review 开启且 `analysis.review=[]`、`winner_selected=false`。这证明固定会话中的多方案分析、原 publication/intake 与执行反馈已连通；不证明无人值守模型自主调用或优化方法优于随机搜索。

四份 manifest 各冻结六个 Knowledge 方法及来源版本；R1/B2 实际调用读取六项，原 R2/A2 读取五项。来源包括 `hist-decode-dma2`、`hist-decode-prepare-padding`、`hist-fwd-state-lifetime`、`hist-profile-accounting` 与两项官方计时/版本方法。完整 revision 见溯源报告；原 manifest 的 `knowledge_corpus_snapshot_consistent=false`、`combined_atomic_snapshot=false` 明确保留，逐方法固定不等于整个 Knowledge/Memory 跨服务原子快照。

原中断 R2 的四份候选已经提出，本次恢复会话根据新准入/历史重新审视并发布两批既定方向。其源码身份与原四项方向保持一致，不能把恢复同一方向说成额外一轮全新候选发明；也不能把 Runtime 3 未提交的批次计为完成硬件实验。

下表源码行号以各自保存 Candidate 的 `pallas_kernels/kernels/mla_decode.py` 为准；精确 diff、父源码摘要、Plan ID、Knowledge 修订及原 response 引用见溯源审计。

| 第二轮 Candidate | 原有事实与 Run 来源 | 原分析的 hypothesis / 源码改动 | 真实反馈与 Memory |
| --- | --- | --- | --- |
| `length-row` | R1 tile512 `wf-r:ff302e1edbba1534f33b983fdecaebdd`、tile256 `wf-r:5a817d8cbe14b528e5447301c17b537a` 的 program 回退；single-tile `wf-r:44028b3ac9bd4026c7961396e3a67ab8` 无明确收益；ABBA host 漂移 | 保留 tile/cache/recurrence，仅把 lengths `[B,H,T]` 压为 `[B,1,T]`，在 attention 广播；行 2914、2924、2933、3011。逻辑 payload 缩减属于 hypothesis，不是实测 DMA 节省 | host 单次降低 3.36164%，program 接近父版；正确性通过，A Branch/Task Best 晋升。未证明稳定收益或机制 |
| `length-smem` | 同一 A 历史和当前 Runtime 7 A 父版本；独立于 length-row 出发 | 去除 prepare 中 lengths 输出，从 `[B]` SMEM 读长度后广播；行 2902、2915、2930、3008、3025。SMEM API 已有用例不保证该组合更快 | program 略短而 host 增加 1.93914%；正确但更慢，未晋升，保留在 Frontier。不能由小中间张量推出端到端加速 |
| `query-in-attention` | 原 R2 依据 R1 delayed wait `wf-r:9693f8c2c46382039b4386db7c6ddbe1`、compact `wf-r:fc2da20b2a1679868f21869279e90949` 及 ABBA；B2 又读 A2、诊断和新父计时 | 保留 delayed wait 和完整 cache，把 query padding 从 prepare 移到 attention；行 2902、2914、2929、3007、3026。减少中间表示可能增加 attention 内工作 | host 增加 6.00515%，program 增至 6.831250 μs；正确但更慢、未晋升。该实际结果不支持本次性能假设 |
| `b1-fused-prepare` | 同一 B 历史；B2 明确读取 A2/诊断结果并保留 ABBA 的漂移边界 | 静态 `batch==1` 将必要 prepare 融入 attention，B>1 保留父路径；行 2917、2936、3022。减少独立调用是待测假设，完整 padding/cache 仍计时 | host 单次降低 4.26699%，program 为 5.890000 μs；独立 prepare event 缺席，不能写“测得 0”。B Branch Best 晋升，Task Best 仍为 length-row；稳定性和更广分支覆盖未确认 |

这条证据链支持“读取过去反馈 → 形成有依据的多个假设 → 独立候选 → 真实验证 → 原 Memory 更新”。Knowledge 命中或数学 payload 计算不等于收益的因果证据；当前没有随机搜索、无 Memory、无 Knowledge 或单 Plan 的受控对照。

## Candidate Results

以下每项正式候选均通过原三组正确性，`timing_validity=valid`、`confirmed=true`。confirmed 表示满足固定证据契约，**不是稳定加速标签**。正 reduction 表示相对该行当前 Context 的精确父 Evaluation 延迟降低。

### 第一轮：Runtime 2

A 父 host/program 为 **0.193195 ms / 6.624375 μs**；B 独立基线为 **0.215365 ms / 6.615000 μs**。两基线源码相同，但使用各自独立 Run，不能互换分母。

| Candidate / 改动 | 父路线 | host ms | program μs | host reduction | speedup | 原分类 / 完成时 Memory | 性能解释 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| tile512 / small block 1024→512 | A | 0.225485 | 8.090000 | −16.71368% | 0.856798 | correct_slower / 不晋升 | slower |
| tile256 / small block→256 | A | 0.202570 | 11.127500 | −4.85261% | 0.953720 | correct_slower / 不晋升 | slower |
| single-tile / 单 tile recurrence | A | 0.193550 | 6.580625 | −0.18375% | 0.998166 | correct_slower / 不晋升 | slower |
| delayed-store-wait / 推迟同 tile 的 wait | B | 0.183795 | 6.190000 | +14.65883% | 1.171767 | correct_faster / B Branch、Task 晋升 | single_run_faster；后续 host 复测 inconclusive |
| compact-new-row / 缩小 prepared new | B | 0.221875 | 6.588750 | −3.02278% | 0.970659 | correct_slower / 不晋升 | slower |

### 第二轮：Runtime 7

A 父为本环境重测的修复基线，host/program **0.211950 ms / 6.638125 μs**；B 父为本环境重测的 delayed-store-wait，**0.223225 ms / 6.196250 μs**。

| Candidate | 父路线 | host ms | program μs | host reduction | speedup | 原分类 / 完成时 Memory | 性能解释 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| length-row | A | 0.204825 | 6.626250 | +3.36164% | 1.034786 | correct_faster / A Branch、Task 晋升 | single_run_faster |
| length-smem | A | 0.216060 | 6.504375 | −1.93914% | 0.980978 | correct_slower / 不晋升 | slower |
| query-in-attention | B | 0.236630 | 6.831250 | −6.00515% | 0.943350 | correct_slower / 不晋升 | slower |
| b1-fused-prepare | B | 0.213700 | 5.890000 | +4.26699% | 1.044572 | correct_faster / 仅 B Branch 晋升 | single_run_faster |

`speedup=parent/candidate`；`reduction=(parent−candidate)/parent`。本次从保存的全部原始样本重新计算 median、mean、population std、min/max、P25/P75，精确值及样本均在原始审计 JSON。这里的 slower 是原规则下该次测得更慢，不是跨重复实验确认性能退化。

两个轮次不同 Runtime，不把 R1 的 0.183795 ms 和 R2 的 0.213700 ms 直接排名。可以确认 R2 的 B1 fusion 相对 Runtime 7 B 父有一次有效改进；不能据此宣称“第二轮整体优于第一轮”或稳定超过原 baseline。

## Memory / Controller

### Snapshot、记录与投影

最新冻结 Snapshot ID：`snapshot:4aecf683cc9e49089b539dabe10fedd8`；version **26**，记录 **1505**，水位 **1530**。实际 UTF-8 大小 **689,053,158 bytes**，完整原字节与逐条 payload 重建 SHA256 均为：

```text
25c8c6475b595fbd471b15077625a36e1e3a7dbeec04415ac9253c6586cfd372
```

Snapshot 4、10、19、23、25、26 均实际重算完整字节摘要；1505 条记录通过 schema v1 规范往返及 SHA256，2925 条 typed links 与表中关系精确相等。原 promotion/frontier Decision 重放得到 **19 条 Best、15 条 Frontier**，与 Snapshot 26 和当前表相等。新只读连接复查水位不变；不是把旧 Snapshot 改写成当前值。

| Runtime 7 范围 | Branch Best | Task Best | Latest Attempt | Last Correct | 显式 Current Parent | Frontier |
| --- | --- | --- | --- | --- | --- | --- |
| 正式 A | length-row | length-row | length-smem | length-smem | A 修复基线 | 两个 A 候选 |
| 正式 B | b1-fused-prepare | length-row | query-in-attention | b1-fused-prepare | delayed-store-wait | 两个 B 候选 |
| 独立诊断 A | diagnostic-baseline | diagnostic-baseline | diagnostic-baseline | diagnostic-single-tile | 原 single-tile | 两个诊断候选 |

Latest Attempt 按 Run 创建顺序、Last Correct 按正确 Evaluation 的保存顺序，因此两者可以不同。Current Parent 来自原显式选择，不会因 Best 晋升而自动改变。所有 15 个 Context × 3 条策略卡已检查，paused=false、deferred=[]；旧 Runtime 的 Best 按原环境保留，七个负例 Context 无 Best，完整表见 Memory 审计。

### 预算与作业状态

| 探索预算组成 | executions / 声明秒 |
| --- | ---: |
| R1 | 5 / 9000 |
| ABBA | 4 / 7200 |
| Runtime 3、5 各一次中断前预留 | 2 / 3600 |
| Runtime 7 A2 / 诊断 / B2 | 2 / 3600，各批一次 |
| **累计已用** | **17 / 30600** |
| **总限额 / 剩余** | **18 / 32400；余 1 / 1800** |

Journal revision **247**；**23 jobs=15 completed+8 cancelled，0 非终态**。全部 52 个历史 Run 为 succeeded 27、failed 11、incomplete 4、not_run 10；failed 包含故意负例与原基线失败，并未删除。Controller 探索 retry=0、repair=0；早期独立基线 repair 仍保留，不能与 Controller repair 混算。

唯一既有 grant 在 revision 210 将限额 16→18、28800→32400，spent 保持 15/27000；绑定原授权、Snapshot 23 与 A2/诊断里程碑，只增加原 B2 两项成本。本次重新核对了授权工件与声明，没有新增 grant。准入使用独立 reservation 域，未混进探索 17 次。声明秒不是 TPU 账单，0 reserved tokens 不表示会话推理免费。

### 恢复与历史 unknown

本次核对 **170 个 durable request、169 个保存回执**：operation ID 唯一且稳定，request digest、Candidate/Parent/Context/Runtime 对齐，回执字节摘要一致。在保留记录中未发现 completed operation 被替换成第二个不同提交。

| Runtime | 原记录与本次核验边界 |
| --- | --- |
| 2 | 第一轮五项终态和工件保留；原桥接中断后回收同 operation，旧数据没有改标签 |
| 3 | 原 R2 四项零 durable request/receipt；首项 cancelled/incomplete，另三项 not_run，预留不退 |
| 4 | attention correctness 有原请求，无最终回执，归档 incomplete，无伪造 Evaluation，远端结果 unknown |
| 5 | 四项恢复尝试同样零 durable request/receipt；首项 incomplete，另三项 not_run，预留不退 |
| 6 | 四项准入已完成；没有探索 Job/Run/request/receipt，不创建或取消不存在的候选 |
| 7 | A2、B2、诊断六个 job completed，原结果与最终 Memory 一致 |

Runtime 4 未决历史 operation 为 `operation:079d2d129595c886b74f35bdc44630a39e7ab03cc9817e67719659c733f64c3a`。本次没有查询远端，所以 **0 本地活动 job 不等于这次远端历史操作已经被证明成功、失败或未执行**。Runtime 3/5 两个 cancelled job 保留非空 operation intent，属于原始归档，不能据此报告仍在运行。[恢复核验](../../.artifacts/p35-final-acceptance-2026-11-16/memory-audit-recovery.json)及[本地进程观察](../../.artifacts/p35-final-acceptance-2026-11-16/local-process-observation.json)分别记录其范围。

协议保障同 Runtime 合作任务的串行、稳定 operation 身份、持久 intent、原子完成回执和本地幂等恢复。completed 回收、running 查询、unknown 阻塞核查、仅 definitely_not_started 允许提交；**不保证任意分布式故障下 exactly-once**。可见 Linux 进程设备句柄检查不能证明隐藏进程或整个采样间隔绝对独占。回执可信依赖原 worker/runner、桥接、隔离路径与工件根；SHA256 不是恶意 producer 的硬件执行证明。

## Software Tests

本次使用现有解释器重新执行，不复用旧 321 项结果。根套件增加到 343 项来自当前已有代码；本审计没有新增生产代码或修改测试预期。

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 -m unittest discover -s tests -v
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 -m unittest discover -s '.artifacts/phase3-2026-11-12' -v
```

| 本次实际运行 | 总数 | 通过 | 跳过 | 失败 / 错误 | unittest 秒 | exit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 修复后根级全量 | 343 | 340 | 3 | 0 / 0 | 568.923 | 0 |
| 修复后 P3.5 辅助完整 discovery | 383 | 383 | 0 | 0 / 0 | 2501.671 | 0 |
| P3.5 辅助完整 discovery，首次执行 | 383 | 353 | 0 | 10 / 23（7 个失败方法） | 2493.076 | 1 |

修复后根测试于 **11:22:05–11:31:35（Asia/Shanghai）**实际运行，Python **3.13.14**；wrapper wall time 569.854 秒。覆盖 Phase 2A–2D+、Phase 3 bridge/backend/worker/protocol/runner、恢复、Evaluator、工件完整性、Memory 与预算。三项跳过是两个 Windows symlink 权限用例和一个 Debian 专属 startup 用例，未放宽权限。

根级[结果与环境](../../.artifacts/p35-final-acceptance-2026-11-16/root-tests.result.json)、[完整 stdout](../../.artifacts/p35-final-acceptance-2026-11-16/root-tests.stdout.txt)、[完整 stderr](../../.artifacts/p35-final-acceptance-2026-11-16/root-tests.stderr.txt)分别保存；unittest 将正常详细输出写到 stderr，stdout 为空不是丢日志。执行前后核对的 243 个源文件摘要均未变化。

修复后辅助全量于 **11:22:28–12:04:10（Asia/Shanghai）**完成，wrapper wall time 2502.050 秒；[结果与环境](../../.artifacts/p35-final-acceptance-2026-11-16/auxiliary-tests.result.json)、[完整 stdout](../../.artifacts/p35-final-acceptance-2026-11-16/auxiliary-tests.stdout.txt)、[完整 stderr](../../.artifacts/p35-final-acceptance-2026-11-16/auxiliary-tests.stderr.txt)均保存并绑定 SHA256。383 是执行次数，包含因模块导入重复执行的 7 项，实际 **376 个不同测试 ID**；根级为 343 个不同 ID。详见[去重计数](../../.artifacts/p35-final-acceptance-2026-11-16/test-discovery-accounting.json)。两套运行前后 243 个源文件摘要均未变化。

辅助 discovery 覆盖当前目录全部 38 个 `test_*.py` 模块，包含 P3.5 confirmation、archive、Runtime 准入、diagnostic/resume、continuation/B2 与 verifier。首次执行失败，原始 stdout/stderr、命令、环境、摘要和源文件身份已原样保存在 [regression-attempt-1](../../.artifacts/p35-final-acceptance-2026-11-16/regression-attempt-1/auxiliary-tests.result.json)。10 个 failure entries 对应 7 个失败方法，另有 23 个 error 方法；实际 353 个成功方法，原 wrapper 简单相减所得 `passed=350` 已通过[独立日志计数](../../.artifacts/p35-final-acceptance-2026-11-16/first-run-corrected-accounting.json)纠正，原错误汇总仍保留。本次不降低原断言、放宽生产保护或删除失败记录。

已确认两类根因：共享 confirmation fixture 的四个预留作业漏填 `spent`，被正式预算保护正确拒绝；手工构造的 `IsolatedAsyncioTestCase` 未初始化异步 runner，其 `doCleanups` 内部捕获错误并跳过清理，使 mock 泄漏到后续恢复测试。修复仅涉及六个辅助测试文件：预算夹具按预留成本补齐状态；五个文件的六处嵌套夹具在 setup 前将 cleanup 注册交给当前正在执行的 TestCase。生产代码、Kernel、Oracle、测试断言和预算保护未改动。

[预算修复](../../.artifacts/p35-final-acceptance-2026-11-16/budget-fixture-debugging.md)先独立复现，再通过两个完整模块 **29/29，110.751 秒，exit 0**。[清理修复](../../.artifacts/p35-final-acceptance-2026-11-16/traceability-recovery-debug.md)按相同顺序从 **17 项、10 failures / 2 errors** 转为 **17/17 通过，29.060 秒，exit 0**；其余嵌套路径另 **3/3 通过，67.891 秒，exit 0**，检查确认 cleanup 成功且恢复 gate 回到原函数身份。原红/绿日志、补丁与修改前字节分别留存。

辅助测试使用临时 Store、synthetic/fake backend，不能计作真实 TPU 实验。两套修复后完整回归均实际退出 0；没有删除失败测试、改变断言或使用历史成功日志代替本次执行，重复 fixture 不计作独立硬件覆盖。

## Performance Confidence

### ABBA 的独立重算

Runtime 3 同 Context、同协议，真实顺序 **C1–T1–T2–C2**；C 为修复基线、T 为 delayed-store-wait，各两个独立 Run。顺序、源码身份、lease 时间、全部 host/Profile 样本和原政策均重新核对。

| 角色 | host median ms | program median μs |
| --- | ---: | ---: |
| C1 | 0.222100 | 6.610000 |
| T1 | 0.223945 | 6.180625 |
| T2 | 0.271240 | 6.225625 |
| C2 | 0.242520 | 6.617500 |

冻结政策 `p35-abba-v1` 要求两对 reduction 均至少 2%、所有 T medians 低于所有 C medians、同角色 spread≤5%。本次重算：

| 维度 | Pair 1 / Pair 2 reduction | C / T spread | 结论 |
| --- | --- | --- | --- |
| host | −0.83071% / −11.84232% | 8.78998% / 19.10195% | inconclusive_drift |
| program | +6.49584% / +5.92180% | 0.11340% / 0.72544% | replicated_observed_improvement |
| 联合 | host 未满足预设稳定判据 | — | inconclusive |

program 的有限重复观察满足预设 program 判据，可以单独报告该指标的复测改善；不能扩称 host、纯 DMA 或普遍端到端加速。每 Run 的 20 个样本不是 20 次独立实验。

### 七个性能问题的回答

1. **delayed-store-wait 的稳定 host 收益未确认。** R1 单次更快，独立 ABBA host 漂移，联合 inconclusive。
2. **ABBA program 改善是有限重复的 trace-duration 证据。** 它支持该 Runtime、该 workload、该指标，不证明完整 public-call 收益或机制。
3. **length-row 仅有单 Run 的 host 改善。** 3.36164% 的 reduction 是观察值，无独立配对复测。
4. **length-smem 不能因 program 较短认定更快。** 原 host 指标反而更慢，二者计量范围不同。
5. **query-in-attention 按原协议更慢。** host 和 program 均回退，但不外推每种配置。
6. **b1-fused-prepare 只有单 Run 改善。** host 4.26699%、program 5.890000 μs；缺独立复测和更广 B=1 分支覆盖。
7. **当前没有充分证据宣布稳定端到端提升。** NOT_CONFIRMED 不等于优化失败或已经证明退化；系统闭环与性能置信度分别验收。

官方 JAX benchmarking/profiling 与 OpenXLA custom-call 解释已对照归档原文和固定版本核对，16 个归档文件摘要一致；没有升级环境。见[官方解释复核](../../.artifacts/p35-final-acceptance-2026-11-16/official-reference-interpretation.md)。

## Independent Review

本次三条分工审计分别由 `/root/accept_raw_evidence`、`/root/accept_traceability`、`/root/accept_memory_recovery` 完成，范围和方法保存在各自报告/JSON/脚本。它们独立重查原始证据，没有把旧 reviewer 的“无问题”结论当本次审查。

另外启动不继承本会话历史的 `/root/accept_final_review`，按用户原始规范审查新审计方法、抽查原始证据及本报告。[独立最终审查记录](../../.artifacts/p35-final-acceptance-2026-11-16/independent-final-review.md)保存了实际方法：独立解析一项候选与两项负例 NPZ、重算 host/ABBA 样本、独立解码 length-row 的 20 个 XPlane 事件、完整散列并重建 Snapshot 26，以及检查原 A2/B2 会话读取和冻结契约。

证据审查未发现 Critical/Important；两项文档 Minor 已修复。首次辅助全量失败记录为 Important，经过六个夹具文件的最小修复、针对性红→绿和两套完整回归后已关闭。Reviewer 独立解析最终日志计数，核对四份 stdout/stderr 摘要，并分别确认两套测试所记录的 243 个源文件与当前文件一致。实际审查范围内无未关闭 Critical/Important；不以代理名称或数量代替审查内容，不宣称所有潜在缺陷不存在。

## Limitations

- 保存的执行后设备输入 cache 缺失，输入不变性仅有原 runner 检查，无法从 retained artifacts 独立复证；CPU Oracle 本次没有重新执行。
- XPlane 已独立重解码，但只证明保留事件与 parser 的一致性；缺少独立 DMA、完整 Wait/Sync、VMEM/MXU 利用率、critical path 和物理因果测量。
- 新候选各一个 Run；ABBA 每角色两个 Run且 host 漂移。正式性能仅 small，不能跨 Runtime 排名或推广到更广矩阵。
- single-tile 零长度诊断覆盖原 single-tile 源码的固定两个 case，不能转移为 B1 fusion 的边界验证；LSE 未暴露。
- Runtime 4 的历史远端 correctness 结果 unknown；合作任务设备协调、本地幂等不等于全局物理独占或分布式 exactly-once。
- bundle/加载路径核对不证明所有文件都执行；回执与 Store 的可信前提仍是原执行 producer、桥接和本地保存环境可信。
- 当前验收证明历史反馈进入第二轮，不证明 Memory/Knowledge/Profile 的独立收益、相对随机搜索优势或通用 Kernel 优化能力。模型分析由原 conversation_session 提供。
- 大型原始工件、SQLite、部分辅助状态仅在本工作区；GitHub 源码不是完整实验数据迁移。本次未提交或推送；已有工作树改动仍保留，不能称工作树干净。
- 本次逐值原始审计覆盖明确列出的 28 个 Run；全 Store 的其余历史只做结构、摘要、关联及恢复核验，没有把所有 52 个 Run 说成重新完成数值验证。

## Final Acceptance Decision

**P3.5 System Workflow：PASS_WITH_LIMITATIONS。Stable End-to-End Improvement：NOT_CONFIRMED。** 系统验收通过适用于下表的声明范围和保留限制，不等同于所有 Kernel、所有配置或稳定性能收益通过验收。

| 层次 | 最终判定 | 已有依据与保留边界 |
| --- | --- | --- |
| Software Infrastructure | PASS_WITH_LIMITATIONS | 修复后根级与辅助完整回归均 exit 0，Store/Memory/预算一致；三项平台跳过和恢复信任边界保留 |
| Real Hardware Integration | PASS_WITH_LIMITATIONS | 原 TPU 执行、正确性、benchmark、XPlane、回执闭包通过本次原始审计；输入 cache 独立复证缺口保留 |
| Multi-Round RSI Workflow | PASS_WITH_LIMITATIONS | 真实 R1 4 Plan/5 Candidate、R2 4 Plan/4 Candidate；冻结历史读取、独立 Candidate 及 source/config 身份、执行和 Memory 回写完整；不扩称自主性或因果有效性 |
| Correctness Validation | PASS_WITH_LIMITATIONS | 固定三 case、六个错误负例、独立两 case 诊断及原始数组通过；受声明矩阵与 retained input 限制 |
| Benchmark Validity | PASS_WITH_LIMITATIONS | 固定计时、20 原始样本、完整输出同步与 Context 分母绑定；host 漂移影响稳定收益置信度 |
| Declared Workload Coverage | PASS | 已声明的 contiguous BF16 正确性、small 性能、原 single-tile 固定诊断均有证据；扩展项未纳入 |
| Stable End-to-End Improvement | NOT_CONFIRMED | 单 Run 改善存在，ABBA host / 联合 inconclusive；未满足稳定端到端收益声明 |

[交付核验](../../.artifacts/p35-final-acceptance-2026-11-16/delivery-verification.json)重新校验测试日志、当前源码身份、文档链接以及原 Store/Snapshot/Journal 状态；核验只写独立审计结果，不写原实验记录。

## 下一阶段建议

1. **Performance Confirmation**：另定预算和预注册判据，在同 Context 下安排足够独立、交错或配对 Run，优先确认 length-row 与 B1 fusion；保留全部样本。
2. **Benchmark Expansion**：先定义更多正式 workload 与 B=1 边界，特别是 fusion 的零长度、多 tile、opaque NaN，再谈跨形状收益。
3. **Analyzer / Knowledge / Memory Effectiveness**：固定任务、预算及评价政策，比较有/无 Memory、Knowledge、Profile 和多 Plan；将搜索效率及性能作为独立指标。
4. **新 Kernel / 接口**：单独规划 Forward、paged 与其他 dtype，建立各自 baseline、Oracle 和准入条件。

以上仅为后续研究建议，不属于本次自动执行范围。剩余 1 execution / 1800 声明秒保持原值。
