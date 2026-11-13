# Phase 3 Workload、Oracle 与 Benchmark 契约

2026-11-12。**Source audit / proposed validation protocol**。本页给出从实际源码导出的可执行矩阵和验收口径，不包含本轮 CPU/TPU 测试结果。来源版本及 SHA 在 [PallasKernels 审计](phase3-pallaskernels-audit.md) 与 [清单](../../knowledge-site/manifests/phase3-2026-11-12/pallaskernels-source-inventory.json)。未修改原 Reference、容差、Kernel 或 benchmark。

## Task 划分和第一目标

第一目标选 `mla_decode` 的 `params.interface=contiguous`。Task 的语义是单 token latent attention 加完整独立输出 cache，含 prepare、padding、append、完整 cache preservation 与 attention；不包括上游模型 Q/KV 投影及 RoPE（输入已经是它们的结果）。Decode dense/paged、Forward 分别固定契约，不互相替代。不同 shape/config/environment/protocol 使用不同 comparison context；不能用缩小 shape 获得的延迟替换大 shape Best。

### Decode 矩阵

所有有效行固定 BF16，H16，R64，scale `0.07216878364870323`、window0、cap0、VMEM budget120795955、single device、`interpret_mode=False`，输入随机种子沿原 cfg=0；额外正确性种子单独记录。表内是由源码约束导出的候选验证矩阵，尚未证明所有行真实 TPU 可编译。

| Workload | B | capacity N | L | block | lengths | 用途 |
|---|---:|---:|---:|---:|---|---|
| decode-vcs | 1 | 128 | 128 | 128 | `[128]` | 原 `mla_decode_vcs`，最小原配置编译/正确性 smoke |
| decode-small | 1 | 1024 | 512 | 1024 | `[1024]` | 保留实际 latent 和 tile 的较小 workload |
| decode-medium | 4 | 4096 | 512 | 1024 | 全满 | batch/容量扩展 |
| decode-default | 8 | 16384 | 512 | 1024 | 全满 | 原默认完整配置，生产代表性仅指仓库形状，不声称实际用户生产分布 |
| decode-boundary | 3 | 256 | 128 | 128 | 分组 `(0,1,129)`、`(-3,128,256)`、`(127,255,300)` | 原单元测试已有边界；验证 clip、跨 tile、inactive |
| decode-mask-cap | 3 | 256 | 128 | 128 | 同 boundary | 原已有 window17/cap2；window129/cap0 等显式独立配置 |

`q[B,H,L+R]`、`new[B,L+R]`、`cache[B,N,L+ceil(R/128)*128]`、`lengths[B]` int32。测试至少区分有限正常输入、cache 无效 tail/padding 为 NaN 的合法 opaque storage、有效区域 NaN/Inf（结果应被 evaluator 拒为非有限，不能因原代码可传播 NaN 而判正确）、错误 dtype/shape（明确拒绝）。不能要求 BF16-only API 支持 FP32；dtype 覆盖包含 BF16 正例和 FP32 拒绝。

输入从原 `get_kernel(...).make_inputs(jax.random.key(cfg.seed))` 生成，保留四个张量的 shape、dtype、原始字节 hash；boundary 更改 lengths/opaque tail 后重新保存真实输入身份。先完成 reference，再调用待测 Kernel，防止将 donation 或状态污染误作 oracle 差异。Contiguous 输入 cache 的字节必须保持不变。

### Forward 矩阵（单独 Task）

固定 Dnope128/Drope64/Dv128、BF16、causal=true、原 tolerance。前四行原始源码 A09 条件成立；都必须记录 assume-safe/guard 前提。一般数据推荐 `assume_a09_safe=False`；用原 YAML true 时先对固定输入证实 bound 与 V finite，并把前提写入 contract，不声称通用数据适用。

| Workload | B/H/S | Rq/Rkv | Q/KV tile | rank tile | 范围 |
|---|---|---|---|---|---|
| fwd-vcs | 1/4/256 | 256/128 | 256/256 | 256/128 | 原 vcs 小配置 |
| fwd-medium | 1/4/1024 | 256/256 | 512/512 | 256/128 | history+causal diagonal，多 tile |
| fwd-default | 1/16/4096 | 1536/512 | 1024/1024 | 256/256 | 原默认实际配置 |
| fwd-heads32 | 1/32/4096 | 1536/512 | 1024/1024 | 256/256 | larger H；需要资源实测，不预设可运行 |
| fwd-boundary | 1/4/257 | 256/128 | 128/128 | 256/128 | 一般路径、padding/crop；assume-safe=false |

FWD 不支持任意 mask/lengths/cache；causal 的重要负例是改变 future token，确认之前 prefix 不改变。RoPE 不在 Kernel 内，Q-PE/K-PE 输入一致是职责边界；不可把上游旋转省略当作本 Kernel 优化。return_lse=false 与 true 具有不同输出契约和编译 specialization，正式 benchmark 不混合统计。已有 LSE 单元/TPU 测试应保持，真实验收分别比较。

## 固定 Oracle

Decode 重用 `runner.reference_functions()[0]` 对应的原 `mla_decode_paged_reference`；这是独立 NumPy FP64 host 运算，经 `jax.pure_callback` 返回 BF16，`run_reference_op` 负责 CPU placement 及所有输出等待。其算法并不调用 Pallas。由于 oracle 和 kernel 当前共居一个源码文件，必须在允许 Candidate 改文件之前冻结 oracle 来源，评估时验证原字节/函数来源，禁止 Candidate 修改 oracle、Benchmark 或 evaluator。

Forward 重用 `jax_ops.mla_fwd.mla_fwd_reference` 与 `run_reference_op`。投影和概率的 dtype 舍入是原任务数学语义，不能替换为“更准确”但不同语义的参考再选择有利阈值。两边必须消费相同输入数组。

对每个输出先比较 shape/dtype，检查 attention/output/LSE 有限，再按原配置 `atol=0.01, rtol=0.01` 执行 `np.testing.assert_allclose(reference, actual, ...)`，保持原调用次序和容差判断方向；另报告 max absolute、max relative（分母策略固定并报告）、RMS error，指标不能代替逐元素 allclose。Cache 是 opaque storage；完整 cache 允许保存未使用区域的 NaN，所以其正确规则是 BF16 原始 uint16 位模式与 reference 相同，而不是要求 cache 全部 finite。默认有限 fixture 可同时报告 finite 信息。

Known-wrong 负例必须是独立 Candidate 或明确标为 oracle-negative 的隔离执行：例如在最终 attention 中改变一个确定性元素（偏差大于原 tolerance）、或漏掉 cache append。原 oracle 必须分别拒绝 attention 错和 cache 错，不能只检查最后 JSON 中一个 passed 标签。负例不进入 Best；其输入、输出、错误度量和拒绝证据保留。不能靠改变 reference 或 threshold 让错误 Candidate 通过。

## 复用 Benchmark 的测量口径

当前原工具 `benchmarks.profiler.measure(fn,*args,repeats)` 使用 `perf_counter()`，调用后 `jax.block_until_ready` 等待整个输出 pytree；返回每次原始毫秒。这是**host synchronized call latency**，含 Python/dispatch/wait 开销，不能叫纯设备 Kernel 时间。`warmup()` 首次调用也含可能的 JIT，不单独区分 compile。`tpu_timer` 的 `sync_tpu()` 是空函数，不能用于可信结果。

初版 formal protocol 应冻结为版本化配置，按以下顺序复用原 measure：

1. 在隔离 bundle 中通过原 registry 创建 runner，固定全部 shape/dtype/static options，取得 oracle 通过记录。对 public runner 包一层具稳定名称的 `jax.jit`；`.lower(*inputs).compile()` 单独计 compile wall time，不进入 latency samples。
2. 输入放到指定实际 TPU，`jax.block_until_ready(inputs)`；记录初始 host→device transfer（如果需要），与正式计时分离。所有必需的 Kernel 内 prepare/append/cache copy 保留在 compiled runner 里。
3. 用原 `warmup(compiled,*inputs)`，记录次数（建议固定5；不是默认1就不记录），每次完成所有输出。已编译后 warmup 不混入 compilation。
4. 原 `measure(compiled,*inputs,repeats=20)` 取得完整 samples；重复使用 contiguous 的不可变输入。每次返回 attention 和整个 cache 都必须完成。用新回执标识 call、protocol、runtime、workload、candidate、loaded-source、compile specialization、队列 lease。
5. 从原 samples 计算 mean/median/std/min/max/P25/P75、样本数；固定“不剔除任何 outlier，只记录诊断”，除非以后先版本化新的剔除规则。少样本不声称统计显著改进。
6. 正式 benchmark 与 Profile 分开 operation 和采集时间边界，按事先冻结的契约归属同一逻辑 Run；不能借用其他 Run 的 Profile。候选/基线对比可在同一独占队列按固定 AB/BA 或交错顺序重复测量，保留原始样本与顺序；发现漂移/并发不明时标 invalid/inconclusive。

这扩展编排、统计和证据记录，核心调用、同步与算法来自原 benchmark，不另造第二套可随意改变口径的测量算法。程序应只晋升同 context 的有效 confirmed measurement，且由原 `consider` 执行。

不能证明 Runtime 没有其他 Notebook/进程使用 TPU 时，不声称全局硬件独占；至少同 runtime 的 compile/correctness/warmup/benchmark/profile 串行化、跨 Task 统一 lease，记录干扰检查与实际限制。不要终止用户任务。执行 timeout 也不等于设备工作失败，恢复先查 operation receipt。

## 原入口复用示意

以下是接口关系，不是已执行命令，也不是最终 adapter 实现：

```python
cfg = load_kernel_config("mla_decode", overrides={"shape": {"batch": 1, "seq_len": 1024}})
runner = get_kernel("mla_decode", cfg)
(inputs,) = runner.make_inputs(jax.random.key(cfg.seed))
reference = run_reference_op("mla_decode", runner.reference_functions()[0], *inputs)
actual = jax.block_until_ready(runner(*inputs))
# 按原 tolerance 检查 attention，按原始 BF16 位模式检查完整 cache。
# 然后编译公共 runner，使用原 warmup / measure；不要只计 attention 子 call。
```

实际 adapter 还需实现持久 operation、回执、来源验证、错误分类、原始工件落地及固定 evaluator；本页不把示意代码当作这些能力已完成。
