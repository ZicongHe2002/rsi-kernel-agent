# Phase 2A Core Foundation Implementation Plan

> **For agentic workers:** 已获用户批准执行 2A；按测试先行、原生并行子任务与独立审查实施，不等待额外实现审批。缺失的 superpowers 执行技能不安装。仅完成本计划，报告后停止，不开始 2B–2D 或 Phase 3。

**Goal:** 为多策略、多 Plan、多 Candidate 和多 Run 建立可恢复、可核验、保留完整历史的最小持久基础。

**Architecture:** Python 标准库 dataclass 定义版本化契约；SQLite 保存不可变规范 JSON 记录、类型化关系、状态事件和事务更新的最佳/Frontier 投影；独立内容寻址目录保存原始工件。每次操作使用独立连接，WAL、外键、FULL 同步、busy timeout 和 `BEGIN IMMEDIATE` 支持同一主机上的并发写入。

**Tech Stack:** 现有 Python 解释器、stdlib `dataclasses` / `sqlite3` / `hashlib` / `json` / `unittest`。不安装包，不依赖 JAX、TPU 或 Colab。

**Spec:** [用户 Phase 2 调整要求](../../../knowledge-site/manifests/phase2a-2026-11-11/sources/phase2-roadmap-adjustment.txt)、[已修正路线图](../../../knowledge-site/content/学习方法/RSI实践/rsi-phase2-roadmap.md)、[共用证据契约](../../../knowledge-site/content/学习方法/rsi-evidence-contract.md)、[Memory 语义](../../../knowledge-site/content/学习方法/rsi-memory.md)。

## Global Constraints

- 只实施 2A；不实现知识检索、Analyzer、调度器、真实 Kernel Adapter 或 Colab 后端。
- 两个外部仓库保持只读，不执行 Kernel / Colab CLI / TPU，不处理 `.auth`、账号权限或全局 Codex 设置。
- 不安装 Skill、依赖或升级 runtime；不创建空占位模块。
- 所有测试是 RSI 基础设施的软件测试；synthetic provenance 必须显式，不能称作 TPU 性能验证。
- failed、invalid、slower、repaired、deferred、incomplete 均保留；普通 Candidate 必须有确切 ParentCandidate。
- Phase 1 历史报告/清单不重写；当前变更另存快照和报告。未请求 Git 提交，且当前 Git CLI 不可用，不声称工作树干净。

## Review Focus

1. 并发同键写入、同键异值和事务中断：只保留一个相同请求结果，冲突显式拒绝，不产生半条关系。由 Task 2 的多进程、回滚和重启测试覆盖。
2. Candidate 身份与比较上下文混淆：source/config/build 摘要不进入共享 context；跨 task/workload/context 绑定拒绝。由 Task 1 / 2 的契约与关系测试覆盖。
3. 异常计时或不完整证据晋升：NaN、无穷、非正值、错误/未确认/未成功/错误绑定不得更新最佳；较差记录仍可保留。由 Task 2 的晋升测试覆盖。
4. 截断历史被称完整：快照在一致事务中固定 high-water mark、记录清单、策略计数和 digest，部分分页明确不完整。由 Task 2 的快照及新增记录隔离测试覆盖。
5. 工件发布失败、路径逃逸或内容损坏：哈希校验、独立目录和不覆盖发布；元数据仅接受已验证工件。由 Task 1 / 2 的工件与存储集成测试覆盖。

## 仓库检查与复用边界

现有实现主要是 `knowledge-site`，没有可直接复用的 RSI 实验数据库或根级 RSI Python 包。沿用网站 revision / SHA 校验和原子发布思路，不将 Markdown 文档存储或认证 SQLite 用作实验数据库。知识库 MCP 的实际 search / fetch / create / update 检查留待 2B。

根 `AGENTS.md` 要求已证实的相关成果同步到本周完成记录。当前 Git 元数据指向 `main` 的 `260de63fb57f5f494326936cdfbead188e10df01`，Git CLI 不在可用 PATH；仅记录已能检查的信息，不推断无未提交变动。

## Task 1: 数据契约与原始工件

**Files:** 创建 `src/rsi_kernel_agent/contracts.py`、`artifacts.py`、`__init__.py`；测试 `tests/test_contracts.py`、`tests/test_artifacts.py`。包入口导出实际接口，不新增空架构模块。

**Contracts:** Task、Workload、EvaluationContext、Strategy、Plan、ParentCandidate、Candidate、Run、RunEvent、Evidence、EvaluationBundle、ProfileBundle、AnalysisBundle、Decision。记录有稳定 ID、schema version 与明确关系；规范 JSON 禁止非有限数字。EvaluationContext 含 task / workload / environment / protocol / provenance，不包含候选 source/config/build digest。ParentCandidate 显式登记导入基线，或指向已有确切 Candidate；Candidate 分别保存 source/config/build SHA-256 和 proposal / repair 归属。

**Artifact interface:** `ArtifactStore` 将字节按 SHA-256 保存，返回可验证引用；读取或 metadata 绑定前核验哈希/长度。发布先于 metadata，数据库失败可留下无引用 blob，保留而不自动删除；不能留下指向部分文件的已提交 metadata。

- [x] 写契约失败测试：非法字段/关系值、非有限数字、错误摘要、synthetic 标记、导入父身份与普通父关系；观察缺失实现导致的预期失败。
- [x] 实现最小契约与规范序列化，验证上列测试通过。
- [x] 写工件失败测试：重试幂等、字节损坏、路径逃逸、并发同内容发布和失败残留；观察预期失败。
- [x] 实现工件发布和校验，运行相关测试；由独立审查检查与存储接口的一致性。

## Task 2: 追加实验存储、受约束投影与快照

**Files:** 创建 `src/rsi_kernel_agent/store.py`；测试 `tests/test_store.py`、`tests/test_acceptance.py`。

**Schema:** schema version 1；不可变规范 JSON records 与 typed references / foreign keys；幂等请求；追加 RunEvent / Decision；branch_best 与 task_best 分别按 context 定位；Frontier 独立集合；不可变 versioned snapshots。状态事件携带 expected previous event 做 compare-and-swap，避免两个 writer 覆盖彼此状态。

**Interfaces:**

- `append(record, key)` / `get(id)` / `history(task_id)`：写入不可变类型化记录、核验引用和工件，读回完整任务历史。相同键和请求返回原 ID，不同请求显式冲突。
- `transition(event, expected_event_id, key)` / `run_state(run_id)`：追加合法 Run 状态转换，拒绝非法边和过期前置状态；不包含执行队列或重试调度。
- `consider(evaluation_id, key)` / `best(task_id, context_id, strategy_id=None)`：由已成功 Run、正确性通过、有效有限正 timing、confirmed 和绑定一致的证据决定资格；严格较低延迟才更新相同 context 的 branch/task 指针。accepted/rejected Decision 均追加保留。
- `set_frontier(candidate_id, context_id, included, reason, key)` / `frontier(...)`：明确维护探索集合和 Decision，独立于最佳结果。
- `create_snapshot(task_id, key)` / `snapshot_page(snapshot_id, offset, limit)`：同一事务固定记录 ID、高水位、每策略计数、digest 和 best/frontier；读取分页时只有完整覆盖才声明 complete。快照之后追加记录不改变旧快照。

- [x] 写存储失败测试：跨类型引用、跨 task/context 关系、无效父关系、A/B 共存、每策略多个 Plan、每 Plan 多候选、每候选多 Run、原始工件链接；先观察预期失败。
- [x] 实现 schema、事务和关系校验，运行契约/工件/存储测试。
- [x] 写状态与幂等失败测试：failed / invalid / deferred / incomplete 保留、repair 新候选保留旧父、非法状态转换、同键异值与 stale event 拒绝；观察失败再实现。
- [x] 写晋升与 Frontier 失败测试：不同分支最佳独立、任务最佳可来自另一策略、较差/不可比/错误/未确认/非有限/错误证据结果不覆盖，探索集合可含非最佳；观察失败再实现。
- [x] 写快照失败测试：完整记录数量和 hash 一致、分页不误报完整、后续追加不改旧快照、每个参与策略历史都有覆盖；观察失败再实现。
- [x] 写数据库集成失败测试：多进程并发同键/冲突键、事务失败回滚、进程重启读回；观察失败再实现和验证。

## Task 3: 完整验收、独立审查与实现报告

**Files:** 本阶段报告位于 `knowledge-site/manifests/phase2a-2026-11-11/` 或同级清晰命名的 Phase 2A 报告；只将已验证结果加入 `knowledge-site/content/待办清单/一周待办 · 2026-11-09.md`。

- [x] 运行完整根级 RSI CPU 单元与数据库集成测试；记录确切命令、解释器、计数和退出码。
- [x] 运行受影响的现有网站测试或适当链接/内容检查；不运行外部 Kernel 仓库测试。
- [x] 向无会话历史的独立 reviewer 提供实现文件、规范和测试输出；修复 Critical / Important 问题，新增有意义的失败回归后复跑。
- [x] 核对验收矩阵：A/B 共存、多 Plan/候选/Run、精确父、失败/延期保存、工件链接、独立 best、单调晋升、诚实完整性、重启恢复全部有测试证据。
- [x] 报告确切文件、schema/API、已运行测试、未验证的外部集成和 SQLite 单机/工件备份限制；按 revision 或原始 hash 合并周记录。
- [x] 停止在 2A，提交报告等待审查；不创建 2B、2C、2D 空模块，不接入真实 MLA / Colab。

验证命令使用现有 `knowledge-site/.venv-mcp/Scripts/python.exe`，通过 `PYTHONPATH=src` 运行 `-B -m unittest discover -s tests -v`；不依赖安装包。最终报告记录实际 PowerShell 命令及所有偏离计划的必要调整，计划中的复选框只按已验证结果勾选。
