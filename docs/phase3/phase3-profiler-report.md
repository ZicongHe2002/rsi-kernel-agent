# P3.3 Profiler / XPlane Report

日期：2026-11-12（Asia/Shanghai）。本报告保留原 Runtime repair Run 的真实 Profile 结果，并补充 Runtime 2 的 A/B 独立基线与独立四模式诊断。此次文档核验只读取已归档工件，不访问远端，不把理论硬件计数器写成实测结果。

## Gate 状态

P3.3 **passed，限当前 Decode contiguous 固定 workload、默认 ProfileOptions 和原 duration parser 的接入范围**。Runtime 2 的两项真实错误负例均被固定 Oracle 拒绝，A/B 独立基线均取得完整 benchmark/profile 及可用 normalization，P3.2 当前范围已通过；见 [Baseline 报告](phase3-baseline-report.md)与 [Runtime 2 独立核验](../../knowledge-site/manifests/phase3-2026-11-12/runtime2-baseline-verification.json)。这不代表多轮优化或 Phase 3 最终验收完成。

下列原 Runtime repair Profile 的历史数据保持原样：raw XPlane 为 78,518 bytes，parser report 为 11,717 bytes，parser SHA256 为 `eb718d742010fbeccf9c45eb8eaf35300d2d23814be7222a7499cb739b9bf561`。report `errors=[]`、每个目标 group 20 个有效样本，normalization availability=`available`。它记录该次 operation 的 capture、回传、保存和 typed parser 结果完整。

## Capture 与绑定

Profile 使用 `default_profile_options`，calls=20、warmup=5，目标设备 `/device:TPU:0`。它与 benchmark 属于同一 Run `baseline-repair-run:22398773cfc39fbfd571714a9578835e2add586ab10b873cf017fa9ee750a8f9`，但 operation 独立：

- benchmark：`operation:9c143d2e1c8b65ca9481388df569feb0779ae5392ea70d3776053d7a0566076e`
- profile：`operation:f1b1a128d220d19aa17ed8120e97188e60068edaf4c663db5409aefa51c9f20a`

benchmark 时间为 12:33:45.144012–12:33:54.238588 +08:00；Profile 时间为 12:34:39.243643–12:34:52.443768 +08:00，二者没有重叠。Profile lease 记录同一 Runtime、同一 owner Run、`interference_observed=false`。原始 XPlane ref 为 `88fef4fdab3a900adf7d3d1a72f97105c5a6a1c9d563b02445d3b8cd676ee45d`。

## Parsed device duration

Parser 的时间来源为 TPU `device_duration_ps`，单位换算为 us。独立脚本重新读取保存 report，确认每个样本的 `duration_ps / 1e6` 与 mean/median 一致：

| Group | Count | Median | Mean | Invalid |
|---|---:|---:|---:|---:|
| program `jit_rsi_mla_5728798e51d32c84_0(...)` | 20 | 6.60375 us | 6.62625 us | 0 |
| kernel `mla_decode_contiguous_prepare` | 20 | 0.726875 us | 0.74925 us | 0 |
| kernel `mla_decode_contiguous_attention` | 20 | 5.03875 us | 5.07225 us | 0 |

每组样本均绑定 `/device:TPU:0`、program_id 和 trace offsets；`multi_device_span` 明确 unavailable。本次固定 runner 把 compiled public callable 交给原 `_capture_entry`，覆盖 prepare/attention/cache 输出；这不同于原工具默认“单独 registry entry、排除 public wrapper”的调用方式。此处的 program/device duration 仍不能解释为跨设备 critical path 或完整用户端到端时间。

## 可报告范围

本 Profile 可报告目标 program/custom-call 的 device duration、调用次数、设备和 parser 统计，并可通过原 Evidence→raw XPlane→parsed report 闭包追溯。它不能分解 DMA、Wait/Sync、VMEM、MXU/XLU、spill 或其他硬件 counters；不能由 custom-call duration 推导 DMA 独立时间，也不能把 kernel duration 称作 MXU busy。parser 没有提供完整 LLO 正文。`multi_device_span` 保持 unavailable，不能用最长设备时间替代跨设备跨度。

Profile 原始 protobuf 的 SHA/size、已保存 parser report 和 normalization 已独立核对；本地没有 TensorFlow/XPlane parser 环境，因此本次独立脚本没有重新解析 raw protobuf，只复核了归档 parser 输出及其 hash。后续若 Runtime 可恢复，应保留原 parser 版本和 trace mode，并在需要时重新跑 parser 作为追加证据，不覆盖当前回执。

上述原 Runtime Profile 验证时，Oracle 负例尚未完成且旧 Runtime 已丢失；这份历史 Profile 本身不能证明错误 Candidate 会被拒绝，也不证明优化收益。后续负例证据来自独立 Runtime 2 Runs，不能倒填进原 Run。原时点限制与工件索引见 [independent-baseline-verification.json](../../knowledge-site/manifests/phase3-2026-11-12/independent-baseline-verification.json)。

## 原 Analyzer 实际接入检查

2026-11-12，原 `prepare_round → capture_inputs → start_session → submit_response` 已读取此真实基线的冻结证据；history工具返回65条完整记录，profile工具通过原归一化器返回60个事件、availability=available。两张状态卡分别保留baseline与Oracle验证路线。DMA、waits、resource metadata、LLO仍为unavailable。完整输出与manifest摘要见 [real-profile-analysis-smoke.json](../../knowledge-site/manifests/phase3-2026-11-12/real-profile-analysis-smoke.json)。

这次会话只执行完整history、Profile及Knowledge读取，没有final PlanSet/publication、没有Candidate或新硬件操作。原Runtime结果不会成为新Runtime的当前对照。四个命名TRACE模式的准确入口及版本条件见 [官方技术参考审计](phase3-official-reference-audit.md)，后续实际诊断记录如下。

## Runtime 2 默认 Profile

Runtime 为 `tpu-v6e1-s-euw4a1-344ww12q09usn`，固定环境为 Python3.11.17、JAX/jaxlib0.8.1、libtpu0.0.30、TensorFlow2.20.0、XProf2.22.1，单设备实际报告 TPU v6 lite。A/B 同源码、同 Context 分别真实执行，未复制或重命名另一条路线的测量。

| 独立基线 | Raw XPlane | Parser JSON | Program median | Prepare median | Attention median |
|---|---:|---:|---:|---:|---:|
| A：baseline-repair | 78,877 bytes | 11,729 bytes | 6.624375 us | 0.694375 us | 5.055625 us |
| B：baseline-b | 78,749 bytes | 11,710 bytes | 6.615 us | 0.69375 us | 5.07 us |

两者均为 `default_profile_options`，每组20个有效样本、availability=available、errors=[]，与各自正式 benchmark operation 不重叠。独立 reviewer 只读核验完整 Evidence 闭包、raw SHA/size，并重算保存 parser 样本和 normalization；没有在本机重解 protobuf。精确 Run/context/ref 及限制见 [runtime2-baseline-verification.json](../../knowledge-site/manifests/phase3-2026-11-12/runtime2-baseline-verification.json)。同字节的时长差异不能当成优化收益。

## 四模式独立真实诊断

任务专属诊断经原 bridge 在同一 Runtime 2 隔离环境完成。它是 `minimal JAX trace-mode capability only`，没有正式 MLA Run、ProfileBundle 或 promotion，未改变已固定 MLA runner 的默认 trace mode。源码 SHA256 为 `c4c3e01e386c6606940ffb30dbb10f781266841d908d4b1d31bf2745e6ff7fdc`，请求摘要 `fe25e2d2347c828d34f1230b4a11c84753678d34be5a846fa7e8f40ae8cec0b1`。

计算为 float32[128,128] 全1输入的 JIT `(x @ x) + x`；编译和2次warmup在 trace 前，各模式在 trace 内调用5次并逐次 `block_until_ready`。四模式均 API succeeded、errors=[]、同步完成，标量检查均返回129.0。

| `advanced_configuration['tpu_trace_mode']` | 已保存 TPU device events | Raw XPlane bytes | 本次实际观测 |
|---|---:|---:|---|
| TRACE_ONLY_HOST | 0 | 197,107 | 有原始PB；没有 `/device:TPU:0` plane |
| TRACE_ONLY_XLA | 16 | 31,842 | 5个jit主程序、5个融合计算及额外slice/squeeze/copy事件 |
| TRACE_COMPUTE | 16 | 31,831 | 此最小程序观察到与ONLY_XLA相同事件名称/数量，不能据此概括所有程序 |
| TRACE_COMPUTE_AND_SYNC | 134 | 36,283 | 在上述16个事件之外出现SyncWait、SyncNoWait、Read、Set、Add名称 |

**API 接受参数与完整模式语义生效分开判断。** AND_SYNC中的额外118条名称计数为SyncWait43、SyncNoWait1、Read14、Set17、Add43；这些是原PB解码清单中的事件名称和数量，不是独立硬件利用率或DMA时间。探针在 `stop_trace` 前执行 `device_get(result[0,0])`，引入 `jit_dynamic_slice`、`jit_squeeze` 与相关copy/sync。因此不能把全部134事件归因于五次matmul，更不能外推到MLA的wait、DMA、VMEM/MXU counter或critical path。没有将嵌套事件相加，也没有报告模式间性能收益。

原PB、trace.json.gz、options、environment、stdout/stderr及模式结果已按原字节回收进唯一ArtifactStore。回收索引见 [trace-mode-diagnostic-collected.json](../../.artifacts/phase3-2026-11-12/trace-mode-diagnostic-collected.json)，此次只读检查见 [trace-mode-verification.json](../../knowledge-site/manifests/phase3-2026-11-12/trace-mode-verification.json)：核验原字节SHA/size及已保存事件清单，没有本地重解PB。

诊断使用原worker runtime锁和持久诊断guard。回收后由不导入JAX的独立控制进程观察：诊断PID17292已退出、guard已清、两把锁可取、receipt完整、可见设备占用为clear，见 [runtime2-idle-after-trace.json](../../.artifacts/phase3-2026-11-12/runtime2-idle-after-trace.json)。原文件为PowerShell UTF-16；验证器按该编码读取并保留SHA。该观测不保证未来时段或其他不可见主机绝对独占，正式worker仍执行自身设备检查。
