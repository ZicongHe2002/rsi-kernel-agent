# P3.5 Final Acceptance & Evidence Audit
## RSI-Kernel-Agent 真实硬件闭环最终验收

> 日期：2026-11-16
>
> 项目：RSI-Kernel-Agent
>
> 当前阶段：Phase 3 / P3.5
>
> 当前状态：原定第二轮四个真实 Candidate 已完成，最终验收尚未完成
>
> 本次目标：完成独立证据审计、全量回归测试、性能结论复核和 P3.5 最终验收
>
> 重要：不要重新实施 P3.0–P3.4，不要重复执行已经完成的 TPU 实验。

---

# 0. 本次任务的核心目标

我们正在开发 RSI-Kernel-Agent，一个能够利用真实 Kernel Profile、Knowledge Search、Memory 和 TPU Benchmark 进行递归自我优化的系统。

此前已经完成：

- Phase 2A：Core Foundation
- Phase 2B：Knowledge / Memory
- Phase 2C：Profiler / Analyzer
- Phase 2D+：Workflow / Controller
- P3.0：环境审计和历史知识恢复
- P3.1：Colab TPU v6e 执行接入
- P3.2：MLA Decode 可信 Baseline
- P3.3：真实 XPlane / Profiler 集成
- P3.4：第一轮真实 Multi-Plan 优化
- P3.5：第二轮四个真实 Candidate 已执行完成

本次不再生成新的大规模优化任务。

**核心目标是判断：目前是否已经具备足够证据，正式宣布 P3.5 在已声明范围内完成。**

必须分别判断以下两个问题：

## A. RSI 系统闭环是否完成？

即系统是否真正实现：

```text
Real Kernel + Real Profile
             |
             v
       Knowledge / Memory
             |
             v
           Analyzer
             |
             v
       Multiple Plans
             |
             v
          Controller
             |
             v
        Real TPU Runs
             |
             v
       Fixed Evaluator
             |
             v
      ExperimentStore
             |
             v
       Memory Snapshot
             |
             v
        Next RSI Round
```

要求证明：

1. 至少两轮真实 TPU 实验。
2. 第二轮真实读取第一轮历史。
3. 第二轮生成新的有依据的 Plan。
4. Plan 确实生成独立 Candidate。
5. Candidate 真实执行并通过固定验证流程。
6. 结果写回原 ExperimentStore。
7. Best / Frontier / Snapshot 状态一致。
8. 中间失败、取消、恢复和无效结果没有被隐藏。

## B. 性能收益是否已经稳定确认？

这与系统闭环是不同的问题。

此前已经出现多个 `correct_faster` 结果。

但部分候选只有一次独立 Run，已有 ABBA 在 Host Latency 上也出现漂移。

因此不能仅根据 Store 晋升或单次计时宣称稳定加速。

最终报告必须分别给出：

- RSI Workflow Validation。
- Correctness Validation。
- Benchmark Validity。
- Performance Improvement Confidence。
- Declared Workload Coverage。

**即使尚未证明稳定加速，只要真实两轮 RSI 系统闭环及全部必需证据通过审查，也可以在明确范围内验收系统能力。**

---

# 1. 项目目录

## 1.1 RSI 主项目

路径：

`C:\Users\fengluo\Desktop\rsi-kernel-agent`

这是当前唯一正式的 RSI-Kernel-Agent 项目。

所有最终审查、测试和报告优先在此项目内完成。

首先阅读：

- AGENTS.md。
- 当前 README。
- Phase 2A–2D+ 验收报告。
- Phase 3 实现与验收资料。
- 当前 Workflow / Controller / Store 代码。
- 最新 Memory / Controller 状态。

## 1.2 当前 Kernel

路径：

`C:\Users\fengluo\Desktop\training\PallasKernels`

当前真实验证范围是：

**MLA Decode contiguous BF16。**

不要未经明确新任务授权就扩展为：

- MLA Forward。
- Paged Decode。
- 其他 dtype。
- 其他 window / soft-cap / mask 组合。
- 所有生产 Workload。

这些可以在最终报告中列为后续覆盖项，但不能标成已完成验收。

## 1.3 Colab

路径：

`C:\Users\fengluo\Desktop\colab`

继续使用已有 Colab Bridge。

本次以离线证据审计为主。

不要因为编写最终报告而自动申请新的 TPU Runtime。

## 1.4 历史资料

路径：

`C:\Users\fengluo\Desktop\project2`

历史实验已经完成初步恢复。

此前共恢复 136 条结构化实验记录，并导入 12 项方法资料。

本次只需要按需核对与真实两轮优化相关的历史来源。

不要重新扫描全部历史目录。

---

# 2. 首先重新确认当前真实状态

在修改代码、生成新实验或写入最终报告之前，执行一次只读状态审计。

检查：

- 当前 ExperimentStore。
- 最新 Snapshot。
- Controller Journal。
- 当前 Round。
- Candidate 状态。
- Run 状态。
- Operation Receipt。
- Budget。
- Branch Best。
- Task Best。
- Frontier。
- Current Parent。
- Paused / Deferred。
- 当前是否存在未完成的真实执行操作。

不要假设 11 月 14 日报告中的状态一定仍是最新状态。

## 2.1 上一次已确认的状态

报告中最新记录为：

```text
Latest Completed Snapshot:
    Version: 26
    Records: 1505
    High Watermark: 1530

Snapshot ID:
    snapshot:4aecf683cc9e49089b539dabe10fedd8

Final Memory:
    status = verified

Original Second Round:
    original_second_round_scope_complete = true
```

Budget：

```text
Total:
    18 executions
    32400 declared seconds

Spent:
    17 executions
    30600 declared seconds

Remaining:
    1 execution
    1800 declared seconds
```

这些是历史报告值。

必须重新读取当前 Store 与 Journal。

如果存在更新的状态，优先使用实际最新状态。

不要将旧 Snapshot 改写为新状态。

## 2.2 禁止重复执行

本次必须默认：

- Runtime 7 第二轮已经执行完成。
- 四个 Candidate 已有真实结果。
- Single-Tile 诊断已经完成。
- 对应 Memory 结果已经归档。

不能为了重新验收而重复创建 Candidate 或 Run。

如果需要补充证明，优先使用已有原始工件进行离线复核。

---

# 3. 第一项验收：真实两轮 RSI 证据链

这是本次最重要的工作。

需要从原始 Store、Memory、Analyzer Session 和真实执行回执中重建两轮 RSI 的完整证据链。

## 3.1 第一轮

第一轮为：

`real-runtime2-round:1`

已知：

- 4 个 Plan。
- 5 个 Candidate。
- 所有 Candidate 完成真实五阶段执行。
- 三组正确性通过。
- Benchmark / Profile 已保存。
- 结果写回 Memory。

第一轮候选：

| Candidate | Host Median | Evaluation |
|---|---:|---|
| Tile 512 | 0.225485 ms | correct_slower |
| Tile 256 | 0.202570 ms | correct_slower |
| Single Tile | 0.193550 ms | correct_slower |
| Delayed Store Wait | 0.183795 ms | correct_faster |
| Compact New Row | 0.221875 ms | correct_slower |

注意：

这些是历史 Runtime 2 的数值。

不能直接与 Runtime 7 的结果合并排名。

## 3.2 ABBA 复测

Runtime 3 曾进行：

```text
Control
Treatment
Treatment
Control
```

即 C–T–T–C。

已知报告结论：

- Program Duration 两组配对降低约 6.50% / 5.92%。
- Host Latency 存在漂移。
- Host 结论为 inconclusive_drift。
- 最终联合结论为 inconclusive。

必须核实：

1. ABBA 原始 Run 和执行顺序。
2. 各 Run 的源码身份。
3. Evaluation Context。
4. Benchmark Contract。
5. 全部原始计时样本。
6. Program Duration 的原始来源。
7. 配对计算方法。
8. 最终结论如何产生。

不能将 Program Duration 改善写成端到端性能已经稳定提升。

不能把同一 Run 中的 20 个样本视为 20 个独立 Run。

## 3.3 第二轮

第二轮在 Runtime 7 完成。

本轮包括两条长期 Strategy：

- Strategy A。
- Strategy B。

原四候选范围全部完成。

### Strategy A

| Candidate | Host Median | Parent Host Median | Classification |
|---|---:|---:|---|
| length-row | 0.204825 ms | 0.211950 ms | correct_faster |
| length-smem | 0.216060 ms | 0.211950 ms | correct_slower |

### Strategy B

| Candidate | Host Median | Parent Host Median | Classification |
|---|---:|---:|---|
| query-in-attention | 0.236630 ms | 0.223225 ms | correct_slower |
| b1-fused-prepare | 0.213700 ms | 0.223225 ms | correct_faster |

以上均为真实 TPU 计时。

但每个 Candidate 只有一个 Run。

**不允许据此直接宣布稳定加速。**

## 3.4 核实每个 Candidate

每个候选必须检查：

- Strategy ID。
- Plan ID。
- Parent Candidate。
- Source Digest。
- Config Digest。
- Build Identity。
- 实际加载模块路径。
- Runtime Environment。
- Workload / Context。
- Compile Evidence。
- Correctness Evidence。
- Benchmark Evidence。
- Profile Evidence。
- Evaluator Decision。
- Store Decision。
- Memory Snapshot。

至少核实五个执行阶段：

```text
generate
compile
correctness
benchmark
profile
evaluate
```

这里 generate、compile、correctness、benchmark、profile、evaluate 是完整生命周期中的实际步骤；如果仓库中将部分步骤组合保存为“五阶段”，以其真实状态机为准，不要为满足字面数量创建不存在的阶段。

核实：

- 没有缺失的必要回执。
- 没有伪造的候选身份。
- 没有跨 Context 混合。
- 没有旧 Runtime 计时被重新贴标签。
- 没有为了优化修改 Oracle 或 Benchmark Contract。
- 所有失败和较慢结果仍然存在。

---

# 4. 第二项验收：第二轮是否真正使用第一轮经验

这是 RSI 系统区别于普通 Kernel Benchmark 的关键。

不能只确认“执行了两轮”。

必须确认第二轮的分析和 Plan 与第一轮真实结果有明确联系。

## 4.1 Analyzer Input

检查第二轮实际：

- AnalysisInputManifest。
- Memory Snapshot。
- 完整参与 Strategy 历史。
- Knowledge 来源版本。
- Profile Evidence。
- 数学模型计算。
- Tool Calls。
- Analyzer Responses。
- PlanSet Publication。

重点确认：

1. 使用的是执行前冻结的 Snapshot。
2. 实际读取了第一轮结果。
3. 引用了真实历史 Evidence。
4. 事实与 Hypothesis 分离。
5. 没有在实验前选择唯一赢家。
6. 不同 Strategy 具有正确 Parent。

## 4.2 分析链路

要求建立：

```text
Round 1 Observation
          |
          v
Round 2 Interpretation
          |
          v
Round 2 Hypothesis
          |
          v
Round 2 Plan
          |
          v
Candidate Code Change
          |
          v
Real TPU Experiment
          |
          v
Memory Update
```

对四个第二轮 Candidate 分别说明：

- 具体利用了哪些历史结果。
- 这些历史结果来自哪个 Run。
- 为什么提出这个修改。
- 修改的具体源码位置。
- 实验是否验证了对应假设。
- 是否发现新的限制或反例。

如果某个 Plan 的依据只是模型推理而非直接测量，明确标记 hypothesis。

不得事后补造 Analyzer 原本没有给出的理由。

## 4.3 不能虚报 RSI 能力

如果实际能够证明：

- 第二轮使用了上一轮 Memory。
- 生成了不同的 Plan。
- 执行并获得新反馈。

则可以证明：

**两轮真实硬件、历史反馈驱动的 RSI Workflow 已经运行。**

但不能因此自动证明：

- Agent 的方法优于随机搜索。
- Knowledge Search 带来了性能收益。
- Memory 一定改善了搜索效率。
- 模型优化能力具有统计显著优势。
- 系统能够稳定优化所有 TPU Kernel。

这些需要后续独立实验。

---

# 5. 第三项验收：Correctness 与独立诊断

当前真实硬件范围：

**MLA Decode contiguous BF16。**

原固定正确性案例：

- decode-small。
- decode-boundary。
- decode-default。

必须检查原始 Correctness Contract 没有变化。

## 5.1 原始正确性

核实：

- attention 输出。
- 完整 updated cache。
- 输入 cache 不变性。
- NaN tail / padding。
- Shape / dtype。
- 逐元素误差。
- BF16 cache 原始位比较。
- 边界 lengths。

原冻结 attention 容差：

```text
atol = 0.01
rtol = 0.01
```

不修改容差。

## 5.2 负例

原 attention 错误负例和 cache 错误负例必须被固定 Oracle 拒绝。

核实两者的：

- 实际执行记录。
- 原始输出。
- Oracle 判定。
- Run 状态。
- Store Decision。

错误候选不能获得有效性能晋升。

## 5.3 Single-Tile Zero-Length 诊断

Runtime 7 已经完成两项独立真实诊断：

- Baseline。
- Single-Tile。

固定诊断覆盖：

```text
decode-small:
    lengths = [0]

decode-boundary:
    lengths = [0, 1, 129]
```

两个候选均通过指定范围正确性和原始 NPZ 复核。

必须核实：

- 独立诊断 Context。
- 原始 Correctness。
- 原始 NPZ。
- Attention 比较。
- 完整 Cache 比较。
- 已保存 Profile。
- 原始 Decision。

但必须保留限制：

- 不代表所有长度已经覆盖。
- 不代表所有 Shape 已覆盖。
- 不代表 LSE 已验证。
- 不代表 Single-Tile 有稳定性能收益。

## 5.4 补齐独立证据复核

此前报告中部分证据仅由原 runner 检查，包括：

- 设备执行时输入 cache 不被修改。
- 部分 cache finite metadata。
- 原始 XPlane protobuf 独立重解码。

本轮优先判断已有工件是否足以离线复核。

如果可以从保存的原始数据独立计算，执行复核。

如果必要的原始输入或设备后状态没有保存，明确记录：

`not independently verifiable from retained artifacts`

不能通过重算原 runner 的 JSON 结果来冒充独立验证。

不要为了补全这几项自动消耗最后的 TPU 执行预算。

---

# 6. 第四项验收：Benchmark 与 Profile 结论

## 6.1 保持原 Benchmark Contract

原协议：

`mla-contiguous-host-sync-v1`

设置：

```text
Warmup:
    5

Measurement Samples:
    20

Outlier Removal:
    None
```

指标：

**Host Synchronized Public-Call Latency**

包括：

- Prepare。
- Attention。
- 完整 Cache Output。
- Host Dispatch / Wait。

不包括：

- 预先输入准备。
- Host-to-Device Transfer。
- Compile。

不能将这个数值称作纯设备 Kernel Duration。

## 6.2 Profile 指标

原 Parser 提供：

- Program Duration。
- Prepare Duration。
- Attention Duration。

这些是 trace-derived 指标。

原系统未提供可靠独立测量的：

- DMA Duration。
- 全部 Wait / Sync。
- VMEM 利用率。
- MXU 利用率。
- Critical Path。

缺失值保持 unavailable。

不能将理论 payload 或数学模型估计包装成 measured。

## 6.3 重新计算结果

从保存的原始样本重新计算：

- Median。
- Mean。
- Std。
- Min / Max。
- P25 / P75。
- Speedup。
- Latency Reduction。
- 配对差异（适用时）。

例如：

```text
Speedup = Baseline Latency / Candidate Latency

Latency Reduction =
    (Baseline Latency - Candidate Latency)
    / Baseline Latency
```

但必须使用真实可比较的 Baseline / Candidate。

不得跨 Runtime 或不同 Context 随意计算加速。

## 6.4 性能结论分类

每个 Candidate 分别提供：

| 结论 | 含义 |
|---|---|
| single_run_faster | 单次有效 Run 比父版本更快 |
| independently_confirmed | 按预先固定规则经过独立复测确认 |
| inconclusive | 数据不足或漂移使结论不确定 |
| slower | 在对应规则下测得更慢 |
| invalid | 测量或证据无效 |

这些可以作为报告层的解释分类。

**不要为了添加这些词修改已经验收的 Store Schema 或旧 Decision。**

## 6.5 最终性能判断

本轮应该回答：

1. `delayed-store-wait` 是否有稳定 Host 收益？
2. ABBA Program Duration 改善能支持什么结论？
3. `length-row` 的 Host 改善是否只有单次证据？
4. `length-smem` 为什么不能因为 Program 较短而认定更快？
5. `query-in-attention` 是否在当前 Context 下退化？
6. `b1-fused-prepare` 是否只有单次更快证据？
7. 当前是否存在足够证据证明稳定端到端加速？

如果没有，明确写：

**Stable end-to-end performance improvement: not confirmed.**

不能将它改写成“优化失败”，也不能改写成“已经稳定加速”。

---

# 7. 第五项验收：Memory、Best、Frontier 与预算

这是重要的系统一致性审计。

## 7.1 最终 Snapshot

当前报告中的最终 Snapshot：

```text
Version:
    26

Records:
    1505

High Watermark:
    1530

Snapshot ID:
    snapshot:4aecf683cc9e49089b539dabe10fedd8

Final Memory Status:
    verified
```

检查：

- Snapshot 完整性。
- Digest。
- Record 水位。
- Run / Candidate 绑定。
- Evidence 关联。
- Decision 关联。
- 当前投影。

Snapshot 不等于当前实时 Store 状态。

如果有更新记录，应分别报告冻结 Snapshot 与实时投影。

## 7.2 Best / Frontier

对所有相关 Context 核对：

- Branch Best。
- Task Best。
- Frontier。
- Latest Attempt。
- Last Correct Candidate。
- Current Parent。
- Deferred。
- Paused。

要重点确认：

1. A/B 各自 Branch Best 没有混淆。
2. Task Best 使用正确 Context。
3. 较慢 Candidate 仍被保留。
4. Frontier 没有因为某个候选领先而错误清除。
5. Latest Attempt 不被误当 Best。
6. Current Parent 必须来自显式合法决策。
7. 旧 Runtime 的 Best 不被直接迁移为新 Runtime 实测。

只有原 Store 的 `consider` 可以完成既定晋升。

不能让审计脚本直接修改 Best。

## 7.3 Budget

最新报告中：

```text
Total Executions:
    18

Spent Executions:
    17

Remaining Executions:
    1

Declared Time Total:
    32400 seconds

Declared Time Spent:
    30600 seconds

Declared Time Remaining:
    1800 seconds
```

必须从当前 Journal 重新核对。

审计：

- 每次真实执行预留。
- 中断 Runtime 的保留成本。
- Retry。
- Repair。
- 已完成 Operation。
- 诊断运行。
- B2 候选。
- 延期和未执行任务。

不能因为过去 Runtime 丢失而自动退还预算。

不能重复扣费。

声明时间不等于真实 TPU Wall Clock 或账单费用。

## 7.4 不默认消耗最后一次执行

本次最终验收以现有证据复核为主。

**不允许仅为了获得一个更好看的加速数字而消耗最后一次 TPU 执行预算。**

如果发现某个阻塞项确实只能通过新硬件执行解决：

1. 明确阻塞的验收条件。
2. 说明已有工件为什么不足。
3. 说明一次执行是否真的足够。
4. 计算剩余预算。
5. 提供有限的补充实验方案。
6. 明确是否需要用户追加授权。

如果一次 Run 仍无法构成独立 ABBA 结论，就不要声称执行一次即可解决统计问题。

---

# 8. 第六项验收：完整软件回归测试

此前完整根级测试结果：

```text
Ran 321 tests in 615.025s
OK (skipped=3)
```

这是此前保存的测试报告，不代表当前代码已重新测试。

后续 Runtime 7 和 B2 工作增加了辅助代码与专项测试。

本次必须重新运行当前主项目的完整根级测试。

## 8.1 命令

优先核对当前环境和实际测试入口。

此前使用：

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -m unittest discover -s tests -v
```

使用现有解释器。

不要自动升级 Python、Pydantic、MCP、JAX 或其他依赖。

## 8.2 测试要求

检查：

- Phase 2A。
- Phase 2B。
- Phase 2C。
- Phase 2D+。
- Phase 3 Backend。
- Remote Worker。
- Recovery。
- Real Protocol。
- Runner。
- Evaluator。
- Artifact Integrity。
- Memory。
- Budget。
- P3.5 Resume。
- B2 Integration。

必须记录：

- 测试命令。
- Python 版本。
- 总数。
- 通过数。
- 跳过数。
- 失败数。
- 错误数。
- 耗时。
- 退出码。
- 完整 stdout/stderr。

不能仅根据保存的旧日志宣称当前测试通过。

如果因环境限制无法运行完整测试，明确记录未运行与阻塞原因，不能复用旧结果冒充新执行。

## 8.3 失败处理

如果新测试失败：

1. 先确认是否为实际代码缺陷。
2. 观察并保存失败。
3. 定位根因。
4. 做最小修复。
5. 运行针对性回归。
6. 再运行完整测试。

不允许：

- 删除失败测试。
- 降低正确性标准。
- 修改测试预期制造通过。
- 进行无关大规模重构。

---

# 9. 第七项验收：Recovery 和远端执行一致性

审查之前发生过的 Runtime 失联和恢复。

包括：

- Runtime 2。
- Runtime 3。
- Runtime 4。
- Runtime 5。
- Runtime 6。
- Runtime 7。

重点核实：

- completed 的 Operation 没有重复执行。
- running 能正确继续 poll。
- definitely_not_started 才允许提交。
- unknown 没有被错误当成失败或未执行。
- 旧运行工件仍然可追溯。
- 中断预算没有被错误退回。
- 不存在跨 Runtime 伪造结果。
- 没有错误地覆盖用户其他 Notebook 活动。

这里优先验证已有证据和已完成的专项测试。

不要求重新制造真实 Runtime 故障。

不要通过终止用户 TPU Runtime 来测试恢复。

## 9.1 信任边界

最终报告必须说明：

- 当前协议是否只覆盖合作任务。
- 是否存在跨 Task Device Arbitration 限制。
- 是否保证分布式 exactly-once。
- 远端 Operation Receipt 的可信前提。
- 原始文件与载入源码身份的验证范围。
- 断电与 Runtime 丢失时的限制。

不允许把已有的本地幂等和持久恢复扩大成未经验证的分布式恰好一次保证。

---

# 10. 最终验收结论必须分层

最终不允许只写：

"Phase 3 completed."

必须分别给出以下结论。

## 10.1 Software Infrastructure

判断：

- 2A–2D+ 是否保持回归通过。
- 真实 Backend 是否保持接口兼容。
- Store / Memory 是否保持一致。
- Recovery 是否满足声明范围。

## 10.2 Real Hardware Integration

判断：

- 是否实际运行过 TPU v6e。
- 是否有真实 Kernel 执行。
- 是否有正确性验证。
- 是否有真实 Benchmark。
- 是否有 Profile / XPlane。
- 是否有可信执行回执。

## 10.3 Multi-Round RSI

判断：

- 是否完成两轮真实优化。
- 第二轮是否真实读取第一轮结果。
- 是否生成不同的 Plan。
- 是否执行真实 Candidate。
- 是否将结果更新到 Memory。
- 是否保持完整谱系和预算记录。

## 10.4 Performance Improvement

单独判断：

- 是否有单次有效性能改善。
- 是否有独立复测。
- 是否有明显漂移。
- 是否符合预先固定的稳定性判定规则。
- 是否已经证明稳定端到端加速。

不得因为某个单次结果 `correct_faster` 就自动回答 Yes。

## 10.5 Scope Coverage

明确已经验证的范围：

- MLA Decode。
- Contiguous。
- BF16。
- 指定 small/boundary/default 正确性。
- 正式 small Benchmark / Profile。
- Single-Tile 指定零长度诊断。

明确未验证或不在本轮范围的内容：

- MLA Forward 真实优化。
- Paged Decode。
- 其他 dtype。
- 更广 mask / window / cap。
- 其他 Shape 的正式性能收益。
- LSE（当前接口未暴露的部分）。
- 全部 DMA / VMEM / MXU 硬件计数器。

---

# 11. 最终报告与交付

创建：

`docs/phase3/phase3-final-acceptance-report.md`

报告必须包括：

## Executive Summary

一句话判断 P3.5 是否通过系统级验收。

## Scope

已验证任务、Workload、设备、运行时与限制。

## Evidence Inventory

列出：

- 两轮实验。
- ABBA。
- Single-Tile 诊断。
- 原始 Run。
- 正确性。
- Benchmark。
- Profile。
- Snapshot。

## Round 1 → Round 2 Traceability

证明第二轮使用第一轮真实历史生成新的 Plan。

## Candidate Results

分别展示每个 Candidate：

- Parent。
- Change。
- Correctness。
- Host Median。
- Program Median。
- Evaluation。
- Memory Decision。
- Stability Classification。

## Memory / Controller

报告：

- Snapshot。
- Records。
- High-watermark。
- Best。
- Frontier。
- Parent。
- Budget。
- Recovery 状态。

## Software Tests

报告本次重新运行的完整测试，不复用旧报告作为当前测试。

## Performance Confidence

明确哪些收益已被独立确认，哪些只有单 Run，哪些 inconclusive。

## Independent Review

如果实际执行独立审查，保存 reviewer、审查范围、发现、复现及修复。

如果未执行，明确标记未完成，不虚构结论。

## Limitations

列出尚未覆盖的 Kernel、Workload、Profiler 能力和硬件统计问题。

## Final Acceptance Decision

只能根据实际证据选择：

- PASS
- PASS_WITH_LIMITATIONS
- PARTIAL
- BLOCKED
- FAIL

每个结论附上具体理由。

建议把系统验收和性能验收分开：

```text
P3.5 System Workflow:
    PASS / PASS_WITH_LIMITATIONS / PARTIAL / BLOCKED / FAIL

Stable End-to-End Improvement:
    CONFIRMED / NOT_CONFIRMED / INCONCLUSIVE / NOT_EVALUATED
```

不能把 `NOT_CONFIRMED` 自动解释为性能退化。

---

# 12. 验收后是否进入下一阶段

如果 P3.5 系统级验收通过：

不要自动启动新的 Kernel 优化轮次。

先交付：

1. 最终验收报告。
2. 当前系统能力总结。
3. 尚未解决的问题。
4. 下一阶段建议。

下一阶段可以独立规划：

### A. Performance Confirmation

设计足够数量的独立、配对真实 Run，验证是否存在稳定端到端收益。

### B. Benchmark Expansion

增加真实 Workload 覆盖，避免只在 decode-small 上比较。

### C. Analyzer / Knowledge / Memory Effectiveness

评估 RSI 中：

- Memory 是否有效。
- Knowledge Search 是否有效。
- 多 Plan 是否优于单 Plan。
- Profile 是否改善方案质量。
- 历史经验是否减少无效探索。

这需要专门的固定任务和实验预算，不应把当前两轮演示直接当成因果证明。

### D. MLA Forward / Paged Decode

在现有系统通过验收后，考虑扩展到新 Kernel 和配置。

这些只是后续研究建议，不属于本次自动执行范围。

---

# 13. 官方技术参考

本次仅用于核对已有 Benchmark / Profile 的正确解释，不需要升级环境。

## JAX Benchmarking

https://docs.jax.dev/en/latest/benchmarking.html

检查：

- JIT 编译隔离。
- Warmup。
- Async Dispatch。
- block_until_ready。
- Host / Device 计时边界。

## JAX Profiling

https://docs.jax.dev/en/latest/profiling.html

检查：

- XPlane Capture。
- Program Trace。
- Compute / Sync。
- Trace 结束前设备同步。

## OpenXLA Custom Call Profiling

https://openxla.org/xprof/custom_call_profiling

注意：

高级 LLO / Runtime Tracing 能力受软件版本和硬件支持限制。

当前 P3.5 不需要升级 JAX / libtpu / xprof。

缺失的硬件计数器仍标 unavailable。

不要为了补齐高级 Profile 数据而改变已经冻结的真实 Benchmark 环境。

---

# 14. 立即执行

现在开始：

**P3.5 Final Acceptance & Evidence Audit**

请依次完成：

```text
1. Read Latest Project State
          |
          v
2. Verify Snapshot v26 / Current Store
          |
          v
3. Audit Round 1
          |
          v
4. Audit ABBA
          |
          v
5. Audit Round 2 A/B
          |
          v
6. Audit Single-Tile Diagnostics
          |
          v
7. Verify Correctness and Raw Artifacts
          |
          v
8. Recompute Benchmark Statistics
          |
          v
9. Verify Memory / Best / Frontier / Budget
          |
          v
10. Review Recovery History
          |
          v
11. Run Full Local Test Suite
          |
          v
12. Independent Evidence Review
          |
          v
13. Produce Final Acceptance Report
          |
          v
14. State P3.5 Final Verdict
```

本次优先离线完成。

不要自动重新申请 TPU。

不要重复已经完成的 Candidate。

不要无理由消耗最后一次执行预算。

不要修改原 Kernel、Oracle、Benchmark Contract 或历史实验记录。

如果证据已足够，直接完成 P3.5 系统级验收。

如果证据不足，明确指出阻塞项、已有证据缺口，以及是否需要额外硬件实验。

**最终目标：对 RSI-Kernel-Agent 真实 TPU v6e 两轮 Multi-Plan 优化闭环做出有证据支持、范围明确、可复核的正式验收结论，并将稳定性能收益作为独立判断，不虚构加速。**