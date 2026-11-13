# 独立 TRACE 模式诊断准备

2026-11-12。仅辅助脚本与本地检查；尚未 prepare、submit 或访问 Runtime。**scope: minimal JAX trace-mode capability only**。不创建正式 MLA Run、EvaluationBundle 或晋升，不修改现有 runner、固定 Context 或 Benchmark。

文件：`trace_modes_diagnostic.py` 是宿主入口，`trace_modes_remote.py` 是按 SHA256 固定的远端任务脚本。复用原 ColabBridge/ContentsClient、ColabRemote 工件下载和唯一 ExperimentStore/ArtifactStore。远端诊断目录是 worker spool 的传输状态，不是第二个实验数据库。

## 运行前置

根线程已核验并 bootstrap 新 Runtime worker，`colab-config-runtime2.json` 与 `environment-isolated-runtime2.json` 指向同一实测 runtime 和隔离 Python；环境未被修改。默认 Task 为 `mla-decode-v1`。诊断使用现有 `worker.coordinator/runtime.lock`，在锁内检查所有既有正式 operation intent 对应的有效完成回执。未知/损坏/未完成回执导致诊断 blocked_before_execution，不运行 JAX。可见设备进程归属检查不清楚时同样拒绝计算，不停止用户进程。

## 根线程确认后使用

```powershell
# 仅本地固定源码、环境与诊断 intent；不连远端。
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/phase3-2026-11-12/trace_modes_diagnostic.py' prepare
# 明确提交一次。重复同一请求只查原 dispatch 状态。
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/phase3-2026-11-12/trace_modes_diagnostic.py' submit
# 查询，不启动任何计算。
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/phase3-2026-11-12/trace_modes_diagnostic.py' poll
# 仅当 completed 时按原 Contents 下载并核验工件，写入原 ArtifactStore。
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/phase3-2026-11-12/trace_modes_diagnostic.py' collect
```

只有明确的 `submit` 会安装任务专属脚本并创建独立子进程。它不 bootstrap worker、不安装依赖、不更换 runtime。`poll` 不重新部署丢失脚本、不恢复丢失 spool、不重新启动 unknown。若配置、环境或脚本改变，原 request 文件拒绝覆盖，应先说明原诊断状态，再由宿主另行决定新的独立诊断 ID/归档位置，不能删除原 intent 来重跑。

## 实际探针与证据

在隔离解释器新进程中读取环境，逐字典匹配固定观测值；读取的 runner 仅用于原 `collect_environment`，不会调用 `run_mla_stage`。计算为 TPU 上 `float32[128,128]` 的 JIT `(x @ x) + x`，编译与两次 warmup 后，在各模式 trace 中同步执行 5 次，每次 `jax.block_until_ready`。四个模式按官方 JAX0.8.1 的 `ProfileOptions.advanced_configuration={'tpu_trace_mode': value}` 设置：

- `TRACE_ONLY_HOST`
- `TRACE_ONLY_XLA`
- `TRACE_COMPUTE`
- `TRACE_COMPUTE_AND_SYNC`

每个模式保留精确 options、API 成败、同步状态、完整原始 `.xplane.pb`、其他 profiler 输出、Python/native stdout/stderr、失败 traceback。对 PB 只做 plane/event-name/count 清单；不汇总嵌套事件时长、不推断 DMA、VMEM/MXU counter 或 critical path。如果 stop_trace 失败，余下模式明确 not_attempted，避免在不确定的 profiler 状态上继续。

receipt 中 `completed_modes` 表示诊断程序完成收集，不表示所有模式 API 成功、存在设备事件或能解析 MLA duration。实际支持需要分别检查每个 mode-result 及原 PB；TRACE_ONLY_HOST 无设备事件符合其模式含义。

**模式参数被 API 接受与模式语义实际生效分开报告。** `api_status=succeeded` 只表示 start/stop API 和该次同步计算成功；event inventory 是实际文件中看到的名字与数量，不能仅据此断言 collector 完整实现了该模式、更细粒度同步、DMA或任何硬件计数。判断模式语义还需跨模式实际事件对照及版本对应解释。

## 持久恢复边界

宿主先保存独立诊断 Decision intent 和脚本 ArtifactRef。远端先独占保存 request/dispatch-intent，再创建进程。若 launch 已写 intent 但确认丢失，poll 显示 unknown，不会二次启动。

进程持 diagnostic.lock 与原全局 runtime.lock。正式计算前在原 worker operations 目录创建**仅供隔离的诊断 guard intent**，字段显式为 `trace_mode_diagnostic_guard.v1`，不伪造正式 request/receipt。原 worker 遇到该 guard 而没有正式完成回执，会保守阻塞；因此诊断崩溃后的 OS 锁释放不会允许正式实验越过未知工作。

诊断完成时先发布独立 `trace_mode_diagnostic_receipt.v1`，绑定脚本、请求、runtime、task、全部工件摘要，再在同 runtime 锁内删除仅该诊断自己的 guard marker。永久执行 intent 和历史 receipt 保留。若在 receipt 后清理前崩溃，poll 核验 receipt/工件后可只补 guard 清理；若没有 receipt，guard 不会被自动清除。原 worker 的任何 intent/receipt 均不被改写。

接收回执和工件经原 ArtifactStore 核验后，保存 `trace_mode_diagnostic_collection.v1` Decision 与 `trace-mode-diagnostic-collected.json`。原 PB 是原始字节工件；不放入任何 MLA Run ProfileBundle。未知设备/进程干扰只作为观测边界，不宣称跨主机绝对独占。

## 本地验证

`test_trace_modes_aux.py` 使用临时目录和 mock，禁止 JAX/远端调用；另以真实原 WorkerSpool 在 CPU 临时目录同步调用 execute，验证未知诊断 guard 阻塞正式 worker，且模拟 runner 计数文件未出现。覆盖 persisted dispatch unknown 不重启、完成回执后的自有 guard 清理恢复、外来/摘要不匹配 guard 不删除、原正式 unknown 阻塞诊断、全局 runtime 锁与 guard 的先后关系、两脚本 AST。没有运行四模式真实 TPU 验证，也没有把本地检查记为 Trace mode 支持。
