# Runtime 2 辅助编排审查

2026-11-12；仅本地源码审查和 CPU fixture 验证，未运行 admission/driver、未访问远端、未修改生产模块或真实实验库。

审查对象：`admit_runtime2.py`、`advance_runtime2.py`、`run_oracle_negatives.py`。

## 结论

新 EvaluationContext 的 ID 绑定旧 Context ID 与新实测 environment；新 Run ID 绑定原 Candidate 和新 Context。原 Candidate/source/config/build 保留，不改写旧 Run 或 Evidence。正式与 Oracle-negative 工作负载继续分开。B 创建自己的 Strategy/Plan/Candidate/Run，精确父指向已修复 Baseline Candidate，采用相同源码工件并独立执行，符合现有 Store/执行协议；B 不是 A 的结果重命名。

负例 driver 的阶段固定为 generate/compile/correctness，无 benchmark/profile。正常 failed correctness 才能被归为 incorrect；提前 compile/generate 失败不算 Oracle 负例通过。新 runtime driver 先完成两个负例，再处理 A/B baseline。

恢复沿用稳定 operation ID、原 ColabBackend 的持久 request/receipt。lookup unknown 不调用 submit；只有 definitely_not_started 才调用原请求。阶段完成回执可本地重新读取，最终归档按原 evaluator intent 幂等。

## 已修复

1. 原 admission 静默复用已有 runtime2 config，且可覆盖同名 job 恢复文件。现在核对新实测 runtime 与固定 spool，nonce 必须独立；环境与 job 采用 fsync 后独占发布，已有异值拒绝。同一环境的相同 admission 可幂等运行。
2. 原负例意外 passed 会直接退出，Run 留 running 且没有持久 Gate failure。现在由辅助 `oracle_gate_aux.py` 保存 `oracle_gate_failure.v1`、全部阶段 receipt/raw correctness refs，再追加 `incomplete/finished` 事件。保留真实 correctness=passed 的事实，同时明确它违反已知错误负例预期；不创建 EvaluationBundle，不调用 consider，不伪称 incorrect/cancelled。Decision 后崩溃可用原前置事件幂等补终态，恢复入口见 Gate failure 即停止。
3. 两个 driver 使用原 `WorkflowJournal.lock()` 同 Task OS 锁，避免并发主机控制交错。它不创建 Controller journal 或修改其预算。
4. 行政关闭/runtime-lost 的终态 Run 若没有原 evaluator intent，driver 拒绝恢复执行；已有 evaluator intent 的归档重放仍允许。

`integrate_result(cancelled=True)` 未用于意外通过，因为它会生成 cancelled 分类并经过 consider；辅助代码仅调用 `_verified_prefix(..., cancelled=True)` 验证成功的部分回执，没有走取消或晋升路径。

## 验证

辅助 `test_runtime2_aux.py`：6/6 通过，1.442 秒，退出码 0。前四项先观察缺失保护函数失败，后修复通过；终态恢复拒绝与 AST 检查后补通过，不声称它们经历缺失实现 RED。

覆盖：已有配置 runtime 不匹配拒绝；同名 job 不覆盖；意外 passed 正确归档 incomplete、无 EvaluationBundle/consider；Gate Decision→终态中断恢复；行政关闭 Run 拒绝续跑；三个入口及辅助文件语法和负例阶段边界。

只读 SQLite `mode=ro` 额外核对旧 runtime：attention negative 有 running 后 runtime-lost/incomplete 事件，operation intents=0、execution receipts=0；cache negative 无 RunEvent，operation intents=0、execution receipts=0。没有把这些旧 Run 写成硬件执行失败。本次未执行任何真实新 runtime 工作。

## 仍需由宿主保证

- runtime2 config 的 session 名称和 isolated remote_python 路径对应新 runtime 实际连接；脚本不安装环境、不替代设备发现。
- admission 只在新 environment 已实际采集后执行；本地测试不代表新 TPU 可用。
- `unknown` 状态继续保留并查询，不会自动认定失败或重复实验；长期断线由明确证据和宿主停止决定处理。
- B 首次测量的 parent_evaluation_id=None，因此可能为 correct_inconclusive；这是独立基线建立，不能当成优化加速。
- 辅助 prerequisite reservation 是已有设计的独立持久记录，不计入后续 Controller 的不可变预算；报告应单列其实际执行成本。
