# Phase 2C 离线 Profile 解析器审计与归一化

2026-11-11。此次只读检查现有外部源码、对应测试和说明文档；没有导入或执行该仓库代码，没有调用 capture/runtime、Kernel、TPU 或 Colab CLI。现有解析器的行为以静态源码为依据，不能称为本机真实 protobuf 解析验收。

## 原始源码与依赖

外部根目录：`C:\Users\fengluo\Desktop\training\PallasKernels`。

| 相对路径 | SHA-256 | 作用 |
| --- | --- | --- |
| `tools/tpu_duration_profile.py` | `eb718d742010fbeccf9c45eb8eaf35300d2d23814be7222a7499cb739b9bf561` | 已保存 XPlane 的离线解析 |
| `tools/get_tpu_duration_info.py` | `38804b7af3ecad7a0279f63cdd48e9f3f54d9c543d76cb9a302f8f4963a7175c` | 真实设备 capture 与报告包装；本轮不执行 |
| `tools/tpu_duration_runtime.py` | `2b0ec43f4bc47bcd1c09505e1c21a13ef888ac6f4b87a6ec27dc83ebad1c706f` | registry / JAX 编译、设备与输入绑定；本轮不执行 |

已读取外部 `AGENTS.md`、`context/programming.md`、`context/correctness.md`，以及 `tests/unit/test_tpu_duration_profile.py`、`docs/tpu_duration_info.md`、`pyproject.toml` 相关部分。历史 [Phase 1 审计](../mla-integration-audit.md) 保持不变。

离线入口位于 `tpu_duration_profile.py:83`：

```python
read_profile(directory: Path, *, calls: int,
             device_ids: Sequence[int], program_name: str | None) -> dict
```

标准库之外，在函数内延迟导入 `google.protobuf.message.DecodeError` 和 `tensorflow.tsl.profiler.protobuf.xplane_pb2`。外部项目声明 Python `>=3.11,<3.12`、TensorFlow `2.20.0`、XProf `2.22.1`、JAX `0.8.1`。本轮使用的知识库 Python 3.13 环境不能视作该项目的合规运行环境；其 `site-packages` 目录未发现 TensorFlow / protobuf / JAX / XProf 分发目录。未安装或升级依赖。

## 输入、事件与实际能力

输入目录递归搜索 `*.xplane.pb`，必须恰好一个文件；以 protobuf `XSpace.FromString` 解码。`calls` 必须为正整数。目标设备由 `/device:TPU:<device_id>` 精确选择。

- 仅查看 `XLA Modules`、`XLA Ops`、`Async XLA Ops` 三类轨道。
- 目标 module 名称按 `(jit_)?<program_name>(<数字>)?` 完整匹配。
- kernel 事件要求 `hlo_category == custom-call` 或名称包含 `custom-call`，并要求属于目标 module 的 `program_id`。
- 时长只使用正且有限的 `device_duration_ps`，没有回退到普通 event envelope 或 host wall-clock。偏移缺失保留 null。
- host `group_id` 可关联明确的 step；唯一包含该 kernel 区间的同 program module 可补充 step/run 关联。事件顺序或相同 run ID 不足以证明跨设备 step 配对。
- program 与各 custom-call 分别统计，不相加父子耗时。多设备逐调用最长时间只在每设备都有唯一显式 step 配对时可用；该值不是跨卡整体执行 span。
- 未验证跨设备时钟原点，`multi_device_span` 总是 unavailable。
- 重复设备轨道、无目标 kernel、无效时长、数量不符合调用次数等保留 errors/warnings；不能仅因 JSON 可读而认为 capture 完整。

不提供独立 DMA 次数/字节/带宽、wait/synchronization 分解、硬件性能计数器、资源容量、compiler 估计、LLO 内容。事件完整名称可含 custom-call 文本，不等同于 LLO 文件。原工具文档明确 kernel 时间可能包含 DMA、同步、通信和等待，不能当作纯 Matmul 时间。此前 v5e 的历史记录不证明 v6e 支持或本轮硬件执行。

## 现有输出 schema

单入口 `entry_N/duration.json`：

```text
time_source = "TPU XPlane device_duration_ps"
unit = "us"
profile: path hint
calls: positive int
devices: ["/device:TPU:0", ...]
groups: [{
  kind: "program" | "kernel", name, plane, line, line_id, program_id,
  count, mean_us, median_us, p95_us, min_us, max_us,
  events_per_measured_call,
  samples: [{duration_ps, device_offset_ps, step_num, run_id}],
  invalid_samples
}]
longest_device_per_call: [explicit-step aggregate or unavailable reason]
multi_device_span: {status: "unavailable", reason}
errors: [string]
warnings: [string]
```

统计由 ps / 1e6 得到 µs，p95 使用排序后位置 `(N-1)*0.95` 的线性插值。原始 ps 样本仍保留。

根目录 `duration.json` 的 `schema_version=2` 包含 config、kernel、shape、tiling、params、dtype、seed、device_config、devices、calls_per_entry、warmup_per_entry、versions、commit、dirty、compiler_flags、output_directory、entries。各 entry 再增加 entry_name / input_arrays。其计时范围是 registry 内部编译入口，排除 public wrapper。该生产者没有 RSI Candidate/Run 绑定，也不执行 correctness 或晋升确认。

## 本轮复用方式与新增接口

[profiles.py](../../../src/rsi_kernel_agent/profiler/profiles.py) 复用现有离线解析输出，不实现另一套 XPlane 解码器。`normalize_duration_report(report, *, binding, artifact_refs)` 接收一个 entry 的报告，重新计算样本统计并核对原 summary。未知/无效/布尔/非有限样本、错单位、缺失 kernel program 身份或坏结构拒绝；错误与缺失样本保留，数量不完整单独标记。原文件路径只是提示，原始字节是否可得由 artifact_refs 和上层工件校验决定。

`parse_offline(reader, directory, *, calls, device_ids, program_name, binding, artifact_refs)` 接受调用方显式提供的现有离线 reader。缺失依赖返回 unavailable，其他解析错误继续抛出供调用方处理。本轮没有运行真实 reader；依赖缺失测试使用一个仅抛 `ModuleNotFoundError` 的替身，测试的是适配器处理，并非 protobuf 解析质量。

`binding` 精确包含：

```json
{
  "strategy_id": "A", "parent_id": "baseline", "candidate_id": "A1",
  "run_id": "run-A", "context_id": "ctx", "provenance": "synthetic",
  "declared_identity": {
    "source_digest": "64位小写SHA256", "config_digest": "64位小写SHA256",
    "build_digest": "64位小写SHA256"
  }
}
```

这里只验证 binding 的字段与格式，数据库中的同 Task/Strategy/Parent/Run/context 关系由输入捕获层验证。输出总是保留 `binding_qualification=declared_identity_only`，不声称验证了 TPU 已加载源码。`artifact_refs` 是 2A 的 `{digest, size}` 列表；此纯解析接口不自行读工件，真实 bytes 核验由上层 ArtifactStore 执行。

另一接口 `normalize_events(events, *, binding, artifact_refs)` 支持明确提供的 saved/synthetic 通用事件；它不凭名称猜出 DMA 或 wait。每项字段严格为：

```text
id, name
kind: program | operation | custom_call | dma | wait | synchronization
device, clock
classification: measured | trace_derived | compiler_estimated | inferred
start_ps: number | null
duration_ps: number | null
bytes: number | null
source_refs: nonempty [string]
reason: string | null (缺失时长/时间戳时必填)
metadata: {
  compiler_metadata/resource_metadata/llo?: {value, classification, source_refs}
}
```

所有数字必须非负且有限，不接受布尔数值；缺失不是零。metadata 值由输入证据明确提供，保持独立分类；不会从事件名捏造计数器或硬件容量。

共同输出 schema v1：`format=normalized_profile.v1`，包含 binding / binding_qualification / artifact_refs / events / operation_stats / interval_unions / metadata / capabilities / quality / critical_path / multi_device_span。能力缺失时 availability=unavailable 且有原因。legacy 原始时长标 measured，重新计算的统计与区间并集标 trace_derived；synthetic provenance 始终保留，不能因此当作真实测量。

区间并集按 `(device, clock, classification)` 分组；measured 和 trace_derived 共用事实时钟组，compiler_estimated / inferred 分别隔离。相交与嵌套区间取并集，缺 offset 的事件不补零且结果标 partial。跨设备或时钟不合并。并集范围明确为已提供事件集合，既不是端到端延迟，也不是 critical path。高 DMA count 不产生瓶颈结论。

## 测试与边界

[test_profiles.py](../../../tests/test_profiles.py) 使用手算的合成 JSON/事件 fixture，不伪称真实 TPU 数据。

初次测试在实现前遇到预期的缺失模块 `ModuleNotFoundError`（`rsi_kernel_agent.profiler`）。实现后 13 项通过；随后新增边界测试实测发现并修复：全缺失时间戳仍宣称 interval available、容器/巨大整数泄漏非验证异常、kernel 缺 program ID 及样本覆盖不足未明确标记。最终 16 项全部通过，见 [完整输出](profile-tests.txt)。初次默认沙箱运行遇到 uv trampoline 权限错误，使用受限本地测试命令自动审批后运行现有解释器，未改变系统权限或环境。

真实 XPlane protobuf 字节解码未在此环境验证；读入保存 JSON 的一致性不能独立证明生产者真实测量了指定候选。一般事件的 source_refs 在此模块仅做结构验证，语义存在性与适用策略由输入清单及 Analyzer 的引用目录负责。capture 的 target/module 匹配来自现有解析器；2C 不声称增加 TPU 后端支持或自动发现真实硬件计数器。
