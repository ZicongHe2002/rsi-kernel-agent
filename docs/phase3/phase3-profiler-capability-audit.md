# Phase 3 Profiler / XPlane 能力审计

2026-11-12。证据类型：**Source audit**。本轮静态检查准确路径 `C:\Users\fengluo\Desktop\training\PallasKernels\tools\tpu_duration_profile.py::read_profile`，没有采集本轮真实 TPU Profile，也没有以历史文档证明当前 TPU v6e 或 TRACE mode 可用。

## 现有链路与版本

现有工具链是 `tools/get_tpu_duration_info.py::run_config → tpu_duration_runtime.prepare_entry → _capture_entry → tpu_duration_profile.read_profile`。源码 SHA 清单见 [pallaskernels-source-inventory.json](../../knowledge-site/manifests/phase3-2026-11-12/pallaskernels-source-inventory.json)；parser SHA-256=`eb718d742010fbeccf9c45eb8eaf35300d2d23814be7222a7499cb739b9bf561`。解析依赖 `google.protobuf` 和 `tensorflow.tsl.profiler.protobuf.xplane_pb2`，后者必须在远端实际 import 后确认；pyproject 声明 TensorFlow2.20.0/XProf2.22.1，不表示本机/Colab已安装。

`run_config(config,warmup=5,calls=20,out_dir=...)` 接收 bundled YAML 名或本地 YAML path；拒绝 interpret，要求 JAX process_count=1、实际 TPU 数足够。`prepare_entry` 保留 registry geometry/sharding，依据规格编译，确认 runtime executable 使用的设备集合正好是配置设备，并把 inputs 移到 compiled input shardings 后等待。不会因为 devices.num_devices 改大就自动复制单卡 Kernel。

`_capture_entry` 先调用 warmup 次并同步，然后 `jax.profiler.ProfileOptions()` 设置 `python_tracer_level=0`，`start_trace(...,profiler_options=options)`；每次使用 `StepTraceAnnotation(tag,step_num=i)` 并等待全部 pytree 输出；finally `stop_trace()`。随后解析并写入 `duration.json`。采集目录独立，保留原 `*.xplane.pb`。通过 CLI 调用默认 tool 还会运行 git rev-parse/status；Colab 可在现有 notebook 进程直接调用函数，避免第二个 JAX 进程争用 libtpu。

当前 capture **没有配置** `TRACE_ONLY_HOST/TRACE_ONLY_XLA/TRACE_COMPUTE/TRACE_COMPUTE_AND_SYNC`。本轮未确认任一 mode 在当前 libtpu/JAX/v6e 的支持。应在实际 Runtime 查看 ProfileOptions/API、记录准确设置及错误，逐模式隔离测试；未测试或被拒绝均记录 unavailable，不能把“start_trace成功”写成“所有 trace modes成功”。不为查模式升级环境。

## Parser 实际输入输出

```python
read_profile(directory: Path, *, calls: int,
             device_ids: Sequence[int], program_name: str | None) -> dict
```

要求 calls 为正整数且非 bool；递归目录必须恰好一个 `*.xplane.pb`，protobuf 损坏拒绝。按 `/device:TPU:<JAX device id>` 筛选设备；重复 plane 报错。只读取 `XLA Modules`、`XLA Ops`、`Async XLA Ops` 三种 line，program 名匹配当前标签（允许 jit_ 前缀与程序数字后缀），kernel 为 custom-call。必须有匹配模块的 program_id 才归属 custom-call；不把同设备其他程序事件当作目标。

唯一有效时长是正且有限的 `device_duration_ps`，换算 us 为 `/1e6`；**不退回**普通 event.duration_ps、host 时间、包络或理论值。offset 为有限非负 `device_offset_ps`，无效则 null。stat 同名重复变为无效。原始 event duration/offset 的单位是 ps，不代表 1 ps 测量精度。

| 返回字段 | 语义 |
|---|---|
| `time_source/unit/profile/calls/devices` | TPU device_duration_ps、us、原 PB 路径、调用数、期望设备 |
| `groups[]` | kind program/kernel、name、plane、line_id/line、program_id；count、mean/median/p95/min/max us、events_per_measured_call、invalid_samples |
| `groups[].samples[]` | duration_ps、device_offset_ps、step_num、run_id；**此 run_id 是 trace 内部字段，不是 RSI Run ID** |
| `errors/warnings` | 目标/设备丢失、无效 kernel 时间、调用数不匹配等；不能忽略 errors 后晋升 |
| `longest_device_per_call` | 多设备且每设备每次调用有唯一 step 关联时取该次最大 duration；不按事件顺序 zip |
| `multi_device_span` | 固定 unavailable，因未验证跨设备时间原点对齐 |

step 来源是明确 step_num、唯一 host group→step，或同 program_id 且区间包围的唯一父 module；仅相同 count/run_id/order 不足以配对。若 program 时间缺失但身份明确，可保留有效 kernel 样本；如果无法确认所属 program，则拒收未归属 kernel。每设备必须至少有有效 custom-call；program 样本数应等于 calls，kernel 样本数必须是 calls 的整数倍。Parser 从源码看具备这些检查；本轮尚未运行它自己的 tests。

## 可报告和不可报告的事实

| 信息 | 证据类型与范围 |
|---|---|
| program/custom-call 单事件设备 duration、offset、样本统计 | 成功解析后为 trace-derived，绑定具体 PB/设备/程序/Run |
| 正式同步 host benchmark | measured，来自独立 benchmark operation，不能与 Profile 的 trace-derived duration 混名 |
| event 区间并集 | 只有同设备同 clock domain 才可另行计算；不是自动得到 critical path |
| DMA 独立时长、Wait/Sync 独立时长 | 原 parser 不提取，unavailable；custom-call duration 内可能包含这些但不可分解 |
| VMEM 使用、spill、MXU/XLU 利用率、硬件 counters | 原 parser 不提供；需要独立 compiler/硬件证据，不由 duration 推导成实测 |
| LLO 完整正文/HLO 全文 | 原 parser 不导出；另存编译器 artifact 才可分析 |
| 跨设备统一跨度或“纯计算”时间 | 不支持；longest duration 不是跨度，kernel 不是 MXU busy |

Program 是父层范围，custom-calls 是子层；二者不能相加。子事件相互重叠也不能直接求和作总时间。不同设备/host/trace clock domains 不混加。原 capture 的 `scope` 明确为 registry entries measured separately, excludes public wrapper；不能自动当作 whole MLA public API 的端到端时间。

## MLA 特定接入风险

当前 Forward registry 只有一个 `_mla_fwd_pallas` 整体 pipeline trace，内部含多个 Pallas calls。其 inputs aval 已做序列 padding，make_inputs 未 padding：非整除序列原 duration 工具会拒绝 shape，不能改变 shape后还标原 public workload。默认 aligned Forward 可复用；public padding/crop 成本要由独立 public benchmark 覆盖。LSE=false 与 true 是不同 specialization，当前 registry 固定 false。

当前 Decode contiguous registry 返回完整 native（prepare+attention+cache output）一个 trace，inputs 不被 donation，适合原工具重复调用。Paged trace donation cache，必须先定义每步 cache 生命周期才能采集；不得把已被 donation 的旧数组重复传入。Profile 中 prepare/attention 的单独时长可以观察，但不能省去 prepare/full-cache-write 然后宣称整个 Decode 加速。

`pallas_kernels.kernels.utils.profiling.tpu_timer` 调用的 `sync_tpu()` 是 placeholder，无同步保证；不要用它建立正式计时。`benchmarks.profiler.measure` 已使用 `jax.block_until_ready`，是可复用的正式 host timing 实现。

## 2C Evidence 接口映射与 P3.3 Gate

采集前冻结 Task/Workload/context/Strategy/Candidate/Run、source/config/build recipe、实际 module 路径及依赖 hashes、compiled specialization、Runtime session、hardware/software fingerprint。capture 请求/receipt 保留 operation_id/request_digest。采集后的 raw XPlane 和 parser JSON 都通过原 ArtifactStore 保存，另保留 parser source SHA、调用参数、trace mode 原值/未知状态、clock domain。RSI Run ID 使用 adapter 显式绑定，不从 trace 的内部 run_id 猜测。

Profile 保存 raw→parsed→normalized evidence；正式 Benchmark 与 Profile 使用独立 operation 和采集时间边界，由预先声明的契约归属同一逻辑 Run。回执明确两次 operation，不能把其他 Run 的 Profile 挪作本 Run 证据。通过原 2C normalization 标 `trace-derived`，不支持字段标 unavailable；Analyzer packet 读准确的 AnalysisInputManifest/Evidence refs。若 2C normalized schema 需要时间区间，从 offset/duration显式转换，保留设备 clock domain与来源；缺失 offset 时只保留 duration，不能伪造 timeline 起点。

P3.3 必须真正证明：已通过可信 baseline 的候选在 TPU v6e 执行；采集完成；原 PB 回传且 hash核验；原 parser 真实解析（或清楚报告缺失能力）；目标 program、设备、调用次数、loaded-source/Run绑定有效；不同时间口径没有混加；原 Analyzer 取得真实 Profile。仅源码审计、mock parser tests、历史文档或生成了 JSON 不满足 Gate。

历史 `docs/tpu_duration_info.md` 报告过 2026-11-04 v5e-1 / JAX0.8.1 的三项真实采集；这是外部历史文字来源，需要原始 PB和版本才能独立核验，且不能证明当前 v6e、MLA workload 或新 Runtime 已通过。现存 `tests/unit/test_tpu_duration_profile.py` 等覆盖设备字段、错误、program身份、多卡step与同步生命周期；它们仍属于 CPU/synthetic software验证，应与本轮真实 Profile分别报告。
