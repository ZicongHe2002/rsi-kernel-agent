# 真实轮次辅助脚本复核说明

2026-11-12。此处是本地 host 辅助工具，复用原 Store、Analyzer、Intake、Controller 和 ColabBackend。编写与本地测试期间没有调用真实 Runtime，没有向生产实验库发布优化 Plan，也没有生成模型提案。

## 文件和调用顺序

- `real_round_aux.py`：共同 Gate 检查、已准备 round/session 的恢复、前轮完成与新历史覆盖检查。
- `prepare_real_round.py`：准备或恢复原 Analyzer 会话。读取 `packet.json` 后仍须当前对话模型提交响应。
- `run_real_round.py`：读取已完成 `session.json` / `result.json` 和人工提供的 variants，调用原 publish → intake → Controller。默认只调用一次 `step()`；可用同样参数继续查询和恢复。
- `test_real_round_aux.py`：临时 SQLite / 原 fixtures 的本地软件测试。

入口需在根代理完成独立复核并确认实际 Gate 后调用。下面命令仅说明用法，本轮未执行：

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/phase3-2026-11-12/prepare_real_round.py' 1
# 当前会话读取并完成 Analyzer 交互，手工构造与最终 PlanSet 标签匹配的 variant 工件引用。
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/phase3-2026-11-12/run_real_round.py' 1 --variants '.artifacts/phase3-2026-11-12/real-round-1/variants.json' --steps 1
```

第二轮将参数改为 `2`，但必须先完成精确的 `real-runtime2-round:1` intake 下全部尝试。新的 Memory 历史必须包含这些 Run 与 `workflow_result.v1` 记录。脚本不替代人工判断新实验如何改变第二轮模型提案。

## Gate 与恢复依据

旧 prepare 使用 result JSON 摘要中的 `assert`。新共同检查从 Store 取精确 `workflow_result.v1`，按 job/run/candidate 验证绑定，再通过原 `_verified_prefix` 检查每条 receipt 和原始工件。Baseline 需要完整五阶段成功、correctness passed、有效 confirmed timing、available Profile。两个已知错误输出的负例必须对应原 admission 与明确变更响应，并在 correctness 失败；它们不能包含 benchmark/profile。

两个 baseline 必须位于同一个正式 Context、不同策略，四个 Gate Run 的 Task、实际环境及固定协议相同。Negative 可使用原有完整矩阵的未改子集，不能改变对应 case 内容或容差。此检查只能验证已保存可信生产者证据；不能仅凭本地测试证明 TPU 已执行。

已完成的 prepared round 从原 Decision、manifest 和 snapshot 恢复，不再搜索或读取当前 Knowledge revision。首次准备的 Knowledge 搜索结果和引用版本先保存为不可变输入；中断后继续同一输入。已有 session 必须匹配原 manifest；完成结果必须匹配 session digest、原工件及交换历史。实际发布仍由原 Analyzer 重放验证。

Variants 和 publication key 在首次发布前固定到 `execution-input.json`；相同请求可重放，异值拒绝。Controller 保留原 journal、operation IDs、预算与回执。unknown 仍 blocked；辅助循环退出后可查询原 operation，不能分配新 Run 绕过未知状态。

## 本地测试与边界

最初 7 项测试在缺少共同辅助模块时失败；首次实现后发现测试错误地假定 profile_failed 仍 confirmed，实际原 Evaluator 为 valid timing / confirmed false，已按真实契约修正测试。之后 10 项全部通过（42.769 秒）；新增 driver 恢复测试单独通过（29.020 秒）。它使用原 publish/intake/Controller 和 SyntheticBackend，验证一次 step 后重启至完成、再次重启，六个 jobs 的 ID、预算、Store 记录总数和完成归档字节不变。这些都仅为本地软件测试；独立 reviewer 的最终复跑结果另行记录。

当前辅助脚本针对明确的 runtime2、两条策略和两轮任务，没有新增通用调度框架。prepare 首次初始化16 executions声明预算；已有 journal 必须具有相同预算，不能重置。原前置 baseline/negative 的预留单独保存在原记录中，不由此预算补算。

run 重启仍启动原本地 StdioKnowledge；已有 intake 不再取当前 revision，但不代表完全离线恢复。若所有已接收 jobs 已完成，`completed.json` 作为本轮版本不可变；其后如显式追加 repair，需另行版本化完成归档，当前脚本会拒绝覆盖原归档。Controller 本身的 repair/retry API 未改变。

状态 JSON 和进度 JSONL 是可读投影；数据库 journal 与原工件才是恢复权威。独立审查、根级全套测试以及实际远端轮次执行由主流程负责。
