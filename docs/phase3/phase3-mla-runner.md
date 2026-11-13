# 固定 MLA Baseline / Runner 接口

2026-11-12。此页记录本地实现和验证边界。真实 Gate 状态以本轮最终报告与回执为准；本页的 CPU 测试不证明真实 TPU Kernel 正确或更快。

## 准备基线

`workflow.mla_baseline.prepare_sources(store, source_root=SOURCE_ROOT)` 只向原 ArtifactStore 保存原始源码、完整 package 依赖、配置、benchmark/capture/parser 和 Source audit 清单，不创建 Run。默认目录精确为 `C:\Users\fengluo\Desktop\training\PallasKernels`；显式 source_root 用于隔离 fixture 或经审计的副本。只纳入 `pallas_kernels/`、`jax_ops/` 的 `.py`、bundled configs 的 `.yaml` 和六份指定 benchmark/tools 文件，不复制 `.git`、`.env`、`.auth`、缓存或整个 project2。

`create_baseline(store, environment, *, source_root=SOURCE_ROOT, task_id='mla-decode-v1', benchmark_case_id='decode-small', cases=None, key='baseline:1')` 返回已有 execution API 可接受的 job。environment 必须是实际 Runtime 探针结果：完整 Python/JAX/JAXlib/libtpu、TPU 型号/设备列表/runtime ID，当前目标接受实际报告的 `TPU v6 lite` 或 `TPU v6e`，不能仅传一个猜测的硬件名字。该函数本身不证明传入值是真实探针；worker 在每个真实阶段重新读取并要求和 context 完全相同。

它追加原 Task/Workload/Context/Strategy/Plan/ParentCandidate/Candidate/Run，绑定原 MemoryWriter Plan 父选择。Plan 明确是“验证未修改基线”，不是 Analyzer 已产生的优化提议。导入 ParentCandidate 的 candidate_id=null，来源身份来自原源码；新 Run 仍 not_run，Best/Frontier 不更新。重复同调用幂等，显式新 key 保留 Candidate 并创建新 Run；不同 benchmark_case_id 固定不同 context。后续执行与终态/晋升必须通过原 backend/Evaluator/consider。

默认 correctness matrix 是 `decode-small` B1/H16/N1024/L512/R64/block1024、`decode-boundary` B3/H16/N256/L128/R64/block128（lengths=0/1/129，opaque tail/padding NaN）、`decode-default` B8/H16/N16384/L512/R64/block1024。默认正式计时为 small；全部 correctness cases 仍必须通过，不能因默认大配置失败就隐藏该 case。矩阵固定输出为 attention allclose、完整 cache exact。

## Candidate 配置与固定来源

config_ref 是 canonical JSON：

```json
{"schema_version":1,"kernel":"mla_decode","case_overrides":{}}
```

Candidate 可在已有 case_id 下调整 `tiling.block_size`（正128倍数）及 `params.vmem_limit_bytes`（正整数）。Shape、dtype、scale、window、cap、interface、interpret、reference/tolerance 属于固定 Workload，不能从 Candidate config 改动。源码修改仍必须作为新 Candidate source bundle，原源码目录不改写。

Candidate 与 Oracle 分别有 bundle ArtifactRef；入口分别是同路径 `pallas_kernels/kernels/mla_decode.py` 中的 `build_mla_decode_contiguous` 和 `mla_decode_paged_reference`。Oracle 固定完整基线包。trusted_files 固定所有非入口源文件、YAML、registry/config loader、benchmark、parser。即便 Candidate 修改了与 oracle 共存的源码文件，也不会影响独立 CPU Oracle 的那份固定包。

## Worker 调用

```python
run_mla_stage(request, workspace, oracle_workspace, output_dir,
              source_bundle=source_bundle, oracle_bundle=oracle_bundle,
              config_path=config_path, runtime_session_id=runtime_id,
              lease=lease, environment=None)
```

由 durable worker 的新进程调用；不能在已经导入 Kernel 的 notebook 中复用。入口前验证全部文件字节和 config digest；实际 module.__file__、入口函数 inspect 源码路径及被导入的其他 package/helper 必须在 Candidate bundle 内。系统 stdlib/site-packages、平台标准扩展目录属于运行时依赖；目录名伪装、外部旧 helper 或用别名导入同源路径均拒绝。Debian 系统 sitecustomize 只有当前 Python 的 `/etc/pythonX.Y/sitecustomize.py`、root所有、非symlink、无group/world写时允许；不放宽任意 sys.path/PYTHONPATH。

`collect_environment(runtime_session_id)` 实际读取 TPU devices、版本、XLA backend platform version、coords（可取得时）；缺失可选依赖版本用 null。正式 stage 必须与固定 environment 完全一致。当前 JAX 进程单设备执行，由原 registry 生成 public runner，确认 compiled runtime executable 实际使用指定设备。

Oracle 使用额外 `-I`、`JAX_PLATFORMS=cpu` 子进程加载冻结包。该进程通过原 make_inputs 生成输入并完成原 CPU reference，保存 NPZ；Candidate 加载**同一份字节**，不能通过改 make_inputs 缩小/弱化测试。BF16 存 uint16 和 shape/dtype/raw digest metadata，加载关闭 pickle。

compile/correctness 遍历完整矩阵；compile 记录独立耗时/HLO/specialization；correctness 保存 inputs、Oracle outputs、Candidate outputs、input manifest，attention 按原 allclose方向和0.01/0.01容差，cache按原始字节比较。opaque cache允许NaN时numeric error用null与原因；attention非有限拒绝。大 case 的 device buffers 在 case结束后释放，不把整个矩阵缓存到最后。

benchmark 先独立compile，再调用冻结原 `benchmarks.profiler.warmup/measure`；原 measure 对完整 output pytree 使用 jax.block_until_ready。compile不进样本，原samples全保存、median晋升输入，补mean/std/min/max/P25/P75。计时是 host synchronized public call，包含prepare/完整cache输出，不叫纯设备时间。

profile 独立operation调用冻结 `get_tpu_duration_info._capture_entry` 与 `read_profile`，使用实际ProfileOptions默认设置，明确尚未验证其他TRACE mode；原始PB及parser JSON保留。Profile和Benchmark按冻结契约归属同一逻辑Run，operation/采集时间边界分开。worker 将 raw_artifact_paths/raw_xplane_paths/report_path 转原ArtifactRefs，再交固定Evaluator。

## 本地验证范围

`tests/test_phase3_runner.py` 的 CPU 用例验证数值误差、已知错误attention/cache、非有限/shape/dtype拒绝、原样本统计、compile边界、多输出接口、源码/依赖/alias入口身份、可信工具、config语义锁和标准运行时路径。`tests/test_phase3_baseline.py` 验证明确文件库存、敏感文件排除、无environment拒绝、原Store准备幂等、sameCandidate/newRun、不同context及未自动晋升。真实worker CPU子进程集成另由 `test_phase3_worker.py` 覆盖。

已在没有JAX/NumPy的本地CPython环境执行这些软件边界测试；WSL现有stdlib Python还验证Linux路径和worker进程锁。没有用它们宣称NPZ/JAX分支或真实TPU负例通过。真实矩阵、oracle负例、timing和Profile需要后续实际Runtime回执及原始工件。
