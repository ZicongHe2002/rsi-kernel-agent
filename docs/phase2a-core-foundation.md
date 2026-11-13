# Phase 2A 核心基础：数据与存储接口

本阶段提供共享契约、SQLite 实验记录和原始工件存储。它不执行 Kernel、不连接 Colab，也不包含 2B 的检索、2C 的 Analyzer 或 2D 的调度器。实施范围见[路线图](../knowledge-site/content/学习方法/RSI实践/rsi-phase2-roadmap.md)和[实现计划](superpowers/plans/2026-11-11-phase2a-core-foundation.md)。

## 文件与数据契约

- [contracts.py](../src/rsi_kernel_agent/contracts.py)：frozen dataclass、字段验证、规范 JSON 序列化；`record_to_dict` / `record_from_dict` 用于已知类型的往返转换。
- [artifacts.py](../src/rsi_kernel_agent/artifacts.py)：`ArtifactStore.put(bytes)`、`read(ref)`、`verify(ref)`、`path_for(ref)`。
- [store.py](../src/rsi_kernel_agent/store.py)：`ExperimentStore(db_path, artifact_root)` 及事务 API。

调用方生成稳定 ID 和幂等键；ID 区分大小写，长度 1–128，只能以字母或数字开头，其余字符限字母、数字、`.`、`_`、`:`、`-`。记录 ID 在整个数据库内唯一。幂等键是非空文本，最长 256 字符，在所有操作间共用命名空间。相同键和相同请求返回原结果；异值复用键或不可变记录 ID 引发冲突。内部生成的 Decision / snapshot ID 使用 UUID。

| dataclass | 字段（按构造顺序） |
|---|---|
| `CandidateIdentity` | `source_digest, config_digest, build_digest`，均为小写 SHA-256 |
| `ArtifactRef` | `digest, size`，内容哈希和非负字节数 |
| `Task` | `id, name, contract_revision` |
| `Workload` | `id, task_id, spec: dict` |
| `EvaluationContext` | `id, task_id, workload_id, environment: dict, protocol: dict, provenance` |
| `Strategy` | `id, task_id, name` |
| `Plan` | `id, strategy_id, description` |
| `ParentCandidate` | `id, task_id, identity, candidate_id=None` |
| `Candidate` | `id, plan_id, parent_id, identity, change_kind="proposal"`；另允许 `repair` |
| `Run` | `id, candidate_id, context_id` |
| `RunEvent` | `id, run_id, execution_status, scheduling_status, reason` |
| `Evidence` | `id, run_id, evidence_kind, availability, request_status, claim_status, value=None, unit=None, artifacts=(), input_ids=(), source={}` |
| `EvaluationBundle` | `id, run_id, correctness, timing_validity, latency_ms, confirmed, evidence_ids` |
| `ProfileBundle` | `id, run_id, evidence_ids, availability` |
| `AnalysisBundle` | `id, run_id, evidence_ids, profile_ids, claim_status, summary` |
| `Decision` | `id, task_id, kind, target_id, reason, details={}` |

`artifacts`、`input_ids`、`evidence_ids`、`profile_ids` 的 Python API 使用 tuple；序列化为 JSON 数组。字典字段使用有限 JSON 值，不接受 NaN、Infinity、非字符串键或任意 Python 对象。dataclass 的 frozen 属性不冻结嵌套字典；store 在写入前验证并复制 payload，之后修改调用方字典不改变已存记录。

`build_digest` 表示候选**声明的构建配方身份**，不宣称 deferred 候选已经编译。实际观测的编译器版本、已加载模块和编译产物身份由可信执行端写入 Evidence / 原始工件。`source_digest`、`config_digest` 同样是输入身份声明；2A 不扫描源码来证明其真实性。

`ParentCandidate(candidate_id=None)` 是显式导入的基线身份，不是普通 Candidate 的空父关系。若 `candidate_id` 非空，必须指向同 Task 中已存在的 Candidate，且 identity 完全一致。每个新 Candidate 都引用一个已存在的 ParentCandidate；修复使用新 Candidate 和 `change_kind="repair"`，原失败候选及 Run 保留。父候选可来自同 Task 的另一策略，当前候选仍归属其 Plan 的策略。

比较上下文是独立稳定 ID，包含 task / workload / environment / protocol / provenance；不包含候选 source/config/build 摘要。`provenance` 限 `synthetic`、`saved`、`real`，Evidence 的 `source["provenance"]` 必须相同。改变工作负载、环境、协议或来源类型，应登记新 context；即使字段恰好相同，不同 context ID 也不混排。当前通用层不解释 environment / protocol 字典中的具体 TPU 协议，真实执行端必须完整填充它们。

证据类别沿用[共用证据契约](../knowledge-site/content/学习方法/rsi-evidence-contract.md)。availability 为 `available / partial / unavailable`，request_status 为 `not_requested / requested / completed / failed`，claim_status 为 `hypothesis / supported / refuted / inconclusive`。`unavailable` 的 value 必须是 `None`（JSON null）；所有缺失值须在 `source["reason"]` 给出非空原因。未知不是零。Plan / action 不是 evidence_kind。

## SQLite schema 与关系

schema 版本为 1，存在 `PRAGMA user_version` 和每条 record 的 `schema_version` 中。初始化拒绝非空的无版本数据库，重开版本 1 时核对预期 schema 和不可变 trigger；较新版本及不相关/不匹配结构被拒绝。未来迁移需要显式实现，不会自动降级。不要把已有知识库或认证数据库传给 ExperimentStore。

| 表 | 内容与更新方式 |
|---|---|
| `records` | 不可变实体；全局 `seq`、唯一 `id`、`kind`、派生 `task_id / strategy_id`、规范 JSON `payload`、payload SHA-256 `digest`、`schema_version`、UTC `created_at` |
| `links` | `(record_id, field, target_id)` 类型化关系边；两端外键引用 records；不可变 |
| `requests` | 幂等键、operation + data 的 fingerprint、已提交返回值；不可变 |
| `best` | `(task_id, context_id, scope)` → EvaluationBundle 和 latency；scope 分 `task` 与 `strategy:<id>`，仅由晋升事务修改 |
| `frontier` | 独立的 `(task_id, context_id, candidate_id)` 探索集合，由明确 Decision 修改 |
| `snapshots` | 每 Task 递增版本、完整 body 与 SHA-256；不可变 |

`records` 的信封字段在数据库中，`get` / `history` 返回 dataclass payload，不返回信封。快照 body 另保存每项 `seq / kind / payload`。`created_at` 是本地数据库写入时间，不是远端测量时间。

外键核验存在性；应用层再核验目标类型、Task 归属和精确父身份。Run 引用同 Task 的 Candidate 与 context；Bundle 的 Evidence / Profile 必须属于同一 Run；派生 Evidence 的 `input_ids` 可引用同 context 的已有 Evidence。插入 Evidence 时验证其工件，拒绝缺失或损坏内容。`records / links / requests / snapshots` 的 UPDATE / DELETE 由 trigger 拒绝；直接操纵数据库文件或移除 trigger 不在信任模型内。

每次操作新建连接，启用外键、30 秒 busy timeout、FULL synchronous。WAL 下写操作由 `BEGIN IMMEDIATE` 串行化，验证、记录、投影和幂等结果一并提交；异常未提交事务回滚。适用于同一主机的本地 SQLite，不能推导出分布式或任意网络文件系统保证。

## Run 状态与写入 API

新 Run 的隐含状态是 `not_run / ready`，event_id 为 `None`。`transition(event, expected_event_id, key)` 验证当前末条事件，过期前置条件引发 `ConflictError`。同一 RunEvent ID 只能使用原幂等键重试，不能换键重放；新转换使用新事件 ID。直接 `append(RunEvent(...))` 被拒绝。

| 当前状态 execution / scheduling | 允许的下一状态 |
|---|---|
| `not_run / ready` | `not_run / deferred` 或 `running / active` |
| `not_run / deferred` | `not_run / ready` 或 `running / active` |
| `running / active` | `succeeded / finished`、`failed / finished`、`invalid / finished`、`incomplete / finished` |
| 任一 `finished` 终态 | 无；重试创建新 Run |

deferred 表示尚未运行的延期工作，不是 failed。incomplete 也是保留的终态；后续重试使用新 Run，不覆盖原尝试。`run_state(run_id)` 返回 execution_status、scheduling_status、event_id；`history(task_id)` 按 seq 返回全部该 Task 记录。

`append(record, key)` 写入普通实体及 note / paused / resumed / deferred Decision。后者是事实记录，不自动调度任务或实现策略状态卡。promotion / frontier Decision 只能由对应受约束 API 生成：

- `consider(evaluation_id, key)` 返回追加的 promotion Decision。它要求 Run 已 succeeded、正确性 passed、timing_validity valid、有限正 latency、confirmed，以及同 Run 的 available / completed / supported timing Evidence；该证据须有可验证原始工件、单位 `ms`、数值与 latency 相同，类别是 measured_timing、trace_derived 或 derived_measurement。晋升时重新校验该 Bundle 的 Evidence 及其全部上游 input_ids 链接的工件。无资格结果保留并记录原因。
- `best(task_id, context_id, strategy_id=None)` 返回任务最佳 EvaluationBundle；提供 strategy_id 时返回该分支最佳。严格更低 latency 才更新各自指针，相等或较慢不会替换；每个 context 独立。
- `set_frontier(candidate_id, context_id, included, reason, key)` 返回独立 Decision，显式添加/移除探索成员；`frontier(task_id, context_id)` 返回候选 ID。Frontier 可含非最佳候选，不影响 best。

EvaluationBundle 的 correctness / confirmed 是**可信生产者的声明**。2A 核验字段、关系、工件和晋升条件，不执行正确性测试、不解析原始 timing 来证明标量，也不取代 Benchmark。`best` 读取的是当时通过资格检查的历史结果，不会每次重读全部工件；后续工件损坏必须通过 ArtifactStore 校验识别，历史指针不会自动撤销。

## 快照与完整性

`create_snapshot(task_id, key)` 在写事务内固定该 Task 全部记录、其最大 seq（high_watermark）、record_count、每策略 strategy_counts，以及 best / frontier 投影，返回 ID、递增 version 和 digest。共享 Task / Workload / context 等记录没有策略归属，因此策略计数之和不必等于 record_count。后续新增记录不会改变旧快照。

`snapshot_page(snapshot_id, offset=0, limit=100)` 返回 records、total、offset、next_offset、snapshot 元数据和投影。只有 `offset == 0` 且本次返回覆盖全部记录时，`complete=True`。最后一页的 `next_offset=None` 不表示该单页包含完整历史；合并分页的调用方必须核对同 snapshot ID / digest、连续 offset 和总覆盖数。读取会核验 body digest、schema 和记录数。

当前 snapshot 将全部 body 存成 JSON，并在读页时完整载入内存；创建、history 和分页加载均为 O(history) 空间，不承诺无限历史规模。快照的完整性针对已提交元数据范围，不代表所有外部原始资料均已取得，也不是独立工件备份。

## 最小合成示例

从仓库根目录运行以下 Python。使用临时目录和明确 synthetic 来源，10 ms 是人为 fixture 值，没有进行任何 Kernel 测量。临时目录在示例结束时自动清理；实际使用应指定稳定本地目录。

```python
from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path.cwd() / "src"))
from rsi_kernel_agent.contracts import (
    Candidate, CandidateIdentity, EvaluationBundle, EvaluationContext, Evidence,
    ParentCandidate, Plan, Run, RunEvent, Strategy, Task, Workload,
)
from rsi_kernel_agent.store import ExperimentStore

def identity(label):
    return CandidateIdentity(*(sha256((label + part).encode()).hexdigest()
                               for part in ("source", "config", "build-recipe")))

with TemporaryDirectory() as directory:
    root = Path(directory)
    store = ExperimentStore(root / "experiments.sqlite3", root / "artifacts")
    for record in (
        Task("task", "Synthetic infrastructure example", "v1"),
        Workload("workload", "task", {"shape": [2, 2]}),
        EvaluationContext("context", "task", "workload",
                          {"device": "synthetic"}, {"clock": "fixture"}, "synthetic"),
        Strategy("A", "task", "Strategy A"),
        Plan("plan", "A", "Synthetic proposal"),
        ParentCandidate("baseline", "task", identity("baseline")),
        Candidate("candidate", "plan", "baseline", identity("candidate")),
        Run("run", "candidate", "context"),
    ):
        store.append(record, "append:" + record.id)
    store.transition(RunEvent("started", "run", "running", "active", "fixture"),
                     None, "event:started")
    store.transition(RunEvent("finished", "run", "succeeded", "finished", "fixture"),
                     "started", "event:finished")
    raw = store.artifacts.put(b'{"synthetic_latency_ms":10.0}')
    store.append(Evidence("timing", "run", "measured_timing", "available",
                          "completed", "supported", 10.0, "ms", (raw,), (),
                          {"provenance": "synthetic"}), "append:timing")
    store.append(EvaluationBundle("evaluation", "run", "passed", "valid", 10.0,
                                  True, ("timing",)), "append:evaluation")
    decision = store.consider("evaluation", "consider:evaluation")
    snapshot = store.create_snapshot("task", "snapshot:first")
    reopened = ExperimentStore(root / "experiments.sqlite3", root / "artifacts")
    page = reopened.snapshot_page(snapshot["id"], limit=snapshot["record_count"])
    assert decision.details["task_promoted"]
    assert reopened.best("task", "context").id == "evaluation"
    assert page["complete"] and len(page["records"]) == page["total"]
    print("Synthetic example: persisted records, best and complete snapshot verified")
```

完整本地测试命令（PowerShell，仓库根目录；不安装包）：

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -m unittest discover -s tests -v
```

测试模块自行把仓库 `src` 加入导入路径。实际通过数量、解释器版本、review 结果和外部未验证范围以本轮最终验证报告为准。

## 错误、工件与恢复边界

`ValidationError` 表示契约、序列化或工件校验错误；`StoreError` 表示关系、状态、scope 或数据库版本错误；其子类 `ConflictError` 表示不可变 ID / 幂等键 / expected event 冲突。底层 SQLite 锁超时、文件系统故障等仍可能抛出 `sqlite3.Error` / `OSError`，不是所有异常都包装成 StoreError。失败后保留原幂等键和请求，只有相同请求才能安全重试；请求变更使用新键。

工件路径由 `sha256/<前两位>/<完整digest>` 生成；写私有临时文件并 fsync，使用**同一文件系统的 hard link** 独占发布，已有内容验证后复用，绝不覆盖。文件系统必须支持此操作。工件根必须是可信本地目录；防护不覆盖恶意并发替换目录、管理员改文件或任意网络文件系统行为。

先发布工件再提交 metadata，因此数据库失败或进程中断可能留下无引用 blob / 临时文件；它们不自动删除。进程恢复测试和 SQLite FULL 同步不等于已验证操作系统断电后的端到端持久性保证。

备份时保留数据库与完整工件目录。可在暂停应用写入后使用 SQLite `Connection.backup()` 获取一致数据库副本，再复制工件树；或停止并关闭所有写入进程后，整体复制数据库、存在的 WAL / SHM 及工件树。不要在活动写入期间只复制主 `.sqlite3` 文件，也不要只保存 snapshot JSON 而漏掉原始工件。恢复到可信本地目录后重开 Store，并按需重新校验工件；目前没有独立备份/迁移命令或自动垃圾回收器。
