# Phase 3 Colab 连接审计

2026-11-12，持续更新。当前已有 **Source audit / Real Colab connectivity / Real TPU execution** 证据：专用 `rsi-p3-20261112` Runtime 已分配，两次最小 JAX 运算均同步完成并返回预期数值。它们验证真实连接和设备计算，不代表 MLA Baseline、正式 Benchmark 或 XPlane capture 已验收。

## 现有连接方式

使用 `C:\Users\fengluo\Desktop\colab\google-colab-cli`，指导文档为上级目录 `WINDOWS-COLAB.md` 和仓库 README。原桥接通过 Colab 控制面分配远端 Runtime，取得原认证代理后连接远端 Jupyter kernel，`execute_code` 发送 Python，`ContentsClient` 上传/下载文件。TPU 挂载在 Colab 远端 VM；本地 Windows 负责源码、编排、SQLite 和归档。它不是让 TPU 运行在本地 Windows 的 Local Runtime 方案。

已阅读原 `commands/session.py`、`commands/execution.py`、`client.py`、`runtime.py`、`contents.py`、`common.py` 和状态模型。原 `Client.assign` 先 GET，同一个 notebook UUID 已有分配则返回；需要时 POST。原源码有超时重查逻辑，但 CLI `new` 的 UUID 未先写入持久请求，因此本阶段辅助分配流程先保存 UUID，再调用同一个原 Client GET/POST，最后按原 SessionState/store/history 注册。没有改原桥接或创建替代认证方案。

## 分配与真实设备观察

- 现有解释器与桥接可导入并访问控制面。多次 `list_assignments` 成功，起初全部为空。
- 第一次原 `session.new(..., tpu='v6e1')` 60 秒内未确认，ReadTimeout；随后 GET assignment list 仍空。
- 改用持久 notebook ID，GET 可完成，POST 在 60/90/120 秒界限出现 ReadTimeout。按同 ID GET 暂无 Runtime；不把超时认定执行失败或已分配。
- 用户随后明确要求持续申请、必须 TPU v6e。辅助循环每轮先 GET 同请求与全部活动分配，再提交同 ID；轮间 30 秒。2026-11-12 02:55:28 UTC（上海时间 10:55:28）确认分配成功并停止申请，session 为 `rsi-p3-20261112`，runtime_id 为 `tpu-v6e1-s-euw4a0-xyzyx7kofigt`。脱敏状态在 `.artifacts/phase3-2026-11-12/allocation-attempts.jsonl`，意图在 `allocation-intent.json`。
- 默认远端进程实读设备名称 `TPU v6 lite`，1 个 TPU device，coords `[0,0,0]`、core_on_chip `0`、process_count `1`。`v6e1` 是实际申请/分配类型；设备自己的名称原样保留，不改写为推测规格。
- 本阶段尚未执行登录流程，没有读取认证文件、Cookie、token 或私钥，没有变更账号权限和 CLI 设置。原桥接内部按既有机制处理认证；其秘密不进入报告或工具输出。

本页不是实时状态服务；上述为已归档时点事实。后续 Runtime 是否仍在线以桥接查询为准。

## 已完成的最小同步运算

| 探针 | 实际执行与结果 | 读取的环境 |
|---|---|---|
| 默认 Runtime | float32 `arange(16).reshape(4,4)` 的 `x @ x.T`；等待 `(x,y)` 全部完成；4×4 输出逐项等于脚本固定预期，returncode=0 | Python 3.13.18、JAX/jaxlib 0.11.2、libtpu 0.0.50；TensorFlow/xprof 不存在 |
| 隔离环境 | float32 `arange(8)` 的平方和，等待 `(x,y)` 全部完成；结果 `140.0`，returncode=0；另成功导入 XPlane protobuf 并序列化空 XSpace | Python 3.11.17、JAX/jaxlib 0.8.1、libtpu 0.0.30、TensorFlow 2.20.0、xprof 2.22.1 |

原始 stdout/stderr、返回码分别保存在 [connectivity-probe.json](../../knowledge-site/manifests/phase3-2026-11-12/connectivity-probe.json) 和 [isolated-probe.json](../../knowledge-site/manifests/phase3-2026-11-12/isolated-probe.json)。环境原文见 [environment.json](../../knowledge-site/manifests/phase3-2026-11-12/environment.json)、[environment-isolated.json](../../knowledge-site/manifests/phase3-2026-11-12/environment-isolated.json)。transparent hugepages 的 JAX 警告保留；没有执行警告建议的 sudo/系统设置修改。

隔离依赖通过历史 Colab 采用过的 uv 方法安装到 `/content/rsi-kernel-agent/env-20261112/`，Python、venv、uv cache/bootstrap 都位于该目录。实际安装结果见 [isolated-environment-setup.json](../../knowledge-site/manifests/phase3-2026-11-12/isolated-environment-setup.json)。它没有替换 Colab 默认环境或本地项目/bridge 环境；具体版本以实读结果为准，不以 PallasKernels 的声明代替。

空 XSpace 序列化只证明解析依赖可导入，尚不证明采集、目标 program 识别或真实 Profile 归一化成功。

## 原 Store 归档与真实性边界

两次已完成探针已由只读外部文件、只写原 Store 的[归档脚本](../../.artifacts/phase3-2026-11-12/archive_connectivity_probes.py)导入 `.artifacts/phase3-2026-11-12/experiments.sqlite3` 和原 `artifacts/`。专用 Task 为 `p3-connectivity-20261112`，各自拥有 Workload、context、Strategy、Plan、显式导入父、Candidate、Run 和 Evidence；两个 Run 均据已收到的同步返回标 succeeded。它们共 25 条追加记录，没有 EvaluationBundle、Benchmark、Best、Frontier 或 consider 调用。快照和全部 ArtifactRef 见 [probe-evidence.json](../../knowledge-site/manifests/phase3-2026-11-12/probe-evidence.json)。二次归档保持同 25 条记录、同 snapshot，未再次调用远端。

这是对既有真实结果的事后导入。RunEvent 的 reason 明确写明它们不是执行前持久化的原事件，两个探针都没有远端 operation ledger，不能将本地 import ID 伪称远端 operation_id 或用它们证明断线后恰好一次恢复。

默认探针事先保存了 source_digest；归档脚本从已保存主控源码静态提取实际远端脚本，代入 Runtime ID，验证 SHA256 一致后保存。隔离探针未记录完整远端 payload 摘要，且当时 f-string 嵌入 runner 字节；其 Candidate 身份明确表示已保存的本地主控 recipe，remote loaded-source attestation 标 unavailable，未将当前 runner 字节冒充当时执行身份。正式 MLA worker 需要另行提供完整加载身份与持久回执。

## 复用边界与实现

新增 `workflow/colab_bridge.py` 在原桥接的已有 Python 中调用其 State、ColabRuntime、ContentsClient。每次校验目标 session.endpoint 等于固定 runtime_id；禁止静默接到另一个 Runtime。跳过 CLI 回调的自动更新检查和 DEBUG logging，不改全局设置，也不重新实现 HTTP/WebSocket/auth。

原 exec 的进程退出码不能独自证明代码成功：返回输出中的 error 必须拒绝，控制输出须恰有一个完整 `RSI_RESULT` JSON。本地超时只结束本地等待，远端可能继续，返回 unknown。`runtime.stop(shutdown_kernel=False)` 仅关闭连接，不中断用户 Kernel。ContentsClient 没有自己的 timeout，因此外层独立进程有时限；下载用 partial 文件再替换，并校验最终长度/SHA256。

原始 Candidate、依赖、固定 Oracle/runner、config 在本地 ArtifactStore；远端只保存独立源码工作区与执行 spool。remote operation ID/request_digest/nonce 与环境固定。恢复依赖 worker 已持久化的状态：completed 取回，不重执行；running 继续查询；可证明未开始才提交；Runtime/ledger 丢失或执行结果未定则 unknown。

显式 bootstrap 安装固定摘要工具和 ledger，常规 lookup 不初始化丢失 ledger。真实资源锁覆盖同 Runtime 下全部合作 Task 的 generate/compile/correctness/benchmark/profile；用户 Notebook 的并发占用另需观察，合作锁不能证明硬件物理独占。不会停止用户的其他任务。

## 后续验证边界

真实设备、环境信息、最小同步运算和本地证据归档已完成。直接 probe 没有可靠远端 ledger，正式 worker 的 completed receipt 回取/断线恢复需用它自己的操作证据证明，不能追溯赋予探针未记录的保证。MLA Baseline、固定 Oracle、正式 Benchmark、Profile 及两轮优化分别按后续 Gate 验收，本页不提前声明通过。
