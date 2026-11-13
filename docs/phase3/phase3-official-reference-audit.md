# Phase 3 官方技术参考审计

2026-11-12。官方资料部分来源类别为 **Source audit**：以无认证 HTTPS GET 实际读取6份官方页面，均HTTP200；另取JAX官方 `jax-v0.8.1` 标签的4份源码/文档。该资料审计没有执行网页示例、安装依赖或访问Runtime。后续独立最小JAX诊断已在真实Runtime 2验证四模式，其追加结果明确标为 **Real TPU profile capability probe**；本次文档更新只读归档，不改变固定环境。

原始HTML、提取正文、URL、响应状态、抓取UTC时间、Last-Modified、SHA256均保存于 [官方网页清单](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/manifest.json)；版本来源见 [JAX0.8.1清单](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-0.8.1-source-manifest.json)。正文由标准库HTMLParser剔除script/style并归一化空白，原HTML字节另存，不把正文SHA当原响应SHA。抓取时间约2026-11-12 05:51–05:53 UTC，即上海13:51–13:53。

## 最重要的版本与硬件结论

1. **四个TRACE模式是 `ProfileOptions.advanced_configuration["tpu_trace_mode"]` 的字符串值。** 它们不是 `XLA_FLAGS`、`LIBTPU_INIT_ARGS`的同名命令行flag，也不是Pallas CompilerParams。JAX latest和`jax-v0.8.1/docs/profiling.md`都明确给出该入口、四个值及默认`TRACE_ONLY_XLA`。
2. **高级周期硬件计数器限制为Ironwood TPU7x及之后。** 不能因为v6e能产XPlane，就宣称支持该功能或实测MXU/VMEM计数。
3. **LLO静态分析并非一概限制为TPU7x，但最新指南要求更新的软件栈。** 官方Custom Call文档写明v6e/v5e可用、libtpu≥0.0.42 / JAX≥0.10.2用于LLO debug info；更细runtime custom-call tracing需要libtpu≥0.0.44 / JAX≥0.11.0，并要求XProf≥2.24.0。当前固定0.8.1/0.0.30/2.22.1低于文档门槛，因此不能照抄flag后声称功能已接入。
4. 正式MLA Run继续使用默认ProfileOptions及原program/custom-call duration parser。后续独立最小JAX诊断已实际设置本页四模式并回收原PB：全部API成功，HOST无TPU plane，XLA/COMPUTE各16个device events，AND_SYNC134个且出现同步名称。它不验证MLA对这些模式的全部语义，不提供LLO注册或周期计数器证据。**文档存在、API接受和实际设备事件，是不同层次的证据。**

## 已抓取资料

| 官方来源 | 归档正文 | 本轮用途 |
|---|---|---|
| [JAX TPU Pipelining](https://docs.jax.dev/en/latest/pallas/tpu/pipelining.html) | [jax-tpu-pipelining.txt](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-tpu-pipelining.txt) | memory space、DMA等待、multiple buffering、emit_pipeline、lookahead |
| [JAX Async Dispatch](https://docs.jax.dev/en/latest/async_dispatch.html) | [jax-async-dispatch.txt](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-async-dispatch.txt) | dispatch和执行的区别、block_until_ready |
| [JAX Benchmarking](https://docs.jax.dev/en/latest/benchmarking.html) | [jax-benchmarking.txt](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-benchmarking.txt) | compile/warmup/transfer/同步边界 |
| [JAX Profiling](https://docs.jax.dev/en/latest/profiling.html) | [jax-profiling.txt](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-profiling.txt) | ProfileOptions及四种TRACE模式 |
| [OpenXLA Kernel Profiling](https://openxla.org/xprof/kernel-profiling) | [xprof-kernel-profiling.txt](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/xprof-kernel-profiling.txt) | TPU7x+周期counter限制；页面显示更新2026-08-26 |
| [OpenXLA Custom Call Profiling](https://openxla.org/xprof/custom_call_profiling) | [xprof-custom-call-profiling.txt](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/xprof-custom-call-profiling.txt) | LLO/compiler/runtime flag、软件最低版本、v6e支持边界；页面显示更新2026-10-16 |

latest页面不保证与当前0.8.1一致，因此额外审阅 [0.8.1 profiler源码](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-0.8.1-profiler.py)、[同标签profiling文档](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-0.8.1-profiling.md)、[pipeline源码](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-0.8.1-pipeline.py)、[TPU公开导出](../../knowledge-site/manifests/phase3-2026-11-12/sources/official/jax-0.8.1-tpu.py)。按tag读取的源码已保存SHA；它证明该tag中的接口，尚未逐字节比对远端安装wheel。

## TRACE模式：实际入口、含义和当前状态

JAX0.8.1 `_src/profiler.py:47`定义`ProfileOptions`；`:101`的`start_trace`和`:247`的`trace`都接受`profiler_options`。同tag `docs/profiling.md:319`开始列出模式，`:379`给出`options.advanced_configuration`示例。按文档形式：

```python
options = jax.profiler.ProfileOptions()
options.advanced_configuration = {"tpu_trace_mode": "TRACE_COMPUTE_AND_SYNC"}
jax.profiler.start_trace(trace_directory, profiler_options=options)
outputs = fixed_callable(*fixed_inputs)
jax.block_until_ready(outputs)
jax.profiler.stop_trace()
```

上面是版本审计后的API形式；后续诊断按该形式实际设置四模式。正式runner仍只接受default/default_profile_options；四模式实验放在独立诊断operation，保存原始trace和环境，没有修改已接受MLA Run的protocol或伪装成其测量。

| 值 | 官方含义 | 当前可确认范围 |
|---|---|---|
| TRACE_ONLY_HOST | 只追踪host，不采集device trace | Runtime 2 API成功，原PB没有TPU device plane；不作为设备duration证据 |
| TRACE_ONLY_XLA | device的XLA层操作；文档默认值 | 显式设置成功，最小JAX程序保存16个TPU device events |
| TRACE_COMPUTE | device compute操作 | 显式设置成功，此最小程序保存16个device events，事件名称/数量与XLA相同；不概括其他Kernel |
| TRACE_COMPUTE_AND_SYNC | device compute与synchronization事件 | 显式设置成功，保存134个device events，含SyncWait/SyncNoWait/Read/Set/Add；未推导独立DMA时间 |

文档表示不识别的key/value会返回InvalidArgumentError；Python层将options交到底层ProfilerSession，不能从Python源码证明libtpu0.0.30的全部硬件collector行为。原`read_profile`解析器主要提取program/custom-call duration，增加trace mode也不会自动增加其DMA/Sync schema或证明critical path。

追加真实探针环境为 Runtime `tpu-v6e1-s-euw4a1-344ww12q09usn`、TPU v6 lite单设备、JAX/jaxlib0.8.1、libtpu0.0.30、XProf2.22.1。float32[128,128]全1输入的JIT `(x @ x)+x`，每模式5次同步执行、结果129.0，四项api_status均succeeded且errors=[]。**API成功不自动证明完整模式语义。** 尤其标量检查`device_get(result[0,0])`位于stop_trace之前，产生额外dynamic_slice/squeeze/copy及同步事件；不能把全部134事件归于主matmul，事件inventory也不证明MLA、DMA、VMEM/MXU counter或完整wait分解能力。

精确设置、原始PB摘要、保存事件清单与只读验证见 [trace-mode-verification.json](../../knowledge-site/manifests/phase3-2026-11-12/trace-mode-verification.json)，捕获/恢复边界和实际统计见 [Profiler报告](phase3-profiler-report.md)。诊断没有正式MLA Run、ProfileBundle、Benchmark或promotion；采集结束后独立无JAX控制进程已确认诊断进程退出和可见设备归属clear，但这只是该次观测。

## LLO与硬件计数器不能混用

最新Custom Call指南明确区分三项：

| 能力 | 官方配置 | 官方版本/硬件条件 | 当前固定环境 |
|---|---|---|---|
| 编译时LLO debug info | `LIBTPU_INIT_ARGS=--xla_xprof_register_llo_debug_info=true` | JAX≥0.10.2 / libtpu≥0.0.42；v6e/v5e可用；相关XProf CLI≥2.24.0 | 软件门槛未满足，未尝试 |
| runtime LLO bundle/custom-call细粒度trace | `--xla_xprof_enable_custom_call_tracing=true` | JAX≥0.11.0 / libtpu≥0.0.44；可用于支持的TPU | 软件门槛未满足，未尝试 |
| 周期runtime performance counters | `ProfileOptions.advanced_configuration`中的`tpu_enable_periodic_counter_sampling`或`tpu_enable_kernel_profiling`等 | Ironwood TPU7x+ | v6e不符合页面的硬件条件 |

`LIBTPU_INIT_ARGS`类flag按官方要求必须在首次JAX/libtpu初始化前设置。它们与`tpu_trace_mode`的profile-call选项不是同一个配置通道。文档中的升级/安装命令仅作为来源文本保留，未执行；不为取得更多trace而升级固定基线环境。

Custom Call指南还指出，fine-grained trace可显著增大trace并丢弃外层HLO begin/end；缺事件不等于Kernel消失或时长为0。其LLO内部时间轴部分由指令cycle模型插值，不应把每个插值span当独立实测硬件counter。静态LLO、compiler资源估计和runtime duration仍须分别分类。

## 流水与Benchmark的可复用知识

JAX TPU流水文档说明：HBM与VMEM之间以DMA复制；显式提供semaphore的copy必须wait。multiple buffering按BlockSpec的pipeline_mode配置，emit_pipeline可在kernel内部建立pipeline，lookahead会增加控制流开销。JAX0.8.1的公开TPU模块确实导出`emit_pipeline`、`make_async_copy`、`async_copy`、`sync_copy`；同tag pipeline源码读取`pipeline_mode.buffer_count/use_lookahead`，默认buffer_count=2，同时明确output buffer_count>2不支持、output lookahead不支持。这些源码支持研究相同版本的实现，不能证明某个MLA变体能编译或更快。

文档还说明Megacore当时针对v4/v5p；不要把`dimension_semantics=("parallel",)`当成当前单v6e有两个TensorCore的证据。实际device/topology来自runtime probe。

Benchmark与Async Dispatch官方文档共同要求等待实际执行完成、分开初次编译与重复运行，并说明host/device transfer成本。现有冻结`benchmarks.profiler.measure`对完整pytree执行`jax.block_until_ready`，与本任务attention+cache双输出相符。`perf_counter + block_until_ready`仍是host synchronized public-call时长；可以结合单独XPlane解释设备事件，但不能无说明改名“纯设备Kernel计时”，也不能把必要prepare或cache输出挪出计时。

## Knowledge去重与后续使用

已通过原Knowledge MCP完整读取10个现有方法后去重。`hist-decode-dma2`、`hist-profile-accounting`涵盖流水机制和事件解释，保留其历史来源身份，不另造同义历史方法、不把它们改写为官方实测。新增官方来源笔记 `RSI/References/phase3-official-2026-11-12.md`，revision `5658e0180d4ab3ab739b46209a51611ef3593a665acec4ddb521bd00deaa6f77`；新增`official-jax-synchronized-benchmark`与`official-tpu-profile-mode-version-gates`。两项均保存后按revision回读，来源状态available；MethodRecord标`source_supported`，local_experiment_refs为空。精确回执及去重结果见 [official-knowledge-import.json](../../knowledge-site/manifests/phase3-2026-11-12/official-knowledge-import.json)。后续通过原Analyzer manifest固定读取，不能只凭本文笔记替代当前测量。

官方资料与独立模式探针不单独打开优化Gate；当前P3.2/P3.3通过依据是另行完成的真实Oracle负例、Runtime2同context独立基线与完整Benchmark/Profile绑定，见 [Baseline报告](phase3-baseline-report.md)。本页不证明后续优化收益或多轮闭环。
