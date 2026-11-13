# P3.2 MLA Baseline Report

日期：2026-11-12（Asia/Shanghai）。本报告只整理归档的真实 TPU Run 证据，并由独立只读脚本重新核对 Store、回执和原始 NPZ。它不把历史 Runtime 或 CPU fixture 当作本轮结果。

## Gate 状态

P3.2 Gate **passed，限当前 MLA Decode contiguous 固定矩阵及已验证 Oracle 负例范围**。原 Runtime repair Run 完成三组 correctness、20次正式 benchmark及Profile；Runtime 2又完成A/B同源码独立基线，并真实拒绝known-wrong attention/cache两项负例。后文原 Runtime数值保留原时点身份，新增结果在「Runtime 2」节分别列出。当前结果不代表所有 mask/window/cap 组合已验证：默认矩阵只有 `decode-small`、`decode-boundary`、`decode-default`，三者固定 contiguous、`sliding_window=0`、`soft_cap=0`。这不是优化收益或Phase3最终验收。

## 来源、候选和环境

修复前的原始 baseline Run `mla-run:02c54a047534c109da0fab01ffd7629dcde9fb73dc494f975f27e48cfd8409ac` 在 `decode-boundary` 编译时失败，归档错误为 Mosaic `result/input offset mismatch on non-concat dimension`。修复 Candidate 只改 `build_mla_decode_contiguous` 顶层定义：先对完整 new-KV batch padding，再按行选择；oracle 函数 AST、其他源码字节和权威源码 SHA 保持不变。该变更引用历史 patch，但当前 Candidate 与 diff 都有独立 SHA 记录，不能把历史 patch 当作本轮运行证据。

修复 Run 为 `baseline-repair-run:22398773cfc39fbfd571714a9578835e2add586ab10b873cf017fa9ee750a8f9`，Candidate 为 `baseline-repair-candidate:f3a6d0550533cde6f8dfb0e2c2c1b67749780cd806213a633c6d74fe543709b2`。三组 correctness 使用同一冻结 Oracle bundle、同一输入生成规则和 BF16 原始存储；attention 用 `atol=.01, rtol=.01` 的 allclose，完整 cache 用原始字节 exact 比较。

Run 环境为 Runtime `tpu-v6e1-s-euw4a0-xyzyx7kofigt`，设备实际报告为 1 个 `TPU v6 lite`，单进程；Python 3.11.17、JAX/jaxlib 0.8.1、libtpu 0.0.30、TensorFlow 2.20.0、xprof 2.22.1。每个阶段的实际 environment 与 immutable context 完全匹配，lease 报告 `exclusive_worker=true`、`interference_observed=false`。旧 Runtime 后来丢失；此报告只读取已落盘回执和工件，不重连或重投。

## Correctness 结果

独立脚本重新读取 ArtifactStore 中的 NPZ manifest 和数组字节，验证每个 NPZ 内部 digest/shape，再从 candidate/oracle NPZ 重算结果：

| Case | 输入数组 | Attention | 完整 cache |
|---|---:|---|---|
| decode-small | 4 | 0 个 tolerance violation；max abs 0.00390625；RMS 0.0002860686 | 原始 BF16 字节相同 |
| decode-boundary | 4 | 0 个 tolerance violation；max abs 0.001953125；RMS 0.0001848576 | 原始 BF16 字节相同；opaque NaN 区域保留 |
| decode-default | 4 | 0 个 tolerance violation；max abs 0.001953125；RMS 0.0001138157 | 原始 BF16 字节相同 |

三组输出均符合记录的 `passed=true`。独立校验遍历 9 条 Evidence，确认全部 stage artifact refs 都能从 Evaluation 的证据闭包到达，并验证 49 个去重工件、总计 509,947,198 bytes。独立校验没有执行 `consider`、`integrate_result` 或任何 Store 写入。

## Benchmark

正式 benchmark 仅测 `decode-small`，使用原 `benchmarks.profiler.measure`，完整 public contiguous prepare、attention 和 cache 输出均在同步边界内；compile 独立记录，不混入 samples。warmup=5、measurements=20、无 outlier 丢弃，样本统计由原始 20 个值重新计算：

- median：0.2207150005 ms
- mean：0.2223145000 ms
- std：0.0122962398 ms
- min / max：0.2071100000 / 0.2534100000 ms
- P25 / P75：0.2134650001 / 0.2250975001 ms
- compile：478.026231 ms

正式 benchmark lease 时间为 `2026-11-12T12:33:45.144012+08:00` 至 `12:33:54.238588+08:00`。这是 host synchronized public-call latency，不能称纯设备 kernel 时间或绝对物理独占。

## 原始证据与限制

完整真实回执、NPZ、输入 manifest、HLO、specialization、设备观察、stderr 和环境信息均通过原 ArtifactStore 保存；独立验证报告为 [independent-baseline-verification.json](../../knowledge-site/manifests/phase3-2026-11-12/independent-baseline-verification.json)，只读核验脚本为 [verify_baseline_locally.py](../../knowledge-site/manifests/phase3-2026-11-12/verify_baseline_locally.py)。原始材料包括 [baseline-evidence-summary.json](../../.artifacts/phase3-2026-11-12/baseline-evidence-summary.json)、[原始失败](../../.artifacts/phase3-2026-11-12/baseline-result.json)、[repair 结果](../../.artifacts/phase3-2026-11-12/baseline-repair-result.json)、[Candidate diff](../../.artifacts/phase3-2026-11-12/candidates/baseline-repair-1/change.diff) 与 [repair 分析](../../.artifacts/phase3-2026-11-12/candidates/baseline-repair-1/repair-analysis.json)。repair 分析中“尚未执行”的字段是执行前写入的历史记录，后续真实结果以上述 Run 回执为准。

原 Store 已将本 repair Run 记录为 `confirmed=true` 并通过原 `consider` 成为该 context 的 Best；这是真实记录状态，不代表所有阶段验收条件已经完成。`parent_latency_ms=null`，原 parent 编译失败，没有可比的父计时，所以结果分类是 `correct_inconclusive`，没有已验证性能提升。

上述原 Runtime 报告写入时，真实 known-wrong attention/cache 两项负例尚未完成；这个缺口随后由下述新 Runtime 的独立 Run 关闭，没有改写旧回执。更多 mask/window/cap workload 仍未覆盖，不能扩称三 case 的适用范围。旧 Runtime 丢失不抹除已归档证据，新环境测量不混入旧对照。

## Runtime 2：负例与 A/B 独立基线

新 Runtime `tpu-v6e1-s-euw4a1-344ww12q09usn` 于2026-11-12 07:40:20UTC分配；环境版本与上述隔离环境相同，已按实际观测建立新 Context `runtime-context:89820a7b421896bf463bb70f671ed7ce4c827086a4e37227ed7c1fc34d34d8b8`。A使用原修复Candidate的新Run；B创建本策略Candidate、精确父指向修复Candidate，source/config/build字节相同，独立编译、正确性、计时及Profile，没有复用A回执。

| 本轮基线 | Run | 三组 correctness | small median / mean | Std / 样本数 |
|---|---|---|---|---|
| A：baseline-repair | `runtime-run:4686bd6eb923d2ce96be095360d34f0c3f533a30c4cf64c8e0bf70c3d275f223` | 均通过 | 0.193195 / 0.195683 ms | 0.008610 ms / 20 |
| B：baseline-b | `strategy-seed-run:c339eda9de36ae18d4916561688194d9fd100ebb7fff38f30fdc2f473cdfcf08` | 均通过 | 0.215365 / 0.218733 ms | 0.011128 ms / 20 |

二者均 `correctness=passed`、`timing_validity=valid`、`confirmed=true`，默认Profile每组20样本、normalization available，benchmark与Profile无重叠。parent_latency_ms仍为null，分类correct_inconclusive；其含义是缺少该Run绑定的可比父计时，不是正确性失败。A/B同字节测量差异及跨Runtime差异均不能作为代码优化收益。

独立reviewer已只读复核两Run的完整Evidence闭包与49个去重stage工件/每Run、从原20个样本重算统计、核验raw XPlane SHA和保存parser/normalization。报告与可重跑只读脚本见 [runtime2-baseline-verification.json](../../knowledge-site/manifests/phase3-2026-11-12/runtime2-baseline-verification.json)、[verify_runtime2_baselines_locally.py](../../knowledge-site/manifests/phase3-2026-11-12/verify_runtime2_baselines_locally.py)。这次review核验NPZ摘要和正确性回执指标，没有再次重算NPZ数值比较；原Runtime独立NPZ重算结果仍保留在前文。

两个故意错误Candidate使用原small/boundary case的独立验证workload/context，Oracle、容差、原输入输出契约不变：

| 负例 | 新 Run | 实际结果 | 执行边界 |
|---|---|---|---|
| attention输出修改 | `runtime-run:9d5212d6c003a102b015985eced689242d5df5ef10160dfce9e5bb64bf52defa` | correctness failed、分类incorrect、confirmed=false | generate/compile/correctness，之后停止 |
| cache输出修改 | `runtime-run:e2a2ecf56d89101d8439099cb38ef1925d98f75bb5b429169dd3755b7fcb9ed0` | correctness failed、分类incorrect、confirmed=false | generate/compile/correctness，之后停止 |

两项没有benchmark/profile receipt、latency=null，没有晋升Best；保留失败Evaluation/Decision是拒绝记录，不是性能成功。该事实核验见 [trace-mode-verification.json](../../knowledge-site/manifests/phase3-2026-11-12/trace-mode-verification.json)中的oracle_negative_controls，原结果为 [attention](../../.artifacts/phase3-2026-11-12/oracle-negative-attention-runtime2-result.json)与 [cache](../../.artifacts/phase3-2026-11-12/oracle-negative-cache-runtime2-result.json)。固定Oracle已在实际TPU上识别这两类已知错误，不能扩称所有可能错误均已覆盖。
