# Phase 3：两轮真实多 Plan 的现有 API 编排核对

2026-11-12。来源为当前本地源码审计；本文件没有执行编排、生成优化 Plan、选择父版本、修改 Store 或访问远端。根线程负责 Gate 验收和硬件操作。示例中的 ID、预算和路径占位符必须换成已验证的实际值。

## 1. 当前入口与 Gate

唯一实验库继续使用本目录的 `experiments.sqlite3` 和 `artifacts/`。真实执行复用 `ColabBackend → ColabRemote → 原 Colab bridge`，所有结果复用 `integrate_result` 与原 `Store.consider`。不能为第二轮创建另一套数据库、直接改 Best，或把仅有历史结论的实验伪造成 Run。

本次读取的 [baseline-repair-result.json](baseline-repair-result.json) 已包含 `provenance=real`、`correctness=passed`、`confirmed=true`、`timing_validity=valid`、Profile ID 及 generate/compile/correctness/benchmark/profile 回执。分类是 `correct_inconclusive`，因为失败父版本没有可比计时；这个分类不表示本次正确性或计时失败。它也不证明优化收益。Oracle 已知错误负例及最终 P3.2/P3.3 Gate 判定仍由根线程完成。

| 对象 | 当前已知身份 |
|---|---|
| Task | `mla-decode-v1` |
| Baseline Strategy | `mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e` |
| 修复 Baseline Candidate | `baseline-repair-candidate:f3a6d0550533cde6f8dfb0e2c2c1b67749780cd806213a633c6d74fe543709b2` |
| 修复 Baseline Run | `baseline-repair-run:22398773cfc39fbfd571714a9578835e2add586ab10b873cf017fa9ee750a8f9` |
| 正式比较 Context | `mla-context:c86eb2d3aeeb66d7e174a2029d9f422d1848432b5a1c98690198fd06634866c3` |
| EvaluationBundle | `evaluation:da254618f822e70e11b5d39ab52dfdf2517a8e1cab5f8328be4cec748ae49178` |
| ProfileBundle | `profile:da254618f822e70e11b5d39ab52dfdf2517a8e1cab5f8328be4cec748ae49178` |

正式优化必须使用原完整 MLA Workload 和 Context。Oracle-negative 验证使用的小矩阵 Context/Strategy 保留在全部状态卡中，但不能混入正式参与策略、替代正式 workload 或进入性能比较。

## 2. 多 Strategy 的真实 Baseline 归属限制

这是现有接口最容易误用的地方：一个已测 Candidate 的当前 Profile/Benchmark 不能同时冒充 A、B 两条策略的当前测量。

`workflow.rounds._prepare_round` 对非导入父版本要求 `ParentCandidate.candidate_id → Candidate.plan_id → Plan.strategy_id` 等于所参与的 Strategy。`profiler.inputs.capture_inputs` 进一步要求选定 Run 测量该策略的精确 ParentCandidate、相同 Context，所有当前 Profile/Benchmark 及其 Evidence 都属于该 Run。相同源码摘要不能放宽这些归属条件。

因此无需修改运行时代码，存在以下合法初始化方式：

1. **最少新增执行的方式：首轮只参与现有 Baseline Strategy，每轮生成至少两个实质不同 Plan。** 多 Plan 不要求多 Strategy；两轮各自读取新历史，依然可以验证真实多方案递归。报告必须明确是一条策略的多 Plan，不能宣称已完成 A/B 多策略真实实验。
2. **如果需要 A/B 两条有当前 Profile 的路线：给 B 建立自己的实测基线。** 创建 B Strategy/Plan，以指向已测 A Candidate 的精确 ParentCandidate 为祖先，创建源码/config/build 相同的 B Candidate，给予独立 Run，在同一正式 Context 下经原真实后端与 Evaluator 真正执行。成功后 B 的本策略 Candidate 才能被 `prepare_round` 选为 B 的当前父。不能复制 A Run、改写 Evidence 的 owner，或把重新归档当成第二次测量。
3. B 可使用 `candidate_id=None` 的显式导入 ParentCandidate，绑定相同字节并进行 runless 分析；其 `run_id=null`、`profile_ids=[]`、`benchmark_ids=[]`。A 的结果只能作为有来源且不确定性明确的历史/共享知识，不能填成 B 的当前 Profile。此方式不能声称 B 首轮使用了自己的真实 Profile。

现有 `parent_policy='branch_best'` 独立选取各 Strategy 在 Context 下的 Branch Best；没有 Branch Best 时才用该策略已有的显式 current parent。它不会自动让所有策略使用 Task Best。`parent_policy='explicit'` 要求 `parents` 精确覆盖所有参与策略，且仍执行上述归属检查。

## 3. Knowledge pin 与新 Round

相关模块：`knowledge.py`、`knowledge_mcp.py`、`workflow/rounds.py`、`profiler/inputs.py`。

```python
from pathlib import Path
from rsi_kernel_agent.knowledge import method_note_id
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.workflow.rounds import prepare_round

# store 是现有 ExperimentStore；ROOT 是 RSI 主项目。
async with StdioKnowledge(ROOT / "knowledge-site") as knowledge:
    note_id = method_note_id(method_id)
    document = await knowledge.fetch_document(note_id)
    knowledge_refs = [{
        "method_id": method_id,
        "note_id": note_id,
        "revision": document.revision,
    }]
    prepared = await prepare_round(
        store, knowledge, task_id, context_id,
        participating_strategy_ids, "real-round:1", knowledge_refs,
        parent_policy="branch_best",
    )
```

方法内容必须确为对应 MethodRecord，来源须检查 availability/revision；方法 note 的 revision 是原字节 SHA256，不是自行重编码计算的正文摘要。`capture_inputs` 固定方法和来源，`intake_publication` 在使用时再次检查被引用来源。方法或来源变更后应重新分析，不能悄悄替换旧 manifest 中的内容。

`prepare_round` 返回 `manifest/request/snapshot/cards/histories`。它会写入两个版本化快照、所需 ParentCandidate、父选择 Decision、最终 `workflow_round.v1` Decision，**不是只读预览**。同一 round_id 和同一输入可恢复；改了输入而复用 round_id 会拒绝。准备后检查：

- `cards` 包含全部策略。
- `histories` 对每个参与策略 `complete=true`，包含失败、修复、必要祖先和依赖。
- 每个绑定的 parent/run/context、当前 benchmark/profile 精确一致。
- 正式分析所需证据 available，任何 stale/unavailable 在 packet 中明确列出。

显式父模式是 `parents={strategy_id: parent_id, ...}, parent_policy="explicit"`。不可混用 `branch_best` 与 `parents`。

`prepared['request']` 的 schema 为：

```json
{
  "schema_version": 1,
  "request_id": "ID",
  "task_id": "ID",
  "workload_id": "ID",
  "strategies": [{
    "strategy_id": "ID",
    "parent_id": "ID",
    "context_id": "ID",
    "snapshot_id": "ID",
    "run_id": "ID or null",
    "source_artifact": {"digest": "SHA256", "size": 1},
    "config_artifact": {"digest": "SHA256", "size": 1},
    "profile_ids": ["ID"],
    "benchmark_ids": ["ID"]
  }],
  "knowledge": [{"method_id": "ID", "note_id": "RSI/Methods/FILE.md", "revision": "SHA256"}],
  "environment": {"analysis_host": "conversation session", "workflow_round": "ID"},
  "tool_versions": {"workflow": "1", "input_manifest": "1"}
}
```

以上仅 schema 示意：真实 runless 情况使用 JSON `null`，不能写字符串 `"null"`；不可手填虚构字节长度。

## 4. Analyzer 使用当前会话

```python
from rsi_kernel_agent.profiler.session import start_session, submit_response
from rsi_kernel_agent.profiler.outputs import publish_result

packet_path = start_session(
    store, prepared["manifest"], session_dir,
    model_label="current conversation model",
    configuration="review", max_turns=6,
    max_tool_calls=12, max_response_chars=60000,
)
# 当前对话模型阅读 packet、真实源码/证据后，写入 response_path。
result = submit_response(store, session_dir, response_path)
# result is None：读取更新后的 packet，继续下一次会话响应。
# result 非 None：响应已完成且记录交换历史。
marker = publish_result(store, prepared["manifest"], result, publication_key)
```

必须在 `result is not None` 后发布。Session 目录已有 `session.json` 时不能再调用 `start_session`。中断后从原 session 和 packet 恢复；完成后读取 `result.json` 并核对原 result digest，再用同一 publication key 重放发布。`publish_result` 逐项幂等追加 Plan/父绑定/每 Run AnalysisBundle，最后写完成标记；Intake 只接受完成标记，不接受手工构造的 PlanSet。

响应严格二选一：

```json
{"tool_calls":[{"id":"unique-call-id","name":"history","arguments":{"strategy_id":"ID"}}]}
```

或 `{"final": proposal}`。可用工具参数为：

| 工具 | 参数 |
|---|---|
| source / history | `{"strategy_id": "ID"}` |
| profile | `{"strategy_id": "ID", "profile_id": "ID"}` |
| knowledge | `{"method_id": "ID"}` |
| reference | `{"ref": "catalog reference ID"}` |
| calculate | `{"model": "compute_memory/tile/live_memory/pipeline 中之一", "inputs": {}, "evidence_refs": [], "assumptions": []}` |
| skill | `{"name": "已显式供应的 skill 名称"}` |

`proposal` 的精确字段形状为：

```json
{
  "code_understanding": [{
    "strategy_id": "ID", "stages": ["..."], "loops": ["..."], "movement": ["..."],
    "dependencies": ["..."], "layout_memory": ["..."], "synchronization": ["..."],
    "edge_cases": ["..."], "refs": ["source:ID"]
  }],
  "observations": [{
    "id": "o1", "strategy_id": "ID", "category": "hypothesis", "text": "...",
    "refs": ["source:ID"], "predicate": null
  }],
  "hypotheses": [{
    "id": "h1", "strategy_id": "ID", "text": "...", "refs": ["source:ID"],
    "alternatives": ["..."]
  }],
  "plans": [{
    "id": "proposal-ID", "strategy_id": "ID", "parent_id": "exact ID",
    "mechanism": "distinct mechanism", "modification": "concrete change",
    "refs": ["source:ID"], "observation_ids": [], "calculation_ids": [],
    "tradeoffs": ["..."], "risks": ["..."], "unknowns": ["..."], "variants": ["label"],
    "validation": {"correctness": ["..."], "performance": ["..."]}
  }],
  "unknowns": ["..."]
}
```

数组中的单行只是字段示意，不是合法完成提案：**每个参与 Strategy 必须至少两个 mechanism 和 modification 均实质不同的 Plan**。Plan parent 必须等于固定输入，引用归属须为同策略或共享 owner=None。不能用拼写差异冒充不同机制。Observation 的 `predicate={ref,path,value}` 检验引用中的精确值；实际 `_predicate` 从 `reference['value']` 开始遍历 path，例如 EvaluationBundle 延迟可用 `['latency_ms']`，不要额外加一层 `value`。packet 中 `predicate_schema.path=['value']` 只是占位示意，须以实际引用和校验器为准。某个数值匹配不证明瓶颈因果；审查会将不支持的断言降为 hypothesis。

## 5. Candidate 工件与真实 Intake

真实 variant 不含 synthetic 的 `scenario/latency_ms`：

```python
variant_specs = {
    proposal_id: [{
        "label": exact_published_variant_label,
        "source_ref": entry_source_artifact,
        "source_bundle_ref": source_bundle_artifact,
        "config_ref": config_artifact,
        "build_ref": build_artifact,
        "priority": 0,
        "device": context.environment["runtime_session_id"],
        "cost": {"executions": 1, "tokens": 0, "time_s": declared_bound},
    }]
}
```

键为 Analyzer proposal ID，不能用 Store 中 publication 后转换出的 Plan ID。每个 proposal 的 variant 数量、顺序、label 必须匹配已发布 Plan。全部工件引用为 `{"digest": SHA256, "size": byte_length}`，JSON 工件使用 `canonical_json(...).encode('utf-8')` 经现有 ArtifactStore 保存。

源码包：

```json
{"schema_version":1,"entry":{"path":"RELATIVE.py","symbol":"entry"},"files":[
  {"path":"RELATIVE.py","artifact_ref":{"digest":"SHA256","size":1},"role":"source"}
]}
```

每个 helper/依赖文件都需保留，role 为 `source/dependency/oracle` 之一；路径不可逃逸，入口文件 artifact 必须等于 variant.source_ref。修改 helper 也必须更新 source_bundle 及 build；仅保存入口源码摘要不完整。

Build 必须绑定：

```json
{"schema_version":1,"source_bundle_digest":"SHA256","oracle_bundle_digest":"SHA256",
 "runner_digest":"SHA256","oracle_digest":"SHA256"}
```

Oracle、runner、原 benchmark/parser trusted_files 均沿用正式 Context。不能为了 Candidate 改变它们。

当前 `mla_runner.validate_candidate_config` 只接受以下顶层字段及资源调整：

```json
{"schema_version":1,"kernel":"mla_decode","case_overrides":{
  "actual-case-id":{"tiling":{"block_size":128},"params":{"vmem_limit_bytes":16777216}}
}}
```

其中 case ID 必须属于固定 workload；`case_overrides` 可为空。每个 case 仅允许 `tiling/params`，两者可省略或为空；`tiling` 仅允许 block_size，须正整数且为 128 的倍数；`params` 仅允许 vmem_limit_bytes，须正整数。示例数值不是推荐值。Shape、dtype、mask、causal、reference、容差、输出语义不能通过 config 改动。

```python
from rsi_kernel_agent.workflow.intake import intake_publication

async with StdioKnowledge(ROOT / "knowledge-site") as knowledge:
    intake = await intake_publication(
        store, marker.id, "real-round:1", variant_specs, knowledge,
    )
# 检查每项 accepted / waiting / rejected 及 reason。
controller.accept_intake(intake)
```

同一 intake ID 的相同输入幂等返回原结果，异值拒绝。已被接受的 publication 不可跨轮再次消费；第二轮必须重新分析并发布。waiting 结果是不可变历史：前置条件恢复后按新 round ID 重试，若来源 pin 已变化则重新分析。

## 6. Controller、预算与恢复

```python
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.journal import WorkflowJournal

prior = WorkflowJournal(store, task_id).load()  # 只读；先查看已有 limits
backend = ColabBackend(store, remote)          # remote 复用已配置 bridge/worker
controller = Controller(store, task_id, backend, limits=chosen_limits)
controller.accept_intake(intake)
state = controller.step()
```

`chosen_limits` 必须在 Task 首次 journal 初始化前确定；已有状态时省略 limits 会沿用，显式提供则必须与归一化已有值完全一致。默认值为 `max_concurrency=2, executions=100, tokens=100000, time_s=10000, max_repairs=1, max_retries=1, weights={}`。不能为了第二轮重置 journal/预算。CLI `status` 也会实例化 Controller，可能首次写入默认 limits；预算未确定前优先直接读取 `WorkflowJournal.load()`。

每次尝试执行前一次预留成本，失败不退款，恢复不重复扣费。tokens/time_s 是声明上界，不冒充会话实际 token 或远端实测耗时。真实阶段为 generate → compile → correctness → benchmark → profile → evaluate；设备阶段通过同一个远端 runtime 队列跨 Task 串行执行。远端互斥只证明遵循 worker 协议的进程间排他，不能据此声称排除了用户 Notebook/外部进程干扰；正式计时仍需实际污染检测和已记录的独占条件。

`run_until_idle(max_steps=...)` 是有界循环，可能密集 poll。远端长任务可以让宿主以受控间隔调用 `step()`，同时持续报告状态。`running` 继续 poll；`completed` 只取回/补归档；`definitely_not_started` 才可用原请求提交；`unknown` 保留阻塞和预留。不可用新 operation/run 绕过未知结果。已持久化的完成回执可在 runtime 消失后本地重放归档。

需要修复时使用 `repair_packet(job_id)` → 当前会话提供新源码/理由 → `submit_repair(job_id, response, key)`，新 Candidate 精确指向失败 Candidate，原失败保留。真实 helper-only 修复以 source_bundle 变化判定。`retry(job_id,key)` 保留同 Candidate、新 Run，仅允许原执行失败/incomplete 分类。不要直接操作 Best/Frontier 替代原评估流程。

## 7. 第二轮必须证明读取了新结果

第一轮所有已开始操作有可靠归档或明确 blocked/deferred 后，保存该轮结果、Evidence、Profile、各路线 Best/Frontier/失败及 snapshot。第二轮使用新的 `round_id`、新的 session 目录、publication key 和 proposal IDs，重新调用 `prepare_round`。

检查第二轮输入中的新 snapshot high-water mark、各参与策略完整历史、当前父、Run/Evaluation/Profile ID；不要沿用第一轮 manifest 或手工拼接第一轮 proposal。Analyzer 明确引用第一轮新事实，说明这些事实如何改变第二轮假设、修改位置、配置范围或保留/暂停判断。若收益不确定也应改变问题表述，不能把未显著变化包装为有效加速。

最低可审查记录：每轮 manifest/result/publication/intake ID，全部 Plan/Candidate/Run 和精确父关系，原始正确性/样本/Profile 回执，整轮失败/repair，结果影响第二轮判断的具体 Evidence 引用，以及最后新 Memory snapshot。Branch Best 与 Task Best 独立；较慢路线仍可保留 Frontier，不能因为另一条路线领先就自动停止。

两轮真实执行成功不自动等于性能提升。最终仍需分别报告真实运行、正确性、计时有效、Profile 有效、可比性能变化及统计限制。

## 8. 已核对的本地源码

- `src/rsi_kernel_agent/workflow/rounds.py`：选父、归属、新快照与 round 幂等。
- `src/rsi_kernel_agent/profiler/inputs.py`：AnalysisRequest 精确字段和同策略/Run/context 证据绑定。
- `src/rsi_kernel_agent/profiler/session.py`：会话持久化、恢复、响应 CAS。
- `src/rsi_kernel_agent/profiler/analyzer.py`、`tools.py`、`outputs.py`：响应 schema、工具、每策略多 Plan 和发布。
- `src/rsi_kernel_agent/workflow/intake.py`：真实变体绑定、实时来源、publication 跨轮去重。
- `src/rsi_kernel_agent/workflow/execution.py`、`real_protocol.py`：真实阶段、源码包、固定契约。
- `src/rsi_kernel_agent/workflow/mla_runner.py`：实际允许的 Candidate 配置。
- `src/rsi_kernel_agent/workflow/controller.py`、`scheduling.py`、`journal.py`：预算、幂等状态、repair/retry。
- `src/rsi_kernel_agent/workflow/colab.py`、`colab_remote.py`、`__main__.py`：真实后端、桥接与 CLI 初始化边界。

此文件为 source audit，不是新的 runtime 模块、测试结果、真实优化 Plan 或硬件实验结果。
