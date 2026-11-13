# Phase 3 真实执行接口指南

<!-- report-status-20261113 -->
> 2026-11-13状态索引：真实Oracle负例、基线、Profile和首轮5候选均已完成；第二轮和稳定收益未确认，当前无Runtime。下文接口说明中的“当前/尚未”及测试数量记录其原撰写时点；最新总体结论见 [Phase 3进展报告](../../knowledge-site/manifests/phase3-2026-11-12/implementation-report.md)。

2026-11-12。本文描述当前实现、固定协议和已取得的验证证据，不是 Phase 3 完成报告。真实 Colab/TPU 最小运算已返回；当前权威 Decode 的首次多 case 编译在 `decode-boundary` 失败，原始结果见 [baseline-result.json](../../.artifacts/phase3-2026-11-12/baseline-result.json)。独立兼容修复 Candidate 随后通过三组真实正确性，完成 small 正式计时和 XPlane 回收，见 [基线报告](phase3-baseline-report.md)。原 Runtime 已丢失，正在按用户授权重新申请 v6e；真实Oracle负例、正式优化与两轮RSI仍待验收。

## 目录和数据归属

| 位置 | 职责 |
|---|---|
| `C:\Users\fengluo\Desktop\rsi-kernel-agent` | 唯一 RSI 系统源码、测试、文档、Store 和本轮归档 |
| `C:\Users\fengluo\Desktop\training\PallasKernels` | 当前 Kernel 权威源码；只读快照，Candidate 在独立目录修改 |
| `C:\Users\fengluo\Desktop\project2` | 历史知识来源；方法通过原 Knowledge MCP 保存，不直接成为当前 Best |
| `C:\Users\fengluo\Desktop\colab\google-colab-cli` | 用户已有桥接、会话和文件传输；原桥接处理既有认证 |
| `.artifacts/phase3-2026-11-12/experiments.sqlite3` 和同级 `artifacts/` | 原 ExperimentStore/ArtifactStore 的本轮数据库与工件树，含连接探针和 MLA 记录 |
| `/content/rsi-kernel-agent/phase3-20261112` | 固定 Runtime 的远端执行 spool、源码副本、回执和下载前工件 |

远端 spool 是恢复所需的执行状态，不是另一套实验数据库或 Memory。正式结果导入原 Store，经固定 Evaluator 和原 `consider` 处理。schema v1、Knowledge/Memory、Analyzer packet/response/session 保持原体系；Controller 管流程，会话模型提出 Plan、Candidate 和明确 repair response。

## 模块与现有入口

| 文件 | 公开入口或职责 |
|---|---|
| [colab_bridge.py](../../src/rsi_kernel_agent/workflow/colab_bridge.py) / [colab_bridge_call.py](../../src/rsi_kernel_agent/workflow/colab_bridge_call.py) | `ColabBridge(python, session, expected_runtime_id)`；execute/put/get 调用原 ColabRuntime、ContentsClient，固定 Runtime，不分配或重启 |
| [colab_remote.py](../../src/rsi_kernel_agent/workflow/colab_remote.py) | `ColabRemote(..., remote_python=None)`；显式 bootstrap、lookup、submit、read_artifact |
| [colab.py](../../src/rsi_kernel_agent/workflow/colab.py) | `ColabBackend(store, remote)`；沿用 submit/lookup/poll，持久化请求、导入工件及回执 |
| [colab_worker.py](../../src/rsi_kernel_agent/workflow/colab_worker.py) | 独立标准库 worker；Runtime 注册、串行、执行意图和完成回执 |
| [mla_runner.py](../../src/rsi_kernel_agent/workflow/mla_runner.py) | `run_mla_stage`；加载身份、独立 CPU Oracle、编译、正式计时及独立 Profile |
| [real_protocol.py](../../src/rsi_kernel_agent/workflow/real_protocol.py) | 固定 Context、bundle、实际加载身份、case、计时与 Profile 绑定核验 |
| [mla_baseline.py](../../src/rsi_kernel_agent/workflow/mla_baseline.py) | `audit_source_tree`、`prepare_sources`、`workload_matrix`、`create_baseline`；创建未验证基线，不伪造执行 |
| [统一 CLI](../../src/rsi_kernel_agent/workflow/__main__.py) | `colab-bootstrap` 与原 run/recover/repair 等入口；按 Task provenance 选择真实或 synthetic backend |

真实 stage 顺序是 `generate → compile → correctness → benchmark → profile → evaluate`。Synthetic 原路径不要求新增真实 Profile。当前 runner 的正式 Kernel 范围为 **MLA Decode / contiguous / BF16**；审计 Forward 不等于 Forward 已接入或通过。两种 Kernel 必须独立 Task 和 Evaluation Contract。

## 配置与显式初始化

本轮配置文件为 `.artifacts/phase3-2026-11-12/colab-config.json`。它包含以下字段，禁止将示例占位值直接用于已有 Runtime：

```json
{
  "python": "C:\\Users\\fengluo\\Desktop\\colab\\google-colab-cli\\.venv\\Scripts\\python.exe",
  "session": "rsi-p3-20261112",
  "runtime_id": "tpu-v6e1-s-euw4a0-xyzyx7kofigt",
  "nonce": "<首次初始化前生成并持久保存的稳定 ledger nonce>",
  "remote_root": "/content/rsi-kernel-agent/phase3-20261112",
  "remote_python": "/content/rsi-kernel-agent/env-20261112/venv/bin/python",
  "cache": "<本地下载缓存绝对目录>"
}
```

`python` 是装有原桥接的本地解释器。`remote_python` 可省略；省略时控制代码在 Notebook Python 中加载 worker，stage 子进程继承该解释器。指定时控制调用使用隔离解释器 `-I -B`，随后 worker child 使用同一 `sys.executable`。该选项必须位于 `/content/rsi-kernel-agent/` 下，不能通过它升级默认环境。`nonce` 是执行账本身份，不是账号凭据；恢复时必须保留，不能重新生成来绕开旧状态。

首次使用已确认 Runtime 时显式执行一次：

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 -m rsi_kernel_agent.workflow --colab-config '.artifacts/phase3-2026-11-12/colab-config.json' colab-bootstrap
```

bootstrap 按 SHA256 文件名安装 worker/runner；已存在字节必须完全一致。首次注册固定 canonical spool root、Runtime ID 和 nonce。同 Runtime 换 root/nonce 会被拒绝。日常 lookup 不会自动 bootstrap；丢失账本时重建新目录不能证明旧任务未执行。

原桥接每次检查 session 的 endpoint 等于固定 Runtime；传输输出必须恰有一个 `RSI_RESULT` JSON 且无 Notebook error。桥接错误/超时表示远端状态 unknown。关闭连接使用 `shutdown_kernel=False`，不会终止用户 Notebook。配置、helper 不读取或打印认证文件。

已通过 intake 的正式优化 Task 使用原入口：

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 -m rsi_kernel_agent.workflow --db '.artifacts/phase3-2026-11-12/experiments.sqlite3' --artifacts '.artifacts/phase3-2026-11-12/artifacts' --colab-config '.artifacts/phase3-2026-11-12/colab-config.json' recover --task '<已存在真实 Task ID>' --max-steps 100
```

初始 Baseline 是优化前 Gate，不是 Analyzer 优化 Plan。当前 [start_baseline.py](../../.artifacts/phase3-2026-11-12/start_baseline.py) 使用生产 `create_baseline`、`ColabBackend` 和 `integrate_result`，先保存固定基线及一次成本预留，再逐 stage 查询/提交。该脚本是本轮辅助编排，不新增 Controller；正式多策略优化仍走 session/publication/intake/Controller。

## Request、receipt 与工件闭包

调用 `make_request(store, job, stage)` 产生 schema 1 请求，包含 `backend_id=colab.v1`、`provenance=real`、稳定 `operation_id`、`request_digest`、stage、完整 job、CandidateIdentity 和 ParentIdentity。operation 由 Run/stage 确定；同 operation 的异值请求拒绝。`ColabBackend` 在远端提交前把固定请求、Runtime ID、nonce 和 root 追加为原 Decision。

真实 variant 含 `source_ref`、`source_bundle_ref`、`config_ref`、`build_ref`、label/priority/device/cost。bundle 明确 entry 的 path/symbol 和每个 source/dependency 文件的 ArtifactRef；build 绑定 source bundle、独立 Oracle bundle、runner 和 Oracle 摘要。Candidate 可变文件与 trusted helper 明确分开，不能通过改 helper、参考实现或 Benchmark 制造通过。

worker 接口 `run_command` 或 stdin JSON 支持 bootstrap/lookup/submit。submit 载荷是 `source_bundle`、`oracle_bundle`、`blobs`（digest→base64）。所有字节先核对长度与 SHA，源码按字节复制到独立目录，拒绝路径逃逸、symlink/junction。stage 在全新 `-I -B` 子进程运行，移除继承 PYTHONPATH；runner 核对实际 module path、entry、文件摘要、config/specialization 和 Runtime 环境，拒绝同名旧模块/未受信任依赖。

远端 completed 返回 `receipt` 及 `artifacts: [{ref, path}]`。receipt 包含请求绑定、contract/run/candidate/parent/context ID、status、outputs、cost 和完整 `artifact_refs`。后者包含 typed output 原始工件，也包含 stdout/stderr、设备探针、specialization、HLO、失败 stage 已产生的数组/trace。`artifacts` 的 ref 数组必须与 receipt 闭包完全相等；本地逐个下载核对后放入原 ArtifactStore，再验证 stage 并发布原执行回执 Decision。原始 receipt 自身另有 ArtifactRef，后续任何读取仍核验上游工件。

typed 输出路径由 worker 转换为引用：`raw_artifact_paths → raw_artifact_refs`、`raw_xplane_paths → raw_xplane_refs`、`report_path → report_ref`。不将附加日志塞进固定 typed schema。网络错误时尚未确认的结果保持 unknown；已导入本地的完整 receipt 可以在 Runtime 消失后重放读取。

## 恢复和设备队列

| lookup 状态 | 行为 |
|---|---|
| completed | 取回同一回执与工件，幂等补归档；不重执行 |
| running | 继续 poll，不另建实验 |
| definitely_not_started | 只有有效账本证明没有执行意图时，允许重交原请求 |
| unknown | 阻塞并查证；不猜成失败、未开始或新 Run |

远端执行意图先 fsync，再调用 runner；completion 独占原子发布并有自身摘要。意图存在、进程已退出且没有有效 completion 时，unknown 会阻止该 Runtime 后续任务。固定协调路径为 `/content/rsi-kernel-agent/runtime-locks/<SHA256(runtime_id)>`，覆盖同 Runtime 的所有合作 Task 和 generate/compile/correctness/benchmark/profile，避免不同 spool 绕过锁。没有强制清除 unknown 或强杀用户 Notebook 的接口。

worker 在前后观察 Linux `/proc/*/fd` 对 `/dev/accel*`、`/dev/vfio/<group>` 的持有；其他 owner 阻止执行，正式 benchmark/profile 缺乏可见性时标 invalid。这个保证限于合作 worker 和可见主机进程，不能证明隐藏容器/其他主机/两个采样之间无竞争。完整实现和真实进程退出测试见 [恢复指南](phase3-worker-recovery.md)。

## 固定多 case Oracle 与计时

默认 Workload Matrix 来自当前接口，`cases` 可显式配置；不能任意删减已固定 Task 的失败 case：

| case | B / H / capacity | latent / RoPE / block | 主要检查 |
|---|---|---|---|
| decode-small | 1 / 16 / 1024 | 512 / 64 / 1024 | 小规模 BF16 contiguous；默认正式 Benchmark case |
| decode-boundary | 3 / 16 / 256 | 128 / 64 / 128 | lengths `[0,1,129]`；未使用 cache/tail 含 NaN，检查不误读及正确保留 |
| decode-default | 8 / 16 / 16384 | 512 / 64 / 1024 | 默认规模和多 batch |

每个 case 使用冻结 Oracle bundle 中原注册 `make_inputs` 生成输入，再在 `JAX_PLATFORMS=cpu` 的独立 `-I -B` 子进程调用原 `run_reference_op` / `mla_decode_paged_reference`。候选读同一保存输入。attention 比较 shape/dtype、有限性、max abs/max rel/RMS 和逐元素固定容差 `atol=rtol=0.01`；cache 全输出按 BF16 原始位比较，包括约定可保留的 NaN 区域。`finite=false` 的 opaque cache 不自动失败，但任何 bit 差异都会失败；attention 必须有限。输入 NPZ、参考输出、候选输出和 manifest 都归档。当前不能把这些实现能力写成真实 correctness 已通过。

compile/correctness 必须覆盖全部固定 cases；Benchmark/Profile 采用固定 `benchmark_case_id`。正式协议 `mla-contiguous-host-sync-v1` 复用冻结的 `benchmarks.profiler.measure`：编译单独记录，5 warmup、20 原始样本、ms、median，不删异常值；保留 mean、population std、min/max、线性插值 P25/P75。输入已在设备上，全部 pytree 输出用 `jax.block_until_ready` 同步；计入 public contiguous prepare、attention 和完整 cache output 的 host-synchronized 调用边界，不称纯设备 Kernel 时间。Host/Device 输入准备、编译不在正式样本内，边界和成本须在比较报告明确。

Profile 是同一 Run 的独立 operation，在正式 Benchmark 之后单独采集，不能混入正式 latency。固定原 parser SHA 和默认 ProfileOptions；20 profile calls、5 warmup，回收 XPlane、duration.json、时钟域和 operation 绑定。TRACE_ONLY_HOST/XLA/COMPUTE 等扩展模式当前未在本次环境证实，runner 不擅自启用；parser 不提供的 DMA、VMEM/MXU 利用率保持 unavailable。真实 Profile Gate 通过前不能开始自动优化。

## 当前真实环境与验证

已实读同一个 Runtime：`tpu-v6e1-s-euw4a0-xyzyx7kofigt`，设备自报 `TPU v6 lite`，1 device / 1 process，coords `[0,0,0]`，core_on_chip `0`。申请类型是 v6e1，保留设备原始名称。当前环境以每次回执为准，以下为归档观察，不是在线状态保证。

| 环境 | Python | JAX / jaxlib | libtpu | TensorFlow / xprof |
|---|---|---|---|---|
| Colab 默认 | 3.13.18 | 0.11.2 / 0.11.2 | 0.0.50 | 未安装 / 未安装 |
| RSI 隔离 | 3.11.17 | 0.8.1 / 0.8.1 | 0.0.30 | 2.20.0 / 2.22.1 |

精确 XLA backend/version 与设备字段见 [默认环境](../../knowledge-site/manifests/phase3-2026-11-12/environment.json)、[隔离环境](../../knowledge-site/manifests/phase3-2026-11-12/environment-isolated.json)，隔离依赖位置和版本见 [setup](../../knowledge-site/manifests/phase3-2026-11-12/isolated-environment-setup.json)。隔离目录复用历史 uv 方法，没有替换默认 Colab 或本地依赖。

最小矩阵乘和平方和实际同步返回预期值，见 [连接审计](phase3-colab-connectivity-audit.md)。探针事后导入原 Store，有独立 Run/Evidence，但没有远端 ledger，不能用它们证明正式 worker 恢复；也没有 Benchmark/Best。当前原始 Decode 的边界编译失败已经固定 Evaluator 归类并归档；新 Candidate 的修复和硬件验证保持独立身份与失败父记录。

本地完整套件实际一次运行 **308 项：306 通过、2 跳过，529.627 s，0 失败/错误**；原 MCP **5/5，0.747 s**。跳过是 Windows symlink WinError 1314 和 Debian 系统 `sitecustomize.py` 不存在；后者的 Linux 专项已运行。它们属于 Local CPU/Synthetic 软件验证，不是 308 次真实 TPU 测试。环境、命令、日志与各类证据见 [test-environment.json](../../knowledge-site/manifests/phase3-2026-11-12/test-environment.json)、[tests.txt](../../knowledge-site/manifests/phase3-2026-11-12/tests.txt)、[existing-mcp-tests.txt](../../knowledge-site/manifests/phase3-2026-11-12/existing-mcp-tests.txt)。原日志保留 PowerShell 对 unittest stderr 的 NativeCommandError 包装和 asyncio 诊断；最终 unittest 为 OK，执行退出码为 0。

之后只更换 Candidate 工件不代表系统代码变化；不因此宣称又运行了一次完整根套件。若修改 runner/backend/evaluator 等系统代码，需补充相关专项验证和新摘要。此处不宣称 Phase 3 完成，真实 baseline、Profile、优化和多轮证据分别由对应 Gate 报告确认。
