# 原 Controller 的显式预算追加接口

此接口支持用户已授权的后续有界批次。它不会自动决定加多少额度、申请 Runtime、恢复 deferred 作业或派发实验，也不重置累计 spent。P3.5 当前真实批次仍须先完成四个恢复候选、两个诊断 Run 和最终 Memory/预算核验，再准备实际追加请求；本文不表示真实额度已经改变。

## 调用方式

```python
controller = Controller(store, original_task_id, None)
record = controller.extend_budget(grant, stable_key)
```

```powershell
python -B -m rsi_kernel_agent.workflow --db <原数据库> --artifacts <原工件目录> extend-budget --task <原Task> --grant <grant.json> --key <稳定幂等键>
```

命令只访问既有本地 Store，不初始化硬件 Backend。它在原 Task Journal 锁内读状态，并在一次 Journal save 中同时保存新限额和 `commands['budget:<key>']` 审计记录。相同 key 和完全相同的请求返回原记录；即使随后已有新执行，也不重复加额。相同 key 不同请求拒绝。

## 请求契约

`grant` 是只包含以下字段的 JSON 对象：

| 字段 | 内容 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `task_id` | 原 Task ID |
| `expected_revision` | 当前原 Journal revision |
| `expected_state_digest` | 原 Journal 完整 state 经 `canonical_json` 编码为 UTF-8 后的 SHA256；不能使用含 `remaining_budget` 等派生字段的 `status()` 返回值 |
| `old_limits` | 当前完整规范化 limits |
| `new_limits` | 新完整规范化 limits；仅正向增加 executions 与 time_s |
| `spent` | 当前累计 spent 原值 |
| `next_batch` | `{round_id, purpose, cost}`；cost 精确包含 executions/tokens/time_s，并等于本次额度增量，tokens 必须为 0 |
| `authorization` | `{source, quote, artifact_ref}`；source 为用户授权来源标识，quote 为准确摘录，artifact_ref 引用原 ArtifactStore 中包含该摘录的 UTF-8 文本 |
| `milestone` | `{snapshot, rounds, successful_round_id}`，见下文 |

`milestone.snapshot` 为原 `create_snapshot` 返回的完整 metadata，包括 digest、version、high_watermark 和 record_count。Snapshot 必须包含与 expected revision 完全一致的原 Controller state。

`milestone.rounds` 为非空列表，每项精确包含 `round_id`、`context_id`、`run_ids`；run_ids 必须列出该轮全部原 Controller 作业，而非抽选成功子集。各轮必须有明确真实 Context、原 Run、非取消的固定 Evaluator 结果、Evidence 和绑定回执，且每个作业至少有实际 compile 成功或失败反馈。

`successful_round_id` 指向列表中的一轮，其中至少一个候选必须完整通过全部五阶段，correctness passed、timing valid、confirmed true。只有 generate、取消归档、synthetic 结果或普通说明 note 均不能充当该成功证据。其他列出的实验失败保留其真实结果，不要求每个探索候选都正确或更快。

授权工件的字节哈希和来源绑定用于审计；它是保存的授权声明，不是密码学用户身份认证。调用方仍必须确认授权对应实际当前会话，并完整列出应完成的里程碑；核心接口不理解特定 P3.5 轮次名称，也不代替最终硬件 Gate 审查。

## 历史、恢复与限制

- 每次原 Journal 读取都验证历史限额变化链。任意无正式 grant 的 limits 改写、移除旧 grant、同一次追加改变 spent/jobs/暂停控制等操作均被拒绝。原无 grant 状态保持兼容。
- `Controller(..., limits=...)` 对已存在的不同 limits 仍报错；后续使用原已核验状态或传入准确当前值，不能通过构造器加额。
- Task 取消或存在 active/evaluating/blocked 作业时拒绝追加。追加保持原暂停、并发、修复/重试限制和策略权重。未开始的 deferred 作业仍需要明确 resume。
- 下一笔 grant 必须以紧邻上一笔声明的 next_batch 作为 successful_round，不能跳过该批次或重复使用已用于追加的完成轮次、下一批 ID。每笔额度保留前一 grant key、序号和原 revision，不改变旧 Snapshot。
- 里程碑重新检查保存的固定评估与小回执绑定，并复验 generate/compile 协议；不重新执行硬件，也不重算 NPZ 或解码 XPlane。大工件的原正确性依赖原 Evaluator 结果和前置完整核验。
- 原 P3.5 恢复/诊断辅助入口仍冻结在 16 executions / 28800 声明秒。当前批次完成前不能通过改这些常量或放宽旧 Gate 来加额。实际追加后，未来轮次需使用能够验证授权额度链的入口，不能重新运行旧冻结 checkpoint。

实现位于 [budget.py](../../src/rsi_kernel_agent/workflow/budget.py)、[Controller](../../src/rsi_kernel_agent/workflow/controller.py)、[Journal](../../src/rsi_kernel_agent/workflow/journal.py) 和 [CLI](../../src/rsi_kernel_agent/workflow/__main__.py)。本地 fixture 在 [test_workflow_budget.py](../../tests/test_workflow_budget.py)，其 `real` 表示测试真实协议契约，不是 TPU 实测结果。
