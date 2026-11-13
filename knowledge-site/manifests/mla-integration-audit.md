# MLA Decode / Forward 集成审计

审计日期：2026-11-11（Asia/Shanghai）。状态：**静态只读审计，未执行 Kernel、测试、JAX 导入或 TPU 作业**。本文件及 [源码清单](mla-source-manifest.json) 是获批 Phase 1 文档产物，不是 Phase 2 实现或实测报告。Colab 连接和路径传输契约由本轮独立的 Colab 审计提供；本文不创建连接系统。

## 1. 基线与允许范围

源仓库：`C:\Users\fengluo\Desktop\training\PallasKernels`。下文代码位置均相对于此目录，行号对应本轮内容哈希。

已读取 `AGENTS.md`、`context/programming.md`、`context/kernel-contracts.md`、`context/correctness.md`。仓库要求保留 config-bound registry 路径、串行运行 TPU、CPU 测试不得初始化 TPU、不以 interpret 模式证明 TPU 性能、不擅自提交。用户本轮要求优先：源仓库全部只读，后续候选仅允许两份实现文件变化，其他核、测试、reference、registry、容差、配置和环境文件保持原样。

Git 元数据：`HEAD -> refs/heads/main`，commit `9979d4949ebc673fd7e81555e4af2ebfd1619d20`；索引版本 2，688 项。当前审计环境没有找到可执行的 Git，因此 `git status`、HEAD 与工作区完整差异未验证。commit 不是当前工作树字节一致的证明，后续基线必须同时固定工作区哈希；不能通过 reset/checkout 清掉已有修改。

### 精确候选修改 allowlist

| 任务 | 唯一可优化实现文件 | 当前 SHA-256 |
| --- | --- | --- |
| MLA Decode | `C:\Users\fengluo\Desktop\training\PallasKernels\pallas_kernels\kernels\mla_decode.py` | `e53b0e5ccff3ecf18192f30ef179cdb207c177f2f2dac48b2a08f40acc5000a6` |
| MLA Forward / Fwd | `C:\Users\fengluo\Desktop\training\PallasKernels\pallas_kernels\kernels\mla_fwd.py` | `f9b07f0c62d14d4ecf4b17242b3ee913db4c0039e20786830688c8ec38fff13d` |

这只是未来候选的范围，不授权现在优化，也不授权写回原仓库。隔离候选包相对冻结基线只可有这两条路径的实现差异；拒绝新增、删除、改名和路径／链接逃逸。实验控制器及结果文件应位于包外，不以生成文件混入候选包。

**文件 allowlist 还需要文件内保护。** `mla_decode.py:2768–2813` 的 `mla_decode_paged_reference` 是当前 contiguous/paged 模式的独立 FP64 host oracle，虽然位于可优化文件中，仍不得修改。该源码段的原始字节 SHA-256 为 `2e6f0dbc4c50196fecdcd7307cf6559c8573ce2d3deeef8efd65573a658875c8`。不能改变其依赖绑定、参考选择或计时／容差来让候选通过。仅锁函数文本不足以隔离可修改模块的全局副作用；后续可信评测应在独立进程从冻结基线执行 reference／保存其输出，而不是信任候选模块提供的 oracle。现有测试仍须原样运行。

`mla_fwd.py` 的 custom VJP 在反向求导时调用 `mla_bwd`（1104–1146 行）。MLA Backward 不在可运行任务范围内；不能运行完整含梯度测试的 Fwd unit 文件或调用 `grad/vjp/value_and_grad`。既有反向契约、reference 和其他核保持不变。

## 2. 实际调用入口与工作负载

统一入口是 `load_kernel_config(name)` → `get_kernel(task, cfg)` → `runner.make_inputs(jax.random.key(cfg.seed))` → config-bound runner，见 `utils/config.py:117`、`registry/lookup.py:134`。`PALLAS_KERNELS_CONFIG_DIR` 可影响默认配置来源，后续必须记录并核验实际解析路径与哈希，不能把环境覆盖当作同一 workload。

### MLA Decode

- registry：`pallas_kernels/kernels/registry/mla_decode.py:15` 的 `MlaDecodeKernel`，调用签名 `__call__(q, kv, cache=None, lengths=None, *, lkv_dim=None, seq_len=None, sm_scale=None, sliding_window=None, soft_cap=None, mask_value=None, block_size=None, interpret_mode=None)`。
- 当前 `mla_decode.yaml` 的 `params.interface` 是 **`contiguous`**。runner 的 `native_function()`（149 行起）选择 `build_mla_decode_contiguous`，不是文件开头旧注释所写的 paged。
- 实现 builder：`mla_decode.py:2858`，`build_mla_decode_contiguous(*, lkv_dim=512, sm_scale=0.07216878364870323, block_size=1024, vmem_limit_bytes=120795955, sliding_window=0, soft_cap=0.0, interpret_mode=False)` → jitted `native(q,new,cache,lengths)`。
- 公共 dense 入口仍存在于 `mla_decode.py:190`：`mla_decode(q,kv,*,lkv_dim,seq_len=None,sm_scale=1.0,sliding_window=None,soft_cap=None,mask_value=None,block_size=16,interpret_mode=False)`。它是不同 ABI，不能与当前四输入缓存 workload 混用。
- paged builder 在 2817 行，`pages_per_block` 只允许 4/8/16。其 native 在 494 行使用 `donate_argnums=(2,)`，必须接回新 cache 或重新准备输入；不能将它传给重复复用相同输入的现有测量工具。本次建议基线只使用现有默认 contiguous 配置。

| 默认配置项 | `pallas_kernels/configs/mla_decode.yaml` 实际值 |
| --- | --- |
| shape | B=8，H=16，latent=512，rope=64，capacity=16384 |
| 输入 | q BF16 `[8,16,576]`；new_kv BF16 `[8,576]`；cache BF16 `[8,16384,640]`；lengths int32 `[8]` |
| 输出 | attention BF16 `[8,16,512]`，updated_cache BF16 `[8,16384,640]` |
| tiling / device | block_size=1024；num_devices=1；vmem_limit_bytes=120795955 |
| 协议参数 | seed=0；atol=rtol=0.01；sm_scale=0.07216878364870323；window=0；cap=0；interpret=false |

`make_inputs()`（registry 237 行起）用固定 seed 生成输入，将缓存 padding 及末 token 清零，lengths 全为 capacity。contiguous native 不 donate，返回独立更新缓存；每次扫描全部 cache block 并保留完整尾部（实现 2986、3003 行），计时范围必须包含两个输出及 cache 更新。lengths 包含本次新 token，非正长度输出零且缓存保持；大于容量按实现裁剪语义处理。后续 ragged/空长度检查必须复用受保护测试契约，不能只测全长后声称覆盖全部输入域。

`mla_decode_vcs.yaml` 已存在：B1/H16/L128/R64/N128、block128、相同 BF16/容差/contiguous。它是独立小配置，不能替代全尺寸结果或混为同一比较上下文。

### MLA Forward

- registry：`pallas_kernels/kernels/registry/mla_fwd.py:17` 的 `MlaFwdKernel`，接收七个张量，keyword-only `sm_scale/bwd_block_sizes/return_lse/debug/interpret_mode`；从固定 config 绑定 tiling 和 `assume_a09_safe`。
- 实现：`mla_fwd.py:1149`，`mla_fwd(q_latent,kv_latent,q_pe,k_pe,w_q_nope,w_k_nope,w_v,*,sm_scale=None,block_sizes=None,bwd_block_sizes=None,return_lse=False,debug=False,interpret_mode=False,assume_a09_safe=False)`。
- Q/K 位置输入已完成旋转；这个 Kernel 不负责 RoPE。输出 `[B,H,S,Dv]`，`return_lse=True` 额外返回 FP32 `[B,H,S]`，是独立诊断输出契约。
- wrapper 校验形状、dtype 和整除条件；按 tile padding 序列轴，再裁回原长度（937–1017 行）。当前默认完整 tile 不需要额外 padding，但不能据此把所有 future workload 的 wrapper 开销抹去。

| 默认配置项 | `pallas_kernels/configs/mla_fwd.yaml` 实际值 |
| --- | --- |
| shape | B1/H16/S4096/Rq1536/Rkv512/Dnope128/Drope64/Dv128 |
| 输入 | q_latent `[1,4096,1536]`；kv_latent `[1,4096,512]`；q_pe `[1,16,4096,64]`；k_pe `[1,4096,64]`；Wq `[16,1536,128]`；Wk/Wv 各 `[16,512,128]`，均 BF16 |
| 输出 | BF16 `[1,16,4096,128]`；可选 LSE FP32 `[1,16,4096]` |
| tiling | block_b=1，block_q=block_kv=1024，block_rq=block_rkv=256 |
| 固定参数 | seed=0；num_devices=1；atol=rtol=0.01；`assume_a09_safe=true` |

`make_inputs()`（registry 79 行起）使用 7 个 split keys，以 BF16 `0.02` 缩放随机输入。默认 scale 是 `1/sqrt(128+64)`，来自受保护 reference 的 `default_scale`（`jax_ops/mla_fwd.py:15`）。Fwd 没有输入 donation；full-pytree 同步仍不可省略。

**固定 A09 的域前提需要独立验收。** 实现 1172–1177 行明确 `assume_a09_safe=True` 不执行运行时 guard/fallback。518–531 行的 guard 对 post-BF16 Q/K projection 构造有浮点余量的范数上界，要求每个 query 的 bound 有限、非负且 `2*bound <= 80`；projected V 也必须全部有限。当前 TPU correctness 测试只检查 output/LSE 与 reference，并不单独断言这个域前提。不能改 YAML 为 false、缩小输入、放宽容差或只以候选自己的 guard 自证来合法化基线。Phase 2 在固定输入上先用冻结基线及其固定投影舍入路径核验这些前提，保留证据；若不满足，记录基线不可用并请求协议决策。

这里的 bound 是自然指数域的原始 logit 上界；`_mla_logit_bound` 实际返回 `(safe, bound * log2(e))`，第二项是供 exp2 使用的 shift，不是原始 bound，不能再对它误套 `2*x <= 80`。Projected V 的有限性检查独立于该返回值，见实现931行。

`mla_fwd_vcs.yaml` 已存在：B1/H4/S256/Rq256/Rkv128，Q/KV256、Rq256/Rkv128、相同维度128/64/128与 `assume_a09_safe=true`。它是独立小配置。

## 3. 受保护依赖、参考与测试

完整文件哈希、绝对路径和静态本地 import 图在 [mla-source-manifest.json](mla-source-manifest.json)。其中记录 597 个源／配置／测试／指导文件，静态解析 453 个 Python 文件无语法错误；未导入或执行它们。保守 import 闭包含190个本地模块，包含 package initializer、延迟分支和 TYPE_CHECKING，因此表示潜在依赖，不代表授权运行其他 Kernel。

关键受保护路径：

| 类别 | 文件与用途 |
| --- | --- |
| Decode reference | `jax_ops/mla_decode.py`（dense reference、padding/shape helpers）；实现文件内 `mla_decode_paged_reference`（contiguous/paged FP64 host reference） |
| Forward reference | `jax_ops/mla_fwd.py`（projection、attention、full reference、scale）；`jax_ops/utils.py`（CPU placement、每个叶子同步、reference快照）；`jax_ops/__init__.py` |
| 注册与配置 | 两个 `pallas_kernels/kernels/registry/mla_*.py`、`registry/base.py`、`registry/lookup.py`、`registry/__init__.py`、`kernels/__init__.py`、`config_loader.py`、`utils/config.py`、四份 MLA YAML |
| 共享实现依赖 | `kernels/gqa_common.py`、`utils/interpret.py`、`utils/__init__.py`；Fwd 的 `mla_bwd.py` 反向依赖保持只读且不执行 |
| TPU correctness | `tests/kernels/test_mla_decode.py`、`tests/kernels/test_mla_fwd.py`、`tests/conftest.py`、`tests/nightly_tpu.py` |
| CPU契约 | `tests/unit/test_mla_decode_reference.py`、`test_mla_fwd.py`、`test_registry.py`、`test_registry_dump_contract.py`；Fwd整个unit文件含反向测试，不可整文件执行 |
| host timing | `benchmarks/profiler.py`、`runner.py`、`metrics.py` |
| device capture/parse | `tools/get_tpu_duration_info.py`、`tpu_duration_runtime.py`、`tpu_duration_profile.py` 及对应unit测试 |
| IR/snapshot | `kernels/utils/wrapped_pallas.py`、`registry/base.py`、`scripts/test_all.py`、`scripts/run_test_subproc.py`、相关 dump 工具 |
| 环境 | `pyproject.toml`、所有环境／打包配置、CI配置；不得为适配当前机器而修改 |

包根 `jax_ops/__init__.py` 和 registry eager imports 会载入其他任务定义；正常定义导入不等于执行其他 Kernel。不得据此绕开 registry 或修改包注册。后续执行必须以任务与 callable allowlist 限制实际求值，不运行整仓测试发现后默认全部执行。

## 4. 现有 correctness 契约与精确选择器

所有命令仅是**审批后的执行计划，本轮没有运行**。Linux/Colab命令在隔离基线根运行，`RUN_DIR` 指向包外唯一结果目录；Python和依赖必须使用已经核验且获批的远端环境。`COVERAGE_FILE` 将既有pytest coverage输出放在结果目录，`-p no:cacheprovider` 和 `-B` 避免污染候选包；没有改测试规则或容差。

```bash
PALLAS_KERNELS_TEST_CONFIG=mla_decode COVERAGE_FILE="$RUN_DIR/coverage-decode" \
python -B -m pytest -q -p no:cacheprovider -m tpu --tb=short \
  tests/kernels/test_mla_decode.py::TestMlaDecodeCorrectness::test_mla_decode_correctness_bfloat16 \
  --junitxml="$RUN_DIR/correctness-decode.xml"

PALLAS_KERNELS_TEST_CONFIG=mla_fwd COVERAGE_FILE="$RUN_DIR/coverage-fwd" \
python -B -m pytest -q -p no:cacheprovider -m tpu --tb=short \
  tests/kernels/test_mla_fwd.py::TestMlaFwdCorrectness::test_mla_fwd_correctness_bfloat16 \
  tests/kernels/test_mla_fwd.py::TestMlaFwdCorrectness::test_mla_fwd_lse_bfloat16 \
  --junitxml="$RUN_DIR/correctness-fwd.xml"
```

仅在 TPU 未被 notebook 常驻进程占用时采用独立 pytest subprocess。若 Colab 当前 kernel 已持有 TPU，复用现有CLI在同一 notebook Python进程里通过 unittest loader逐项执行上述完全限定测试，不再并发开第二个JAX进程，不删除锁、不重置session；执行器须记录测试数、失败、错误和skip，不能把 notebook transport成功当成测试成功。

同进程复用必须先核验**已加载代码**：模块`__file__`/包根、加载时源码摘要或可核验的代码对象指纹、registry绑定的callable、测试模块实际配置及解析路径必须对应本次冻结快照。两个测试模块在导入时绑定配置（各自20行），capture工具只是插入sys.path后普通import；切换cwd/sys.path不会清除sys.modules或已编译函数。仅核对当前磁盘哈希不足以证明旧代码对象已更新。来源错配或无法证明加载身份时阻止执行，保存证据，不自动reload、删除模块或restart session。

Decode测试（37–96行）先完成CPU reference，再调用runner；检查输出shape/dtype/finite/allclose。contiguous更新缓存必须全量精确相等；paged只检查有效prefix。Forward测试（35–97行）覆盖 output-only 和 LSE：shape、BF16 output、FP32 LSE、finite，以及固定 `cfg.get_tolerance()` 比较。两者默认都只有seed0配置输入，不是全输入域证明。

验收需要 Decode **1项**、Fwd **2项**实际执行成功且无skip。`tests/conftest.py` 与测试 `setUpClass` 无TPU时会skip；pytest退出0或JUnit文件存在不等于TPU通过。保留完整失败／skip输出。更广ragged、guard与CPU契约测试需事先枚举，仅运行这两个任务的相关forward行为；不能直接运行 `pytest tests` 或完整 `tests/unit/test_mla_fwd.py`。

## 5. 已有计时、Profile、版本和IR工具

### 可复用的设备证据链

`tools/get_tpu_duration_info.py` 已实现真实采集，**无需另建 Collector**：

```python
run_config(config: str, *, warmup: int = 5, calls: int = 20,
           out_dir: str | Path = "out/tpu_duration_info") -> dict[str, Any]
```

CLI只有四项：`--config`必填，`--warmup`默认5，`--calls`默认20，`--out-dir`默认`out/tpu_duration_info`。calls/warmup必须为正。该通用工具能运行其他核，RSI调用端必须只允许 `mla_decode`、`mla_fwd` 及事先批准的对应YAML，不传用户自由路径。

实际流程：读取固定config → TPU/单进程/设备数检查 → registry `pallas_trace_args()` 与 `make_inputs()` → `tpu_duration_runtime.prepare_entry()`按spec编译、验证实际device集合、放置输入 → untimed warmup → `jax.profiler.start_trace` → 带step annotation的逐次执行及 **`jax.block_until_ready`整个pytree** → `finally: stop_trace` → offline XPlane解析。当前两份默认配置非donating；若将来选paged，重复输入生命周期不适配，必须先解决，不能直接套用工具。

测量范围是**registry导出的内部编译入口**，明确排除public wrapper；Forward为 `_mla_fwd_pallas`，Decode为config-bound native。`return_lse=True`不在默认trace契约中。不要把这些数值称为public API端到端延迟。

返回schema_version=2；记录config/shape/tiling/params/dtype/seed/devices、calls/warmup、`jax/jaxlib/libtpu/xprof` distribution版本、Git commit/dirty、`XLA_FLAGS`/`LIBTPU_INIT_ARGS`及entries。工具不执行correctness、不验证A09域前提、不做晋升确认，也没有Candidate/Run绑定，外层可信运行记录必须补足这些关联。

`get_tpu_duration_info.py:111,114` 无条件调用`git`。批准执行前检查远端Git可执行文件可用性；包快照没有.git时元数据可为空，但缺少git程序会在写capture.json前抛FileNotFoundError。缺失时记录前置阻塞和外层日志，不安装Git或修改受保护采集器绕过；工作树哈希仍由受信外层绑定。

每次新建 `duration_<随机后缀>`，保存：

```text
<run-artifacts>/duration_<suffix>/
  capture.json
  duration.json
  entry_0/
    duration.json
    plugins/profile/.../*.xplane.pb
```

异常发生时已写入资料保留，完整性须单独检查；准备输入／设备检查可能在建立capture目录之前失败，所以CLI stdout/stderr和请求元数据必须先由控制侧保存。CLI成功返回0，捕获的ValueError/RuntimeError/ImportError/KeyError或parser errors返回1，argparse错误返回2；其他未捕获异常也不能当成功。

`tools/tpu_duration_profile.py:83` 的 `read_profile(directory, *, calls, device_ids, program_name)` 可直接对**已保存**目录离线解析，虽然没有独立命令行。它要求单一XPlane文件、指定TPU轨道，按目标module/program_id绑定custom-call，仅使用有效 `device_duration_ps`；按ps保留原始样本，除以1e6换成µs。不以host或普通envelope字段补零／回退，不相加program与其子kernel。没有目标device/custom-call样本、错误数量等会报错；无可验证跨卡时间原点时span为unavailable。应优先复用这个Parser，先确认目标环境的设备轨道与版本兼容。

文档 `docs/tpu_duration_info.md` 记载过2026-11-04在v5e对matmul/GQA的历史验证；它不证明本轮MLA已运行、目标为v6e，或新环境兼容。本轮不复现其他任务。

### 其他工具的边界

- `benchmarks.profiler.warmup/measure`（15/22行）使用 `time.perf_counter` 和全pytree同步，输出**同步host wall-clock ms**；可用于独立端到端补充口径，不冒充设备时间。`BenchmarkRunner`保存raw `times_ms`，但缺可靠比较、oracle、环境和candidate身份；不得自动晋升。
- `kernels/utils/profiling.py:15` 的 `sync_tpu()` 是空函数，因此 `tpu_timer` 不能作为正式TPU延迟裁判。
- `registry/base.py:218` 的 `dump_artifacts(out_dir,...)` 可生成CPU参考golden及TPU lowering的`*_before_opt.hlo`；这是编译／参考证据，不是设备实测。snapshot monkeypatch只可放在独立诊断run，不能混入正式计时。
- **不要直接使用 `scripts/test_all.py` 作为基线执行入口。** 它在182、320、332行调用会删除`/tmp/libtpu_lockfile`的cleanup，在锁错误后重试，subprocess没有timeout，并且总脚本不以聚合失败显式返回非零。该行为不符合本任务不得扰动会话／锁的边界。保留源码不改，复用精确测试入口及安全的既有capture工具。

### 版本发现计划

包声明（`pyproject.toml`）：Python `>=3.11,<3.12`；`numpy>=2.0`；`jax==0.8.1`；`tensorflow==2.20.0`；`xprof==2.22.1`；TPU extra `jax[tpu]==0.8.1` 与未固定版本的`libtpu`。jaxlib没有独立显式版本钉住。tests extra含pytest>=8.4.2、pytest-cov>=5.3.0、pytest-xdist>=3.6、pyyaml。网站的Python3.13环境不能视作该包合规运行环境。

下一阶段先经既有Colab CLI执行不导入JAX的metadata发现：`sys.version`、`sys.executable`、`importlib.metadata.version`读取上述包及jaxlib/libtpu版本，缺失记null，不安装。随后在获准TPU进程中记录 `jax.__version__`、`jax.devices()`的platform/device_kind/device id/process_index、`jax.process_count()`以及指定的编译标志。不得输出整份env或认证配置。若与包约束不符，先报告兼容性差异；不自动升级／降级远端runtime。现有capture会再次记录核心四包版本，但它会执行TPU，不能用来替代本轮只读发现。

## 6. 最小 unchanged-baseline 计划

1. 固定当前完整源工作树、两个实现和所有受保护文件的哈希，记录未提交差异；在获批的隔离目录准备字节相同副本。当前清单不是自动上传许可，源资料发送到远端须限获批Colab及任务范围。
2. 用既有CLI确认所选运行时／当前Python进程、版本、设备及是否已有TPU持有者。不重置session、不删除锁、不安装依赖。包外创建两个独立Run目录；控制侧先记录请求和日志位置。
3. 冻结完整YAML和oracle，读取真实shape/dtype/layout/state约定。Default Fwd先完成上述post-BF16 guard/finite-V前提核验；不满足时记录基线失败／协议阻塞，不修改参数。Default Decode保持contiguous与完整cache输出。
4. 串行执行上节的1项Decode和2项Fwd correctness。检查实际执行数、无skip、固定容差、cache精确比较、LSE结果。正确性失败仍保存全部证据。
5. 正确性和域前提成立后，分别在同一已占用TPU进程中调用既有函数：`run_config("mla_decode", warmup=5, calls=20, out_dir=<decode_run_dir>)`、`run_config("mla_fwd", warmup=5, calls=20, out_dir=<fwd_run_dir>)`。若选择独立process且无持有者，等价CLI为 `python -B tools/get_tpu_duration_info.py --config mla_decode --warmup 5 --calls 20 --out-dir <decode_run_dir>`，Forward替换对应名字即可。只运行这两个任务，严格串行。
6. 保留capture、全部PB、每个entry和root JSON、stdout/stderr、退出码、原始样本和成本。检查target event、device映射、样本数、warnings/errors和输入shape；不只看mean，不把missing当0。不因解释未知而丢弃有效结果。
7. 经现有Colab文件下载能力把明确Run目录打包取回，校验远端／本地大小与SHA-256，保留原始路径和相对目录。先保存artifact目录清单；防路径逃逸，下载只包含批准Run目录而非用户home或认证状态。失败或下载不全标记incomplete并可继续取回，不重跑已完成TPU作业来代替取回。
8. 离线用现有parser重算并核对逐样本；两任务分别形成基线，不比较不同workload的赢家。5次warmup/20次capture是现有工具默认实验提案，不是已批准的统计晋升阈值；确认／复测规则仍由Benchmark在Phase2协议中固定。

本轮没有建立候选、执行上述命令、测量延迟或记录任何TPU通过结论。后续多个strategy可各自形成多个Plan/Candidate；固定裁判、队列与记录层必须保留全部失败、回退、回归和deferred，不能在实验前强制选唯一方法。

## 7. 剩余风险与静态验证结果

| 项目 | 当前状态 |
| --- | --- |
| 源码身份 | 两个确切文件及597项manifest已定位；Git工作树状态尚未通过git验证 |
| 文档漂移 | Decode顶部旧注释写paged；Forward docs仍写tile512和guard，而当前YAML为1024/fixed A09。原仓库只读，本轮仅记录差异 |
| oracle隔离 | Decode oracle嵌入可优化文件；必须保护源码段并使用可信基线reference进程，不能仅靠文件allowlist |
| Fwd输入域 | 默认fixed A09前提未由TPU correctness测试显式检查；必须在基线计划中补可信前提证据，原测试不改 |
| benchmark覆盖 | 现有默认seed0不是ragged、非有限或全部工作负载覆盖；已存在边界测试但不能把静态存在写成通过 |
| 设备采集 | 真实Collector/Parser存在；本轮仅读源码，MLA兼容性、事件完整性及实际TPU版本未验证 |
| 运行安全 | 不使用会清理TPU锁的test_all，不并行争用notebook TPU，不执行Fwd反向 |
| 检查 | 453份Python仅AST解析通过；未导入包、未运行测试、未执行TPU；原源仓库未修改 |

源码清单中的静态import分析保守包含不可运行的其他任务依赖；它不替代未来执行审计。Python包、YAML、reference、测试、benchmark、parser和配置全都保持受保护，不因发现上述问题而扩大两文件优化范围。
