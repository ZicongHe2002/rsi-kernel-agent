# 冻结协议与官方资料的解释核对

2026-11-16。本次只读使用 2026-11-12 已归档的官方页面及 `jax-v0.8.1` 标签文件，重新核对全部 16 个文件的 SHA256；没有重新请求网页、安装依赖或改变运行环境。结果见 [source-reference-audit.json](source-reference-audit.json)。同一检查重新散列权威 Kernel 清单内的 390 个文件，全部与原清单相同；这不代表清单以外文件或 Git 工作树的状态。

| 官方资料 | 本次核对点 | 冻结实现与可解释范围 |
| --- | --- | --- |
| [JAX Benchmarking](https://docs.jax.dev/en/latest/benchmarking.html)；归档 `jax-benchmarking.txt` 323–365 行 | JIT 初次编译、异步派发、输入传输分别处理，计时等待结果 | `mla_runner.py::measure_compiled` 在样本外完成编译和输入同步；调用原 `benchmarks/profiler.py::measure`，对完整返回 pytree 执行 `jax.block_until_ready` |
| [JAX Profiling](https://docs.jax.dev/en/latest/profiling.html)；归档 `jax-profiling.txt` 434–437 行及固定标签文档 | 结束 trace 前等待设备工作完成 | 真实 runner 绑定原 capture/parser，保存独立 profile operation；不能把 host public-call 样本改称纯设备 Kernel duration |
| [OpenXLA Custom Call Profiling](https://openxla.org/xprof/custom_call_profiling)；归档正文及原版本审计 | 编译时 LLO 信息、运行时细粒度 tracing、周期计数器是不同能力，受版本与硬件约束 | 现有 JAX 0.8.1/libtpu 0.0.30/xprof 2.22.1 的已验范围只有已保存 XPlane 的 program/prepare/attention duration；独立 DMA、完整 Wait/Sync、VMEM/MXU 利用率和 critical path 仍 unavailable |

`mla-contiguous-host-sync-v1` 的 5 次 warmup、20 次样本、不移除异常值是本项目冻结实验政策，不能说成官方文档要求的唯一采样次数。host 样本覆盖 prepare、attention、完整 cache 输出及主机派发/等待；编译和预先输入传输在样本外。Profile 单独采集，不把 trace 派生时长与 host 样本互换，也不相加重叠或父子事件。

原 runner 在 correctness 阶段比较 `inputs[2]` 的设备回读字节与原 host cache；已保存输入 NPZ 不等于保存了执行后的设备输入。能否独立重验该项由原始工件审计确定，不能用原 JSON 中的 passed 替代缺失的设备后状态。

以上是协议和来源解释核对，不是新 TPU 能力测试。来源完整路径和摘要均保存在 JSON；原详细资料见 [官方参考审计](../../docs/phase3/phase3-official-reference-audit.md)。
