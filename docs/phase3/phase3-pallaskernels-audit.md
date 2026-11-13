# Phase 3 PallasKernels 源码审计

2026-11-12。证据类型：**Source audit**。本报告只对本机实际源码、配置、测试和工具作静态审计，不表示已在 TPU 编译、执行或获得性能收益。

## 权威目录与版本

核实目录为 `C:\Users\fengluo\Desktop\training\PallasKernels`。已读取该目录 `AGENTS.md` 及其 `context/programming.md`、`kernel-contracts.md`、`correctness.md`。审计没有改写原仓库，也没有执行 Kernel、安装依赖或读取认证。Git 元数据是 `main`、HEAD `9979d4949ebc673fd7e81555e4af2ebfd1619d20`；未以 Git status 验证工作树是否干净，因此下面的源码字节哈希是本轮来源身份，不用 HEAD 代替未提交文件身份。

[完整来源清单](../../knowledge-site/manifests/phase3-2026-11-12/pallaskernels-source-inventory.json) 保存 390 个源文件/配置/相关审计文件的字节长度与 SHA-256。`direct_audit` 标识本报告重点检查的文件；其余为保留正常 package/registry 导入所需的包树库存，不表示逐个审核了所有其他 Kernel。

| 文件（相对权威仓库） | SHA-256 |
|---|---|
| `pallas_kernels/kernels/mla_fwd.py` | `f9b07f0c62d14d4ecf4b17242b3ee913db4c0039e20786830688c8ec38fff13d` |
| `pallas_kernels/kernels/mla_decode.py` | `e53b0e5ccff3ecf18192f30ef179cdb207c177f2f2dac48b2a08f40acc5000a6` |
| `pallas_kernels/configs/mla_fwd.yaml` | `ea0dc7dea0a374cf11d0548a4b439976cca3c98a39467748432029ecee26084b` |
| `pallas_kernels/configs/mla_decode.yaml` | `f1b000111ea5a16fee58cc3fd5016db2249382ac1ef27a0ee9f81ec703145dfa` |
| `tools/tpu_duration_profile.py` | `eb718d742010fbeccf9c45eb8eaf35300d2d23814be7222a7499cb739b9bf561` |

`pyproject.toml` 声明 Python `>=3.11,<3.12`、JAX `0.8.1`、TensorFlow `2.20.0`、XProf `2.22.1`、NumPy `>=2.0`；TPU extra 为 `jax[tpu]==0.8.1` 与未定版本的 libtpu，测试 extra 有 PyYAML/pytest。远端实际版本仍须从 Runtime 取得，不能由这个文件推断，也不能为符合声明自动升级原环境。FWD 使用 JAX 私有 Mosaic lowering API 和自定义 `mla_v6e_exp2` primitive；兼容性必须通过真实编译确认。

## MLA Forward

正式调用链：`load_kernel_config → get_kernel("mla_fwd", cfg) → MlaFwdKernel.__call__ → mla_fwd → _mla_fwd_impl → _mla_fwd_pallas`。默认 output-only 路径附带 custom VJP；`return_lse=True` 是不经过该 VJP 的 forward diagnostic 路径。本阶段只验证前向，不把 backward/grad 支持混入前向验收。

| 输入/输出 | 实际契约 |
|---|---|
| `q_latent` / `kv_latent` | `[B,S,Rq]` / `[B,S,Rkv]`；序列长度必须相同 |
| `q_pe` / `k_pe` | `[B,H,S,Drope]` / `[B,S,Drope]`；已投影、已旋转的 positional features，由调用者提供 |
| `w_q_nope` / `w_k_nope` / `w_v` | `[H,Rq,Dnope]` / `[H,Rkv,Dnope]` / `[H,Rkv,Dv]` |
| output / optional LSE | `[B,H,S,Dv]`、输入 dtype；`[B,H,S]`、float32 |

三次线性投影得到 Q-NoPE、K-NoPE、V；score 是 `(Q-NoPE @ K-NoPE.T + Q-PE @ K-PE.T) * scale`，使用包含对角线的 causal mask，softmax 后乘 V。默认 scale=`1/sqrt(Dnope+Drope)`。该 API **没有**任意 mask、非 causal 开关、每序列有效长度数组、KV cache 或 RoPE 旋转实现；不能在 Workload 中声称这些参数可配置。七个输入必须使用相同浮点 dtype；源码允许一般 floating，TPU 测试配置和 A09 路径为 BF16，其他 dtype 的真实后端支持尚未证明。

`MlaFwdBlockSizes` 默认 Q/KV128、B1、Rq256、Rkv128；检查所有 tile 为正、KV tile 是 128 的倍数、B/Rq/Rkv 可整除相应 tile。序列轴由 public wrapper 分别补到 Q/KV tile，再裁剪 output/LSE。forward 可接受非整除序列，但 registry trace 的输入规格是补齐后的形状，而 `make_inputs` 仍生成原始形状：**不能直接用现有 registry duration 工具测非整除序列，它会在 shape 校验处拒绝**；边界测试应调用原 public runner，并明确包括 padding/crop 的计时边界。

实现有两条路径：

- 一般路径：单独 KV projection，再在 attention 内投影 Q，online softmax、FP32 累积。
- A09：B tile=1，Q/KV square tile 为 256/512/1024、S 完整整除、BF16 且 `Dnope/Drope/Dv=128/64/128`。先独立 Q projection，再按 H 的整除性选择 group4/2/1 的 fused KV projection，再 attention。使用原生 exp2 primitive、FP32 softmax state、BF16 probabilities；guarded 路径根据 post-BF16 projection 的 norm bound 和 finite V 选择 fixed-shift 或稳定 online fallback。

当前 `mla_fwd.yaml` 实际值是 B1/H16/S4096/Rq1536/Rkv512、128/64/128、BF16；tile Q=KV1024、Rq=Rkv256，`assume_a09_safe: true`。该选项直接走 fixed-shift，不执行 guard 决策及 finite-V fallback。默认生成器把七个随机输入均乘 0.02，但“小随机值”不是已验证的前置条件：若原配置作为基线，需对**相同已投影输入**记录 `_mla_logit_bound` 返回 safe 且 V finite；该前提只允许固定输入/已有保证成立的 Task。一般数据的 Task 应显式 override 为 false，记录不同 config digest，不能把 guard 成本偷偷移到计时外再宣称通用加速。

当前 `docs/kernels/mla_fwd.md` 有多处已落后于源码：称 joint-QKV/两次调用、默认 tile512，以及 public/registry 总会保留 guard；实际源码已独立 Q projection，默认 tile1024 且 assume-safe=true。该文中的 0.465709/0.473003/0.328970 ms 是历史文字报告，软件版本也为 JAX 0.7.2，不是当前配置的 Baseline。本审计保留该差异，没有改写外部文档。

独立 reference 位于 `jax_ops/mla_fwd.py`：纯 JAX CPU einsum 投影（投影后按输入 dtype 舍入）、float32 score、causal mask、`jax.nn.softmax/logsumexp`、BF16 probabilities（BF16 输入时）。`tests/kernels/test_mla_fwd.py` 使用原 runner、同一输入、`run_reference_op` 强制 CPU reference，检查输出/LSE shape、dtype、finite 和 cfg tolerance。默认 `atol=rtol=0.01`，output 与 LSE 均遵守该配置；不因 Candidate 修改容差。单元测试另覆盖 causal、caller-provided q_pe、形状/dtype、guard/nonfinite、大 logits fallback、不同 heads/tiles 和 interpret/frontend；这些是测试源码覆盖，**本轮未运行，也不是 TPU 结果**。

## MLA Decode

同一 registry runner 支持三套不同语义，必须把 `params.interface` 放入 Workload/contract，不能混作同一基线。

| 接口 | 输入、输出与约束 |
|---|---|
| dense（未指定 cache interface） | `mla_decode(q[B,H,L+R], kv[B,N,L+R], lkv_dim=L, seq_len=...)`；BF16 only；静态 Python seq_len 在 `(0,N]`；输出 `[B,H,ceil(L/128)*128]`；不 append、不返回 cache |
| paged | `build_mla_decode_paged → native(q,new,packed_cache,lengths)`；BF16；H 偶数；L 是正 128 倍数；packed cache `[B*N/128,64,2,L+ceil(R/128)*128]`；pages_per_block∈{4,8,16}；cache 被 donation，输出 attention+cache，只有有效 prefix 保证 |
| contiguous（当前默认） | `build_mla_decode_contiguous → native(q,new,cache,lengths)`；BF16，H 正且是16倍数；L/tile 正且是128倍数，capacity N 是 tile 的倍数；`new[B,L+R]`、`cache[B,N,L+ceil(R/128)*128]`、int32 `lengths[B]`；输出 attention `[B,H,L]` 和完整同形 cache；保留输入 cache、单独返回更新结果 |

Dense Q/KV 已含 latent NoPE 与 positional 特征，V 是 KV 的 latent 前缀，没有 Q Rank 投影权重、输出 projection 或内部 RoPE。Dense 对 last dimension padding，buffer length 必须整除 block_size；扫描所有 KV blocks，每块 async DMA 后立即 wait，再 score/softmax。可选 sliding_window、soft_cap；无 LSE 输出，seq_len 变更可引发重新编译。

当前默认 contiguous 配置为 B8/H16/N16384/L512/R64、BF16、block1024、scale `0.07216878364870323`、window0、cap0、vmem_limit_bytes120795955、单设备。lengths 包含本次追加 token，语义为 clip 到 `[0,N]`；正长度替换 `cache[b,clip(length)-1,:L+R]`；非正长度输出零且 cache 不变。所有其他位置、padding 和未使用 tail 必须完整保留。capacity 是 buffer 容量，不能用有效 length 缩小 buffer 逃避必需复制工作。

Contiguous 的调用链是 `MlaDecodeKernel.__call__ → native_function → build_mla_decode_contiguous`，两个 Mosaic call：prepare pads Q、广播 new/lengths；attention 每 batch 一个 program 扫描所有 capacity blocks，load、append、store 到新 cache、mask 无效 token/padding、FP32 online softmax，最后输出。没有 page table/scalar-prefetch metadata；DMA 是 HBM↔VMEM。其 source 没有真正重叠 load/wait 或双 buffer，源码次序只构成潜在研究点，**未证明当前性能瓶颈或收益**。默认 cache 160 MiB，整块读写是契约的一部分。

Paged 路径另外包括 metadata、prepare、batched/single attention、restore 共五个 calls；七个 scalar-prefetch 元数据。由于 donation，不能把原 `benchmarks.measure`/capture 的固定输入重复调用直接用于 paged。默认 contiguous 不 donation，可重复相同 frozen inputs，适合第一轮可信接入。

独立 cache oracle 是 `mla_decode.py::mla_decode_paged_reference`，由 runner 的 `reference_functions()[0]` 取得：它用独立 NumPy FP64 host matmul/softmax，先复制 cache，按 clipped lengths 追加，最后 cast BF16；通过 `jax.pure_callback` 提供 CPU/JAX shape tracing。与 Kernel 放在同一文件不影响其独立算法，但 **Candidate 不能修改这个 reference**：基线时冻结此函数源码/文件，评估时从固定 oracle bundle 加载，不能从 Candidate 修改后的同文件静默取出 oracle。

`tests/kernels/test_mla_decode.py` 对默认 contiguous 进行 attention allclose、shape/dtype/finite 和**完整 cache exact equality**；paged 仅比较有效 prefix。CPU 单元测试另覆盖 lengths `0,1,129`、`-3,128,256`、`127,255,300`，window/cap，NaN tail/padding 不污染输出，重复调用及输入 cache 不变。全 cache 的 BF16 位模式比较也有现成用法。默认 attention tolerance 为 atol=rtol=0.01；FP32 不是该 Kernel 支持的替代 dtype，dtype 负例应拒绝。

## 复用与集成决定

首先推荐 **MLA Decode contiguous**：默认配置/独立高精度 oracle/完整 cache 契约/不可变输入/两阶段原 profiler 链完整；先跑 vcs 小配置或同接口 B1/H16/N1024，再扩展默认 B8/H16/N16384。选择它不代表 Forward 缺失价值；Forward 必须另建 Task，先解决 assume-safe 与文档/源码不一致。

最小逻辑依赖是：Kernel、对应 registry、config loader/KernelSpec、runner base、原 CPU oracle/CPU placement helper、原 benchmark `profiler.py`/`metrics.py`，以及原 duration capture/parser/runtime。实际 package 导入并不只加载这几份：`registry/lookup.py` eager imports 全部 runner，`jax_ops/__init__.py` eager imports 多个 reference。隔离 Candidate 应同步已哈希的 `pallas_kernels/` 与 `jax_ops/` 包树、`benchmarks/` 三文件和 `tools/` 三文件，保留正常 `get_kernel` 路径，不复制认证、`.git`、缓存、未知 notebook 状态或全部 project2。基线 frozen oracle 与可修改 Candidate 分开验证身份，但不是第二套 oracle 算法。

远端必须记录实际 `module.__file__`、每个已加载本地 package 文件 digest、入口 qualname、配置内容及 digest、specialization 的 input shapes/dtypes/static kwargs、编译后的 HLO/可执行设备。仅证明上传 zip 或一个 mla 文件 hash 不够。不能把 installed package 版本字符串当作当前源身份。

基线 Workload 与验证步骤见 [Workload / correctness 契约](phase3-workload-and-correctness.md)；Profile 工具能力与限制见 [Profiler 能力审计](phase3-profiler-capability-audit.md)。P3.2/P3.3 需要实际远端证据才能通过，本静态审计未通过这些 Gate。
