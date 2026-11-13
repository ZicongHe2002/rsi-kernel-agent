# Phase 3 独立代码审查

审查日期：2026-11-12（Asia/Shanghai）。审查者为独立协作 agent；未修改生产代码。依据根 `AGENTS.md`、`docs/phase3/phase3-implementation-plan.md` 与归档原始请求。检查范围为真实协议、Colab bridge/remote/worker/backend、固定 MLA runner、原 execution/intake/controller/evaluation/CLI 的兼容改动及对应测试。增量复核加入 `mla_baseline.py`、其测试、远端独立解释器和按 SHA256 命名的工具路径。

本报告只证明 Source audit / Local CPU test。没有调用外部 Runtime、TPU 或读取凭据；没有真实 correctness、benchmark、XPlane 或优化收益结论。完整根级套件由主 agent 执行，本审查没有重复运行。

## 结论

初次审查发现 3 项 Important，均在审查过程中及时通知主 agent，生产修复由其他协作者完成。修复代码已复读，对应本地专项已验证。当前检查范围未发现未解决的 Critical / Important；仍有 1 项 Minor 的证据字段语义问题。此结论不代表 P3.1–P3.5 硬件 Gate 通过。

## Important 发现与修复复核

### R1：设备锁原先只按 spool 目录隔离

- 初次位置：`src/rsi_kernel_agent/workflow/colab_worker.py:367`，原为 `self.root / 'runtime.lock'`；unknown 前序扫描也只覆盖该 root 的 operations。
- 触发方式：相同 `runtime_id` 创建两个 `WorkerSpool`，使用不同 root/nonce 并分别 bootstrap；两个 Task 各自提交长时间 CPU fixture 操作。原代码允许建立两套互不协调的锁与隔离状态。相同 root 内的跨 Task 测试无法覆盖此情形。
- 影响：同一 Runtime 的两个 spool 可以并行使用 TPU；另一个 root 也无法看到原 root 的 unresolved intent，违背跨 Task 设备互斥及 unknown 隔离要求。
- 修复：当前 `colab_worker.py:182` 使用由 runtime ID 决定的固定 coordinator；`:188` 持久登记唯一 root/nonce，`:222` 验证登记和 sentinel，`:390` 使用 coordinator runtime lock。第二 root/nonce 被拒绝，登记丢失保持 unknown，不重新创建执行空间。
- 复核：`test_same_runtime_cannot_use_second_spool_to_bypass_serialization`、`test_global_registration_loss_does_not_create_fresh_execution_space`、原有跨 Task 串行及 crash/unknown 测试均通过。

### R2：下载后的完整原始输出没有进入正式证据闭包

- 初次位置：`src/rsi_kernel_agent/workflow/colab.py:82` 下载 `state.artifacts`，随后只发布 `receipt`；原 `evaluation.py:232` stage Evidence 仅引用 receipt 本身。
- 触发方式：远端 compile/benchmark/失败操作返回 stdout、stderr、HLO、specialization、device-before/after 等 descriptors，但这些 refs 不在 typed outputs。原 adapter 虽把字节写入 ArtifactStore，却丢弃 descriptors；销毁远端状态后，无法从 Run 的正式证据找回这些工件，也不能在本地恢复时复核它们。
- 影响：失败诊断、编译身份、warmup 明细和干扰观察成为无关联 blob；后续损坏不一定影响可见证据有效性。
- 修复：当前 worker `colab_worker.py:468` 在 canonical real receipt 中加入全部 `artifact_refs`；`colab.py:83` 要求下载 inventory 与回执完全一致；`execution.py:211` 验证每个 ref；`evaluation.py:232` 将完整 refs 绑定到该 stage Evidence。synthetic receipt schema 保持原样。
- 复核：`test_all_remote_logs_are_bound_and_reverified_on_local_recovery` 验证日志引用进入回执，并验证本地日志损坏导致恢复拒绝；worker 失败、stdout/stderr 和完整 inventory 测试通过。原 correctness/raw-profile 证据测试继续通过。

### R3：顶层或别名 helper 的旧路径导入绕过身份检查

- 初次位置：`src/rsi_kernel_agent/workflow/mla_runner.py:140`，原代码仅检查 module 名称以 `pallas_kernels` 或 `jax_ops` 开头的条目。
- 独立 CPU 复现：创建包含 `pallas_kernels/kernel.py` 与 `helper.py` 的正确 bundle；将传入 modules 中 `helper.__file__` 指向另一目录 `old/helper.py`，其内容为 `value = 999`。原 `verify_loaded_bundle` 接受，并返回上传目录 `helper.py` 的 SHA256，而不是拒绝旧路径。复现输出为 `accepted: true`。没有导入 JAX 或执行 TPU。
- 影响：上传摘要可与实际使用的本地 helper 脱离，错误路径依赖不被发现。
- 修复：当前 `mla_runner.py:144` 起核对完整 modules 路径，检查包逻辑根与 manifest Python 路径后缀；本地外部 helper 被拒绝，只允许显式运行时安装目录与 launcher。合法 stdlib/site-packages/dist-packages 被单独处理。
- 复核：别名顶层 helper 负例通过；修复过程中发现按 `__init__.py` basename 判断会误拒合法库，已及时反馈并改为逻辑路径判断，合法 package initializer 回归通过。独立首轮遇到 dist-packages 回归 ERROR，随后实现加入 `site.getsitepackages()`，15 项 runner 专项重跑全部通过。

## Minor 未完成项

### M1：`loaded_files` 实际是全部验证过的 bundle 文件

- 位置：`src/rsi_kernel_agent/workflow/mla_runner.py:181` 返回完整 `expected` 清单；`real_protocol.py:171` 要求与整个 bundle 相同。
- 触发：bundle 包含一个未导入的 Python helper 或 YAML，调用 `verify_loaded_bundle(..., modules={})` 后它仍会出现在返回列表中。
- 当前路径与字节核验已加强，R3 的旧路径绕过已修；但字段名和 “Actually loaded source dependencies” 文案比实际观测范围更强。
- 建议：明确记录其语义为“全部 bundle 字节校验 + 已加载 Python module 路径边界检查”，或以后另外记录真实 `loaded_modules`。不要声称清单中每个文件均已执行或导入。本项不阻止当前本地软件集成检查。

## 独立验证

解释器：`.\knowledge-site\.venv-mcp\Scripts\python.exe -B -X utf8`。默认 sandbox 启动 uv trampoline 返回 permission denied；经自动审批允许后运行同一现有解释器。没有安装依赖、网络或硬件访问。

首轮命令：

```powershell
$env:PYTHONPATH = 'src;tests'
.\knowledge-site\.venv-mcp\Scripts\python.exe -B -X utf8 -m unittest -v test_phase3_worker test_phase3_runner test_phase3_backend test_phase3_protocol test_phase3_remote test_phase3_bridge test_phase3_cli
```

首轮实际结果：71 tests / 70 passed / 1 ERROR，耗时 48.500s。唯一 ERROR 是当时正在修复的 `test_system_dist_packages_remain_allowed_runtime_dependencies`；完整记录保存在 [independent-review-tests.txt](independent-review-tests.txt)。PowerShell 对 unittest stderr 加了 NativeCommandError 包装，最终 Python exit code 为 1；未把该轮记为通过。

依赖路径修复后仅重跑受影响的 `test_phase3_runner`：15 tests / 15 passed / exit 0，耗时 0.215s，见 [independent-review-rerun.txt](independent-review-rerun.txt)。其余 56 项在首轮已通过。随后补入的 Debian system-owned `sitecustomize.py` 允许规则已复读，Windows 测试不覆盖该 Linux 分支；最终集成回归由主 agent 运行。无需把这些 CPU fixture 解释为 TPU 执行证明。

代码复读确认继续复用原 Store、Controller、Evaluator 和 `consider`；真实操作使用 generate → compile → correctness → benchmark → profile，同一个 Run 中 benchmark/profile operation ID 不同且时间不重叠；原 synthetic 四阶段保留。未知远程状态不触发盲目重提；已持久完成回执可本地恢复。真实 oracle 在独立 CPU subprocess 使用冻结来源，benchmark/parser 由固定来源加载。设备环境和实际软件/API 兼容性仍需首个真实 Runtime 验证。

## 增量复核：Baseline 与隔离环境

本次增量没有新增 Critical / Important 发现。独立运行 `test_phase3_baseline` 和 `test_phase3_remote` 共 10 项，全部通过，exit 0，耗时 0.544s；日志为 [independent-review-incremental.txt](independent-review-incremental.txt)。没有调用真实 Runtime，未中断或重提正在运行的 baseline operation。

### Baseline 数学范围、Oracle 与环境

- `mla_baseline.prepare_sources` 从指定权威目录读取显式 package `.py`、YAML 和六份 benchmark/tools 文件，拒绝链接；保存原 ArtifactStore 后重新核对源摘要，未复制凭据、缓存、历史工作树或修改原 Kernel。
- `workload_matrix` 明确 contiguous MLA Decode、BF16、16 query heads、L512/R64 small/default 与 L128/R64 boundary；attention 为 allclose，完整 cache 为 exact。边界 lengths=0/1/129，opaque tail 与 padding NaN 仅按原始 cache 位模式保留，不把 attention 非有限值视为合法结果。
- 源码 entry 与 oracle entry 虽位于同一个原文件，Oracle 的整个 bundle 单独冻结；所有非 entry Python、YAML、registry/config、benchmark/parser 都在 trusted_files 中。Candidate 的资源配置仅能更改 block_size/vmem，不能改变 shape、scale、window、cap、interface 或容差。
- 准备动作创建原 Store 记录及 MemoryWriter Plan 父绑定，Run 为 not_run，Best 不变；新 key 保留 Candidate 新建 Run，不同 benchmark case 进入不同 immutable context。数学范围是 public contiguous prepare/attention/full-cache output；原 warmup/measure 同步完整输出，compile 独立计时。
- `_observed_v6e` 接受设备实际报告的 `TPU v6 lite` / `TPU v6e`，要求版本、设备、进程和 Runtime 信息。准备器仅验证输入结构，不能独自证明观察真实性；runner 每次实际读取并 exact-match 完整 environment。切换 `remote_python` 因而不能把版本不一致的阶段当成同一 comparison context。

### Remote / worker 变化

- `colab_remote.py:28` 为 worker/runner 生成 SHA256 命名路径，bootstrap 只在不存在时安装、已有字节必须一致；不会覆盖旧子进程仍使用的 worker 文件。
- `colab_remote.py:38` 的显式 `remote_python` 使用参数列表、`-I -B` 和 stdin JSON 运行 worker control，45 秒超时保留为 transport unknown；`lookup` 仍不会 bootstrap。该路径只允许位于 RSI 隔离目录。
- `colab_worker.py:339` 子进程去掉 PYTHONPATH，并通过 `-I -B` 禁用调用者路径影响；设备锁和持久 claim 逻辑不变。已复读 stdlib、DLLs、site/dist-packages 与 Debian 精确 system-owned sitecustomize 允许规则，未放宽任意 sys.path。
- 另外读取协作者归档的 [worker-windows-tests.txt](worker-windows-tests.txt) 与 [worker-linux-tests.txt](worker-linux-tests.txt)：分别记录 20/20，8.052s / 4.917s，含真实 worker 调用固定 runner 的入口拒绝与 PYTHONPATH startup 负例。本增量没有把这些日志冒充本 reviewer 新运行的测试。

### 实际进度与未关闭验证范围

增量已只读核对 `connectivity-probe.json`、`environment-isolated.json` 及连接审计：主 agent 已取得 Runtime 分配和同步 JAX 最小计算结果；默认环境 JAX/jaxlib 0.11.2，隔离环境 Python 3.11.17、JAX/jaxlib 0.8.1、libtpu 0.0.30、TensorFlow 2.20.0、xprof 2.22.1，实际设备名为 `TPU v6 lite`。这些已保存结果支持连接/设备计算观察。reviewer 没有独立重跑远端 probe，仍不据此声明 MLA 正确性、计时、真实 XPlane 或优化收益。

主 agent 本次交接时 baseline 为 compile operation running；此处不是实时状态。以下事项保持未验收：

1. 默认矩阵仅 small/boundary/default，window=0、cap=0。实施计划中 medium 和 mask/cap 配置尚未由这个默认矩阵覆盖；三 case 成功不能扩称所有分支通过。
2. 真实 known-wrong attention/cache Candidate 的 oracle 拒绝证据尚未由本次审查收到。CPU compare_frame 负例只能证明局部比较逻辑。
3. 真实全矩阵 correctness、正式原始计时样本、Profile capture/parser 与完整回执，以及后续两轮优化仍各自需要实际证据。本报告不作这些 Gate 的通过声明。
4. M1 的 `loaded_files` 字段语义仍建议明确，不应描述成所有清单文件都曾被执行或导入。

## 增量复核：大工件分块回传

主 agent 的真实 compile 已完成，但两个约 167MB NPZ 的单次 Contents 下载超过外层超时；该事实来自主 agent 的执行诊断，本 reviewer 没有触碰 Runtime。新增回传逻辑位于 `colab_remote.py:98` / `:117`，没有改变 worker、runner、固定 contract 或重新提交 compile。

代码复读未发现新增 Critical / Important：远端 stdlib 将不可变 blob 切成默认 8MiB 块，检查源件大小/完整 SHA256、块字节/摘要及非链接路径。客户端核对 manifest 的源摘要、连续 offset、完整覆盖和每块路径/大小/摘要；已缓存块重新核验后才能复用。整件先写临时文件，完整大小与 SHA256 通过后才 replace；失败删除临时整件。原 backend 仅在全部 artifact 导入成功后发布 receipt，部分下载不构成成功。

按主 agent 的 tests-only 授权，本 reviewer 为这次变化新增四个边界测试，未修改生产代码：

- `test_valid_chunk_hashes_cannot_mask_a_wrong_whole_artifact`：每个块摘要都合法但拼接整件不符，拒绝且不留完成目标或 assembly 临时件。
- `test_corrupt_cached_chunk_is_retrieved_again_before_assembly`：缓存块被改写后只重取坏块，再按整件摘要复核。
- `test_corrupt_downloaded_chunk_cannot_publish_full_artifact`：网络返回错误块字节时拒绝，不生成完成目标。
- `test_partial_chunk_transfer_does_not_publish_receipt_or_repeat_execution`：真正调用本地 `ColabRemote` 分块下载器，以 4-byte threshold 和 mocked 原 bridge 模拟第二块断线。首次 backend 返回 unknown，Store 无 execution receipt、无完整 artifact；恢复的下载序列为 `[0,1,1,2]`，执行提交计数仍为 1，完整原始字节导入后才发布 completed receipt。

独立执行 `test_phase3_remote` 与 `test_phase3_backend`：19 tests / 19 passed / exit 0，耗时 1.669s；见 [independent-review-chunks.txt](independent-review-chunks.txt)。这些测试不访问网络，不证明真实网络吞吐；此前整套日志与本次针对性回归应分别报告。本 reviewer 没有重跑完整套件，也未把下载恢复视为新一次硬件执行。

### 后续增量：有界并发下载与错误文案

再次只读复核 `ColabRemote.transfer_workers`：默认 4，运行时校验整数且范围 1..4；单个 artifact 的线程池只并行原 `bridge.get` 和独立块校验，各 index 使用不同缓存目标。并发不包含 `submit`、compile 或其他设备执行。全部 future 成功之后才建立 assembly 临时件，组装仍按 manifest index 顺序，整件摘要复核与 backend receipt 发布条件保留。

任何 future 异常会离开下载路径，线程池退出会等待已安排工作结束；已成功块可用于后续恢复，错误不进入 assembly 或 completed receipt。该上限约束单次 artifact 下载，并非跨所有独立客户端的全局限速；当前 adapter 的工件循环按顺序调用，符合这次范围。

`colab_bridge.py` 分开报告 deadline exceeded 与 process unavailable，仍使用固定文案并 `from None`，不附带原异常、stdout、stderr 或 URL。复读未发现新增 Critical / Important。

本 reviewer 独立运行 remote/backend/bridge 三模块：26 tests / 26 passed / exit 0，耗时 1.516s，日志 [independent-review-parallel.txt](independent-review-parallel.txt)。新增并发测试观察设为 2 时峰值为 2，并验证完整输出顺序；顺序恢复测试显式使用单 worker，原损坏、部分下载不发布回执与不重复执行测试继续通过。没有使用真实连接，也没有重跑完整套件。

## 增量复核：跨工件共享分块缓存

日期：2026-11-12。独立检查范围为 `src/rsi_kernel_agent/workflow/colab_remote.py` 与 `tests/test_phase3_remote.py`，另只读追踪现有 backend 的 receipt 发布条件。本轮未发现新增 Critical / Important / Minor，也未修改生产代码。此前 M1 的字段语义建议仍保留；本结论不关闭真实 TPU Oracle 或 Phase 3 Gate。

审查版本 SHA256：`colab_remote.py = 103991f93e17bf5749bd89d5250f181ab187585018f5299c848f3e4a8fad03b9`；`test_phase3_remote.py = 60f68099e9154314ebc611e701b0624f2070b0ac141174be05756697335971bf`。Git CLI 不可用，本次以文件摘要绑定复核版本。

`colab_remote.py:20` 每次复用同时核对 size 与 SHA256；`:201` 起的旧 `parts/<whole-digest>/<index>` 迁移也对实际字节重新计算身份，不相信旧目录名或 index。共享块保存于 `cache/chunks/<chunk-sha>`，不同 whole digest 及新建客户端实例可以复用。`:28` 使用新临时文件写入、fsync、原子 replace，未创建可写硬链接；共享文件与当前/旧 staging 的后续改写彼此独立。

`:228` 的逐 digest Lock 合并同一下载调用内的重复内容；坏共享块与坏 staging 都不能避免重新下载。`:244` 验证下载结果，`:263` 在组装时再次验证 staging，`:268` 验证整件 size/SHA 后才发布目标。每次调用保持 1..4 个 worker，顺序按 manifest 组装。该锁及并发上限属于单次 `_download_chunks` 调用，并非跨客户端或跨进程的全局下载去重/限流；现有 backend 的 artifact 循环顺序读取，符合本次实现范围。

`:14` 检查本地文件及全部父路径的 symlink/junction；当前共享目录和旧 parts 迁移目录均经过检查。独立 Windows junction 探针拒绝共享目录链接及旧 parts 子目录链接，未读取或改写链接外目标。现有 Windows 文件 symlink 测试因权限跳过，随后使用本地 WSL Ubuntu 单独运行相应用例并通过；没有修改系统权限或安装环境。

本 reviewer 在独立脚本 [review_shared_chunk_cache.py](review_shared_chunk_cache.py) 加入 7 个 CPU 探针，未改动协作者的生产文件或正式测试文件：旧目录名不能授权错误字节；组装前 staging 被改写时拒绝且不发布整件，随后从共享块恢复而不再次下载；默认 4 路并发峰值及非法 worker 数；损坏共享块的重复内容只下载一次；共享文件改写不影响 staging 副本；旧 parts junction 拒绝。现有 backend 的部分传输测试继续证实首次 unknown 时无 execution receipt/完整 artifact，恢复只下载缺块，远端执行提交计数保持 1。

独立验证命令与结果：

```powershell
$env:PYTHONPATH = 'src;tests'
.\knowledge-site\.venv-mcp\Scripts\python.exe -B -X utf8 knowledge-site/manifests/phase3-2026-11-12/review_shared_chunk_cache.py
```

Windows：33 tests / 32 passed / 1 skipped / exit 0，耗时 2.081s；其中既有 remote 19 项、backend 7 项、独立探针 7 项。完整日志：[independent-review-shared-chunks.txt](independent-review-shared-chunks.txt)。首次单独 remote/backend 检查为 26 tests / 25 passed / 1 skipped / exit 0，1.814s；后续上述运行包含全部这 26 项并补充独立探针。

```text
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/c/Users/fengluo/Desktop/rsi-kernel-agent && PYTHONPATH=src:tests python3 -B -m unittest -v test_phase3_remote.RemoteTests.test_shared_chunk_symlink_is_rejected_without_following_it'
```

WSL：1 test / 1 passed / exit 0，耗时 0.002s；日志：[independent-review-shared-symlink-linux.txt](independent-review-shared-symlink-linux.txt)。PowerShell 为 unittest stderr 加了 NativeCommandError 显示包装，WSL/Python 实际 exit code 是 0。现有 Python/WSL 启动沿用 scoped 自动审批；所有 bridge 均为内存 fixture，未访问网络、凭据或 Runtime，也未把 CPU 测试声明为实际下载吞吐或硬件执行证据。最终全套集成回归由主 agent 执行。

## 增量复核：runtime2 真实 Oracle 负例

2026-11-12，本 reviewer 使用 [verify_oracle_negatives_locally.py](verify_oracle_negatives_locally.py) 独立只读复核两个已落盘的真实负例；脚本 exit 0，报告为 [oracle-negative-verification.json](oracle-negative-verification.json)。Store 使用 SQLite `mode=ro` 和 `query_only`，零网络调用、零 Store 写入；只检查 small/boundary 工件，未读取大型 default baseline NPZ。两个 Run 均绑定新 Runtime `tpu-v6e1-s-euw4a1-344ww12q09usn` 与负例 context `runtime-context:1f262bee28366383c5ea39851fc84e52f48178a855a57acd65a2276d37449886`，没有混用旧 Runtime 计时。

从原始 candidate/oracle NPZ 独立重算：attention 负例在 small、boundary 的 `[0,0,0]` 各有且仅有一处 allclose 容差违规，max abs 分别为 0.999267578125 / 1.0；两个完整 cache 均逐字节一致。cache 负例两组 attention 均零违规；small cache `[0,0,0]`、boundary cache `[1,0,0]` 各只有一个 BF16 word 不同。原始 NPY metadata、shape 和内部 SHA 均通过校验，数值统计与记录回执一致。

源码、source bundle 和 diff 与正式 ArtifactStore 相同；两个 Candidate 仅在 `build_mla_decode_contiguous` 的返回前添加故意错误 wrapper，原 native builder body 保持不变，冻结 Oracle 源 SHA 为 `e53b0e5ccff3ecf18192f30ef179cdb207c177f2f2dac48b2a08f40acc5000a6`，Oracle AST 未改变。每个 Run 核对 28 个 stage 工件、6 条 Evidence；全部 stage refs 可从对应 Evaluation 到达。attention/cache 的去重工件字节总数分别为 5,697,469 / 5,697,461。

两项实际 Store 记录均只有 generate、compile、correctness 三个 execution request 和回执，correctness failed、classification incorrect、confirmed false，无 benchmark/profile 请求。原 `consider` **确实产生了拒绝 Decision**，其 `eligible=false`、`branch_promoted=false`、`task_promoted=false`、reason=`run_not_succeeded`；两个 Evaluation 都没有进入 Best。准确结论是“未晋升”，不是“未调用 consider”。这两项 known-wrong Oracle 拒绝验证已有真实证据；本报告前面“负例未验收”的段落属于此前时间点，不能继续当作当前缺口。该结论不代表所有错误 Candidate 都必然被拒绝，也不代替 runtime2 baseline、优化轮次或完整 Phase 3 验收。

## 增量复核：真实优化轮次辅助脚本

只读审查 `.artifacts/phase3-2026-11-12/real_round_aux.py`、`prepare_real_round.py`、`run_real_round.py` 和对应测试；未发现新增 Critical / Important / Minor，未修改辅助实现或生产代码。审查文件 SHA256 分别为 `9201c2b297de1517b298ac4b646ee19a9c950517825f7ac4620b7bbcd87b470f`、`dfaa146f726d4f62558578ef55382249ff554d5a48d0ffe9c9ad81b05a7d0fb6`、`9b18e673c512482962905ae40ee9c691f1ec1f914f5ef77d64f80c3a64ae91b1`；`test_real_round_aux.py` 为 `9ea611cb11ba23c15d0d0446d0823442019cb37edad6f20f85613ec4e84c3c4c`。

`real_round_aux.py:53` Gate 从原 Store 的 exact workflow-result 完成 marker 读取结果，重新使用原 `_verified_prefix` 核验回执与工件；负例必须在 correctness 拒绝，baseline 必须五阶段全部成功且 Profile available。`:104` 绑定 runtime2 环境、协议、两条不同 Strategy 的 baseline 和两项原始 known-wrong admission，并拒绝该 Runtime 持久记录的 Oracle Gate failure。对这些条件的检查不依赖可编辑的独立 result JSON 文件。

`prepare_real_round.py:41` 恢复已持久 round 时使用原 manifest、Memory snapshot 和完整 histories，不读取新的 Knowledge revision。session/result digest 和 manifest 由 `real_round_aux.py:171` 核对；`:184` 要求前轮 exact intake 的所有 accepted jobs 及后续 repair/retry attempt 都有终态及原完成 marker，第二轮 snapshot 还须包含这些 Run 和结果。`run_real_round.py:61` 固定原 variants/publication 输入，随后只调用现有 publish → intake → Controller；中断恢复沿用原 Job、Run 和 operation，unknown 保持 blocked 并交原 Controller lookup 处理。

独立运行：

```powershell
.\knowledge-site\.venv-mcp\Scripts\python.exe -B -X utf8 .artifacts/phase3-2026-11-12/test_real_round_aux.py
```

11 tests / 11 passed / exit 0，50.129s；完整日志 [independent-review-real-round-aux-tests.txt](independent-review-real-round-aux-tests.txt)。包含无正式完成 marker、改 Run 身份、坏原始工件、Profile 不可用时拒绝，以及固定 session/manifest/variants、已准备 round 不重取 revision、前轮未完成拒绝。新增 driver 测试执行 1 step 后恢复完成并再次重跑，6 个 Job ID、spent executions=6、Store 记录数、`completed.json` 字节都不变。测试使用临时 SQLite、fixture Knowledge 和 SyntheticBackend，没有访问真实 Store、网络或 Runtime；asyncio slow-callback 日志及 PowerShell stderr 包装不代表测试失败。

恢复范围仍有明确边界：run 入口会启动本地 StdioKnowledge context；已有 intake 时原 intake 函数直接返回持久结果而不重新读取 revision，但仍依赖 MCP 本地启动可用。因此不把它描述成完全离线恢复。这些 CPU 测试证明辅助编排与恢复约束，不能代替接下来的真实优化执行证据。

## 增量复核：runtime2 两条 Strategy baseline

2026-11-12，本 reviewer 分别运行 [verify_runtime2_baselines_locally.py](verify_runtime2_baselines_locally.py) 的 `baseline-repair` 和 `baseline-b` 参数，两次均 exit 0；合并输出为 [runtime2-baseline-verification.json](runtime2-baseline-verification.json)，`both_runtime2_baselines_verified=true`。没有重复下载、硬件执行、原 Store 写入或覆盖旧 Runtime 报告。校验按完整工件 SHA 流式读取，同一次校验中已核对且 stat 身份未改变的 immutable blob 不重复读取。

两条 baseline 属于 Runtime `tpu-v6e1-s-euw4a1-344ww12q09usn`、同一个新 context `runtime-context:89820a7b421896bf463bb70f671ed7ce4c827086a4e37227ed7c1fc34d34d8b8`、两条不同 Strategy。它们使用完全相同的 repair 源 SHA `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`。每条均完成原五阶段，small/boundary/default 三组 correctness 回执通过、Evaluation confirmed=true；每条验证 49 个 stage 工件、9 条 Evidence，Evaluation 的完整 stage 工件闭包可达。A/B 字节总量分别为 509,949,153 / 509,948,192。

small 的 20 个原始 host-synchronized samples 分别重新计算全部统计量，与 result 完全一致：A median=0.19319499995162914 ms、mean=0.1956829999812726 ms；B median=0.21536499980356894 ms、mean=0.21873349999168568 ms。两者 warmup=5，无 outlier 丢弃。原 Profile 与 benchmark operation 不同且时间不重叠，raw PB/report 的 SHA 与 size 校验通过，保存的 parser 每组 20 个有效样本，归一化均 available、errors/issues/warnings 为空。A/B program median 分别为 6.624375 / 6.615 us；prepare 为 0.694375 / 0.69375 us；attention 为 5.055625 / 5.07 us。

原 Store 完成时的 promotion Decision：A branch/task 均 promoted=true；B branch promoted=true、task promoted=false。两个来源都是同一 repair 源，`parent_latency_ms=null`、classification=`correct_inconclusive`；这些采样差异不能写成已验证的源码优化收益。此项只确认同新 Runtime 下两条 Strategy 的可比较起点，尚不代表真实优化轮次完成。

本次完整 raw NPZ 已做 SHA/size 验证，correctness 数字经原 receipt 协议重新验证，但没有再次执行大型 NPZ 的独立逐值比较；原始 protobuf 也没有重新解析，仅复核已保存 parser 样本统计和 normalization。上述边界已写入 JSON，不与前面独立逐值重算的 Oracle 负例或旧 Runtime baseline 混淆。

## 增量复核：确认复测与只读核验辅助工具

在用户恢复TPU使用后，按requesting-code-review技能派发无会话历史的只读reviewer，检查 `prepare_confirmation_round.py`、`run_confirmation_round.py`、`verify_real_round.py`、`resume_active_round.py` 和相应测试。没有修改生产运行时。确认工具复用原Analyzer/Intake/Controller、原工件和固定预算；测量复本显式标识，不冒充新优化。reviewer未访问网络或真实Store。

发现并关闭一项Important：旧恢复脚本在检查原journal/intake之前构造Controller，异常缺journal时会先建立默认零花费预算。当前真实任务journal完整，没有发生该问题。修复在构造前核对DB存在、journal存在、intake已接纳、规范化原LIMITS一致。根线程以临时SQLite和旧脚本副本实际复现默认100 executions/2并发journal被创建的RED，再运行修复版本1项GREEN/exit0；首次测试代码括号错误另行修正，不算行为RED。reviewer只读复核并确认问题关闭，无新增阻断问题。

根线程确认复测辅助测试7/7，实际Python退出码0，374.456秒，见 [confirmation-tests-final.txt](../../../.artifacts/phase3-2026-11-12/confirmation-tests-final.txt) 和 [实际退出码](../../../.artifacts/phase3-2026-11-12/confirmation-test-result.json)。测试前一轮已报告OK，但PowerShell重定向包装返回1，因此重跑并捕获子进程真实退出码，没有把shell错误忽略为成功。只读核验器12/12、4.223秒、exit0，保留输入cache修改拒绝事实并区分未启动取消与真实执行；其数值/位等重新计算不宣称重新验证device输入未被修改。reviewer的独立测试启动被本机uv trampoline拒绝，故这次测试由根线程运行，不称独立重跑。

审查明确不裁定真实TPU收益、原XPlane protobuf独立重解析或Git历史干净；这些需要相应证据。仅两次独立Run/实现的ABBA复测也不等于大量独立样本或普遍性能证明。

## Runtime 3 重新准入辅助代码审查

2026-11-12，新的无会话历史reviewer只读检查Runtime绑定、重新准入、setup/probe与原prerequisite driver调用。发现Important：脚本原本按命令行标签选择输出文件，却始终读取当前allocation-intent；错误标签可能把新环境写入历史报告。修复在构造bridge前校验generation对应config及不可变观察绑定；每次更新观察文件保存原始字节快照。新增测试先观察缺失helper失败，再通过，专项5/5。reviewer复核关闭该问题，审查范围无其他已确认Critical/Important。

保留Minor：观察文件的read/check/replace不是多写者原子CAS；本地编排须串行。准入成本按原协议单独记录，不计入16次探索预算，不声称该探索预算是全部硬件花费上限。reviewer未运行测试、未连接TPU，不能据此宣布Runtime3就绪。根线程实际完成31项相关辅助验证，详情见 [测试事实与代码哈希](runtime3-preparation-tests.json)。新硬件准入、第二轮及重复性确认仍待完成。
