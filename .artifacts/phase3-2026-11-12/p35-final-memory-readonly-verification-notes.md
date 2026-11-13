# P3.5 最终 Memory / Best 只读核验入口

2026-11-13。以下是后续核验方法，不是最终 Gate 已通过的声明。

本次事实文件 `p35-runtime4-progress-facts.json` 于 `2026-11-13T10:44:00.741946+00:00` 完成读取，SQLite 使用 `mode=ro`、`PRAGMA query_only=ON` 与单一显式事务。其数据库视图为 835 records / high-watermark 860；最新已保存 Snapshot 仍是 version 11 / 818 records / high-watermark 843，Controller revision 146，预算 10 executions / 18000 声明秒。Snapshot 11 是丢失归档时点，尚不包含 Runtime 4 新 Context/Run，不能拿它查询新 Context 状态卡或声称覆盖新准入。

最终执行完成后，应由原 driver 保存正式完成 Snapshot。核验程序只消费该 Snapshot ID；不得为了验证临时调用 `create_snapshot`、`consider`、`select_parent`、`set_frontier` 或构造 `Controller`。

| 所需字段 | 原只读入口 | 语义与交叉检查 |
| --- | --- | --- |
| Branch Best / Task Best | `ReadOnlyStore.best(task_id, context_id, strategy_id)`；省略 strategy 为 Task Best | `src/rsi_kernel_agent/store.py:372`；对应 EvaluationBundle → Run → Candidate，强制 Run.context_id 与目标一致；对照该 Context 的 promotion Decision，不能跨 Runtime 排名 |
| Frontier | `ReadOnlyStore.frontier(task_id, context_id)` | `store.py:399`；与同 Context 的 frontier Decision 顺序及 Snapshot projection 核对；不得以 Best 代替 Frontier |
| Latest Attempt / Last Correct / Current Parent / paused / deferred | `MemoryReader(readonly_store).open_snapshot(snapshot_id).status_cards(context_id)` | `src/rsi_kernel_agent/memory.py:337`；原逻辑按 Run/Candidate 的记录顺序定义 Latest Attempt，不能改成最后更新事件；Last Correct 为该 Context 最新 correctness=passed Evaluation，不自动意味着 confirmed/最优；Current Parent 来自 parent_selection.v1 |
| 完整参与历史 | `view.histories((strategy_a, strategy_b))` | `memory.py:317`；必须 complete=true，保存 owned/dependency totals；不要仅用 filtered search 充当全历史 |
| Run 当前状态 | `ReadOnlyStore.run_state(run_id)` | `store.py:283`；未开始旧取消 job 仍可能是 not_run，应结合 archive journal reason 解读，不能伪造 RunEvent |
| 原 Controller 预算与终态 | `WorkflowJournal(readonly_store, task_id).load()` | 只调用 load，不调用 lock/save/step；预算与归档前 10/18000 加后续每个 reserved job 的固定 cost 相符，成本不能退款或跨库记账 |

可复用只读包装来自 `knowledge-site/manifests/phase3-2026-11-12/verify_runtime2_baselines_locally.py::ReadOnlyStore`（也由 `verify_real_round.py` 导出）。它不执行 `ExperimentStore.__init__` 的建库/WAL/schema 初始化，所有连接只读；ArtifactStore 亦是只读。

建议最终脚本先读取恢复优化轮及诊断的 `completed.json`、各自 immutable binding / selection、manifest 引用；验证 snapshot digest/metadata，然后通过上述原 MemoryReader 获取全卡片与完整历史。保存每个 Context 独立结果：Runtime 3 ABBA、Runtime 4 常规 workload、Runtime 4 独立 zero-length workload。将每个 Best 的 evaluation、run、candidate、parent、context 与原 promotion Decision 绑定，并调用现有 `verify_job` 核验相关五阶段回执/工件，`_parent_latency` 检查比较分母。原始 NPZ/保存 parser normalization 与独立 protobuf 解码属于不同验证范围，按实际执行分开记录。

如果 final Snapshot 与实时 Store 已有不同高水位，报告两者各自时间点；只在确认期间没有后续 Context 相关 mutation 时，才将其投影直接比较为同一状态。禁止用当前全局 latest Snapshot 的名称推测它已经包含目标 Context。
