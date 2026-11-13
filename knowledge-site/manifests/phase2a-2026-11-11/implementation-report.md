# Phase 2A 实现与验收报告

2026-11-11｜Phase 2A 已实现并完成本地验收，停在本阶段等待用户审查。未开始 2B、2C、2D 或 Phase 3。

路线图已先于代码改为 **2A 核心基础 → 2B Knowledge/Memory → 2C Profiler/Analyzer → 2D Workflow/Controller → 3 MLA/Colab 最终集成**。用户请求逐字节保存于 [sources/phase2-roadmap-adjustment.txt](sources/phase2-roadmap-adjustment.txt)，旧路线图与受改文档位于 `before/`；Phase 1 历史报告未改写。

## 交付与文件

| 文件 | 实际交付 |
|---|---|
| [contracts.py](../../../src/rsi_kernel_agent/contracts.py) | 14 种实体契约，以及 CandidateIdentity / ArtifactRef；严格字段、状态、有限 JSON、序列化验证 |
| [artifacts.py](../../../src/rsi_kernel_agent/artifacts.py) | SHA-256 内容寻址、长度/摘要核验、fsync 后独占发布、损坏拒绝覆盖 |
| [store.py](../../../src/rsi_kernel_agent/store.py) | SQLite 记录、关系、幂等、状态事件、晋升、独立 Frontier、版本化快照 |
| [包入口](../../../src/rsi_kernel_agent/__init__.py) | schema 版本与验证异常；不加载执行后端 |
| [test_contracts.py](../../../tests/test_contracts.py) / [test_artifacts.py](../../../tests/test_artifacts.py) | 10 项契约、7 项工件测试 |
| [test_store.py](../../../tests/test_store.py) / [test_acceptance.py](../../../tests/test_acceptance.py) | 13 项存储、15 项真实 SQLite / 多进程验收 |
| [实现指南](../../../docs/phase2a-core-foundation.md) | 完整字段/API、状态表、可运行合成示例、信任与备份边界 |
| [实施计划](../../../docs/superpowers/plans/2026-11-11-phase2a-core-foundation.md) | 实现前方案、任务和验收步骤 |
| [路线图](../../content/学习方法/RSI实践/rsi-phase2-roadmap.md) | 修正顺序及后续阶段门槛 |

同步更新工程索引、知识库文档索引、RSI 总览、共用证据契约状态、网站 README 和本周完成记录。全部具体路径、原文快照及最终 SHA-256 见 [changes.json](changes.json)。`.artifacts/phase2a-2026-11-11/` 的验证/文档维护脚本是本轮辅助工具，不是 RSI 运行时模块。

## Schema 与实现选择

采用现有 Python 标准库和独立 SQLite + 工件目录，无新增依赖。现有仓库没有可复用的 RSI 实验数据库；复用网站的 revision / SHA / 原子发布思想，没有将 Markdown 或认证数据库作为实验存储。知识库 MCP 接口复用留待 2B。

schema 版本 1：`records` 保存规范 JSON、稳定 ID、全局 seq、Task/Strategy 归属、摘要和写入时间；`links` 保存外键关系；`requests` 保存幂等响应；`best` 分开保存 task 与 strategy scope；`frontier` 是独立集合；`snapshots` 保存完整版本和摘要。记录、关系、请求、快照禁止 UPDATE/DELETE。目标类型、同 Task / Run / context 关系由事务内代码核验。

每次操作独立连接，WAL、FULL synchronous、30 秒 busy timeout、`BEGIN IMMEDIATE`。同时首次打开空数据库的 WAL 锁竞争也有有时限的重试。未知/不匹配 schema 拒绝，不能静默初始化不相关数据库。

Candidate 的 source/config/build 身份不参与共同比较 context。`build_digest` 是声明的构建配方摘要，实际编译/已加载源码身份由后续可信执行端提供证据。导入基线是显式 ParentCandidate；其他父记录必须引用已有同 Task Candidate 且身份完全相等。

Run 从 `not_run/ready` 经追加事件到 deferred 或 running，再进入 succeeded/failed/invalid/incomplete。延期不是失败；终态重试使用新 Run。CAS 拒绝过期前置事件和换键重用旧事件 ID；相同请求键安全返回原结果。修复使用有精确父关系的新 Candidate。

晋升要求成功 Run、correctness passed、有效有限正 timing、confirmed，以及同 Run、同 context 的绑定计时证据和已核验工件。重新检查全部上游证据工件；只有严格较低延迟才更新各自 scope，较慢/无资格 Decision 与原尝试保留。Frontier 不影响 best。

快照在同一事务固定全部任务历史、各策略计数、high-water mark、投影与 digest。部分分页或单独末页不标 complete；后续写入不改变旧版本。完整性仅表示已提交元数据范围完整，不代表远端资料已经采集。

## 验证与输出

现有解释器：CPython **3.13.14**，SQLite **3.53.1**；未安装或升级环境。仓库根运行：

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -m unittest discover -s tests -v
```

最终输出：

```text
Ran 45 tests in 5.562s
OK (skipped=1)
```

即 **44 通过、1 跳过、0 失败/错误**；15 项验收全部通过。完整输出见 [tests.txt](tests.txt)，解释器和命令见 [test-environment.json](test-environment.json)。跳过项是 Windows symlink 创建权限不足（WinError 1314）；Windows junction 拒绝、路径逃逸、并发发布等其他工件测试通过。未放宽系统权限来运行该项。

使用现有解释器的沙箱启动最初遇到 uv trampoline 权限限制，随后通过受限范围的自动审批运行同一解释器；没有依赖安装、环境升级或认证改动。

| 验收条件 | 已运行的测试证据 |
|---|---|
| A/B 共存；每策略多 Plan、每 Plan 多 Candidate、精确父及 repair | `test_many_branches_plans_candidates_and_repair_keep_exact_lineage` |
| 多 Run；failed、invalid、deferred、incomplete 与重试保留 | `test_multiple_attempts_preserve_fail_invalid_defer_incomplete_and_retry` |
| 原始工件与 metadata 绑定、缺失/损坏拒绝 | `test_artifact_binding_rejects_missing_or_corrupt_raw_bytes_without_partial_records` |
| Branch Best / Task Best / Frontier 分离；单调晋升；不同 context 不覆盖 | `test_best_per_branch_task_and_frontier_are_independent_and_monotonic`、`test_comparison_contexts_cannot_replace_each_others_best` |
| 同键并发只提交一次、异值冲突、竞争晋升与状态 CAS | 4 项 `test_concurrent_*` 多进程测试，以及首次打开数据库测试 |
| 快照覆盖、分页诚实、版本隔离、投影冻结 | 两项 `test_snapshot_*` 验收及存储快照测试 |
| 独立解释器重启恢复、未提交事务进程退出回滚 | `test_fresh_process_recovers_records_best_artifacts_and_snapshot`、`test_process_death_rolls_back_uncommitted_record_and_remains_writable` |

先写契约/工件/存储/验收测试并观察缺失实现的 RED，再实现；审查发现均追加实际失败回归后修复。初次整合还修正了测试自身的 SQLite 连接未关闭和原始 INSERT 漏摘要字段问题。合成示例实际运行成功；10 份 Markdown 的 88 个相对链接有效，结果见 [links.json](links.json)。链接扫描排除代码块及行内代码中的示例。网站运行时代码未改，未重跑无关的账号/HTTP 测试或外部 Kernel 套件。

本周完成记录已补充；本地 revision API 返回 401，未读取认证文件，改用原始字节快照与写前 SHA 检查、重新读取合并后原子替换，保留已有安排和其他同时新增记录，不勾选无关待办。

独立 reviewer 无会话历史审查源代码、测试和指南，复现并确认关闭三项 Important：旧事件 ID 假成功、派生证据上游损坏漏检、空数据库首次 WAL 初始化锁竞争。复核 5 项专项测试通过；额外 30 轮 × 8 同时初始化从修复前 63 次锁错误降为 0。文档检查另外发现的版本 1 不相关 schema 接受问题也经失败回归修复。最终无未关闭 Critical / Important / Minor；详见 [review.md](review.md)。

## 剩余边界与停止点

- correctness / confirmed 和输入摘要来自可信生产者；2A 不执行 Benchmark、不证明原始数值真实，也不验证已加载 Kernel 身份。所有测试是 synthetic/CPU 基础设施测试，**没有真实 Kernel 性能或 TPU 验证**。
- SQLite 写入在单机串行化；不保证分布式文件系统行为。raw artifact 发布需要本地 hard link 支持；根目录需可信。进程恢复通过不等于已验证断电持久性。
- DB 失败或进程中断可留下无引用 blob / 临时文件；未实现自动垃圾回收。历史 best 不因事后工件损坏自动撤销；后续晋升会重新校验。
- 当前快照与 history 完整加载 JSON，空间随历史量增长；尚未做大规模性能测试。备份需同时保存一致数据库及工件树，不能只复制活动 WAL 数据库主文件或 snapshot JSON。
- Git CLI 不可用；只确认 `main` / HEAD `260de63fb57f5f494326936cdfbead188e10df01` 的现有元数据，不声称工作树干净；未创建提交。

没有对两个外部仓库执行 Kernel/CLI 命令或写入；没有运行 TPU、修改 `.auth`、账号权限、CLI 配置或全局 Codex 设置。Phase 2A 报告等待用户审查，后续阶段保持未开始。
