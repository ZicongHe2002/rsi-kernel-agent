# Phase 3：RSI 系统接口集成审计

2026-11-12，P3.0。本文依据当前主项目源码进行 **Source audit**，不是新的真实执行验收结果。Phase 2D+ 报告中的 230 项测试、229 通过及 1 跳过是已归档历史结果；本审计不冒充重新运行了这些测试。

当前唯一 RSI 主项目为 `C:\Users\fengluo\Desktop\rsi-kernel-agent`。现有体系可以支持 Phase 3，无需迁移 schema、第二数据库、第二 Controller、第二 Knowledge 服务或模型 API。必须做的兼容扩展集中在 workflow 的真实执行协议和固定结果校验；仅实现 `ExecutionBackend` 三个方法还不足以接通真实运行。

## 已检查的依据

- 根目录 [AGENTS.md](../../AGENTS.md)；用户完整 Phase 3 请求及其 Gate、目录、认证和现有连接方式约束。
- [2A 契约](../../src/rsi_kernel_agent/contracts.py)、[Store](../../src/rsi_kernel_agent/store.py)、[ArtifactStore](../../src/rsi_kernel_agent/artifacts.py) 的记录关系、状态、晋升和工件接口。
- [Knowledge](../../src/rsi_kernel_agent/knowledge.py)、[Memory](../../src/rsi_kernel_agent/memory.py)、[关联](../../src/rsi_kernel_agent/associations.py) 的原接口。
- [2C 输入](../../src/rsi_kernel_agent/profiler/inputs.py)、[Profile 归一化](../../src/rsi_kernel_agent/profiler/profiles.py)、原 Analyzer/session/publication 调用链。
- [2D execution](../../src/rsi_kernel_agent/workflow/execution.py)、[evaluation](../../src/rsi_kernel_agent/workflow/evaluation.py)、[intake](../../src/rsi_kernel_agent/workflow/intake.py)、[controller](../../src/rsi_kernel_agent/workflow/controller.py)、[journal](../../src/rsi_kernel_agent/workflow/journal.py)、[rounds](../../src/rsi_kernel_agent/workflow/rounds.py)、[CLI](../../src/rsi_kernel_agent/workflow/__main__.py)。
- [2C 接口指南](../phase2c-profiler-analyzer.md)、[2D 接口指南](../phase2d-workflow-controller.md)、[2D 实施计划](../superpowers/plans/2026-11-11-phase2d-workflow-controller.md)、[2D 验收报告](../../knowledge-site/manifests/phase2d-2026-11-11/implementation-report.md)、[独立审查](../../knowledge-site/manifests/phase2d-2026-11-11/independent-review.md)，以及根级对应测试。

外部 Kernel、历史、Colab 的实际文件和能力分别记录在各专项审计；本文不根据它们的旧报告假定当前 TPU 已连接。

## 可直接复用的契约

| 层 | 实际接口与复用方式 | 必须保留的边界 |
|---|---|---|
| 契约 | `Task`、`Workload`、`EvaluationContext`、`CandidateIdentity`、`Run`、各 Bundle 和 `Decision` | `Task.contract_revision` 是契约引用；上下文已有 environment/protocol/provenance，支持 real，无需添加表 |
| Store | `append/get/history/transition/consider/best/set_frontier/create_snapshot/snapshot_page` | Run 终态不重开；重试新 Run；晋升只用原 consider；Best 按 context 分隔 |
| ArtifactStore | `put/read/verify`，摘要、字节数、内容寻址 | 上传和下载均核验原字节；本地摘要正确不等于远端执行身份真实 |
| Knowledge | 原 MCP、`KnowledgeClient`、来源 revision、`MethodRecord` | 历史结论保留来源及不确定性；无真实 Run 证据不写成实验结果 |
| Memory | `open_snapshot/status_cards/histories`、`bind_plan/select_parent/link_method` | 全策略卡片、参与策略完整历史、父选择显式且冻结 |
| Analyzer | `AnalysisRequest/capture_inputs/load_manifest`、`start_session/submit_response/publish_result` | 当前会话模型产出响应；程序验证引用、Plan 和证据，不让模型修改晋升指针 |
| Journal | 现有 `workflow_state.v1` 追加状态链、Task 文件锁、过期写拒绝 | 原锁只覆盖同主机同 Task；不能视为跨 Task 或远程 TPU 锁 |
| Scheduler | weighted served/weight、策略内 priority、attempt 一次预算预留 | tokens/time_s 是声明上界，未变成实际模型用量或外部硬件 watchdog |
| Controller | intent → lookup/submit/poll → receipt → evaluation → consider | unknown 保持阻塞；取消不能改变已持久评估意图；失败和 repair 谱系保留 |

`ExperimentStore.consider` 校验成功 Run、正确性 passed、valid/confirmed 正计时、同 Run 的匹配计时 Evidence 和工件。它明确依赖可信生产者，不验证任意 Python 源码、Oracle 或真实性。因此真实数据必须在固定 Evaluator 入口完成额外校验，不能绕过它直接构造 EvaluationBundle 后把晋升当成真实性证明。

## 当前必须解除的 synthetic 限制

| 实际位置 | 当前行为 | 最小兼容改变 |
|---|---|---|
| `execution.execution_contract` | 仅允许 context.provenance=synthetic；policy 写死 synthetic | 从不可变 context 选择已知固定执行策略；saved 不获得执行权限 |
| `execution.validate_variant` | 精确要求 scenario 和人工 latency_ms | synthetic 原 schema 原样；real 精确 schema 排除这两个字段，要求可核验源码包 |
| `execution.make_request` | backend_id/provenance 写死 synthetic.v1/synthetic；四阶段常量 | 根据 contract.context 选择固定 backend 和阶段；相同 synthetic 输入保持原 request digest |
| `intake.fixed_contract` | 重复构造 synthetic policy；随后显式拒绝 real | 与 execution 共享 policy 构造；保留重放、同父关系、实时来源检查和逐 Plan 结果 |
| `Controller._ensure_records/_start/repair_packet` | 备注和 packet provenance 固定 synthetic | 从 job 固定 context 读取；synthetic 文本/键保持兼容，real 显式标记 |
| `Controller.accept_intake` | 直接索引 variant.scenario | 仅 synthetic 读取 scenario；real 容量状态来自后端 |
| `Controller.step` | 四阶段列表；仅同 Task benchmark 互斥 | real 增加独立 profile stage；实际跨 Task 全设备阶段锁放到远端执行队列 |
| `evaluation._verified_prefix` | compile 必须 synthetic-v1/executed=false；correctness.synthetic=true | 固定 real 验证分支检查真正编译、加载身份、Oracle/误差、环境和协议；不允许任意插件替换晋升校验 |
| `evaluation.integrate_result` | benchmark 内嵌 synthetic Profile，要求其单样本恰等于 benchmark 延迟 | real Benchmark 与 Profile 不同 operation，分别原始工件和 Evidence；不能要求 Profile 时长等于独立 benchmark 样本 |
| CLI `create/intake/run/recover` | 拒绝 real Task；无条件创建 SyntheticBackend | 显式选择既有 Colab adapter；读状态命令不应触发远端任务或分配新 Runtime |

保留旧 `STAGES` 与所有 synthetic 字段、状态、ID、摘要、幂等键；新增 `stages_for(provenance)` 供真实分支使用。不能仅把字符串 synthetic 改成 real 后继续使用人工 latency/scenario。

## 共享接口建议

以下是实现前协调的最小契约，最终已实现字段以 Phase 3 接口/验收报告为准。

```python
backend_id(provenance)                 # synthetic.v1 或 colab.v1；其他拒绝
stages_for(provenance)                 # synthetic 四阶段；real 增加 profile
validate_variant(store, variant, *, provenance='synthetic')
execution_contract(store, context_id)  # 返回完整固定 records + policy，以及 digest
make_request(store, job, stage)        # 由 job 的固定 context 决定协议
verify_receipt(store, request, receipt)

class ColabBackend:
    def lookup(self, operation_id): ...
    def submit(self, request): ...
    def poll(self, operation_id): ...
```

真实 variant 保留 label/source_ref/config_ref/build_ref/priority/device/cost，新增 `source_bundle_ref`，不接受 scenario/latency_ms。source_ref 仍指实际入口 UTF-8 源码，保证现有 Analyzer 可阅读；source_bundle_ref 指版本化源码依赖清单，列出相对路径、内容工件和角色，入口工件必须等于 source_ref。协议拒绝绝对路径、`..`、Windows drive、重复路径和遗漏入口；物化端还需拒绝符号链接/junction。

config_ref 保存实际 workload 和运行参数；build_ref 保存 `{schema_version,source_bundle_digest,oracle_bundle_digest,runner_digest,oracle_digest}`，把完整依赖闭包纳入 Candidate build identity。可信 Oracle、Benchmark、runner 的固定摘要来自 Task/Context 契约，不能由任意 Candidate 更换。PallasKernels 审计发现 Oracle 与 Kernel 可在同一源码文件中，因此 Oracle 不能复用可变 Candidate 模块：`context.protocol.oracle_bundle_ref` 固定独立基线包，加载身份另行核对。`trusted_files` 固定 parser/benchmark 等文件摘要。

后端复用现有 canonical receipt envelope 和 `execution_receipt.v1` 发布 Decision。新字段尽量放在各阶段 outputs；本地 Evaluator 同时核验 raw artifact、request digest、durable publication、Task/Run/Candidate/context/parent 关系，不能只信任 `status=succeeded`。

## 真实回执应证明什么

| 阶段 | 必需的真实输出 |
|---|---|
| generate | 已落盘 bundle 各文件摘要；远端绝对工作区；入口源码、config、build 对应关系 |
| compile | Runtime session；新进程/明确清理的导入环境；实际 module `__file__`、入口 symbol、源码/依赖摘要；编译 specialization；编译耗时与环境指纹 |
| correctness | 固定 Oracle 和输入 digest；完整 case 列表；shape/dtype/有限性；max_abs/max_rel/RMS；原容差和逐 case 结论；同输入 Tensor 绑定 |
| benchmark | 明确计时范围、编译/预热次数、每个原始样本、多输出同步；统计重算；协议版本；设备租约及干扰观察；相同环境/加载身份 |
| profile | 单独 operation ID、trace mode 实际支持结果、原始 XPlane、parser 源码/版本/依赖、解析 JSON、clock domain；绑定同一 Run/源码/环境 |

真实环境读取 Python/JAX/jaxlib/libtpu/可获取 XLA 信息、TPU model/device count/topology、Runtime session、workload、source/config/build。不可读取的值记录 unavailable 和原因；不能由文档规格补成实测。输入错误源码身份、错误 Oracle、无原始 timing、非有限结果、环境变动、无法确认的干扰都必须阻止 confirmed valid promotion。

`perf_counter + block_until_ready` 按实际协议标 host-observed synchronized latency，不能自动命名 pure-device kernel time。配对基线和候选必须使用同一测量范围。Profile-derived custom-call duration 属于另一个度量，不能替换正式协议吞掉 packing/projection 等必要工作。

## Profile 与原 2C 的同 Run 约束

`profiler.inputs` 当前严格要求：selected Run 测量该 strategy 的 exact ParentCandidate、context 相同，ProfileBundle/EvaluationBundle 及其 Evidence 全部同 Run。AnalysisBundle 也是单 Run。这是防止证据串用的有意设计，不应为了接入硬件直接去掉。

首版可把一次逻辑 Run 定义为：generate → compile → correctness → benchmark → profile → evaluate。benchmark 和 profile 是独立远端 operation，正式计时结束后才启动 trace，结束前等待所有输出完成。它们本来就属于同一个预先声明的 Run；回执分别保存真实 operation ID 和时间边界，故无需修改 2A/2B/2C 或伪装其他 Run。

如果真实适配器后来确实另开 Profile Run，应保留那个 Run 的全部身份，新增显式关联和支持多 Run 的分析输入版本，不能将另一 Run 工件重新标成 benchmark Run。目前先不做这个额外扩展。

Profile 缺失不能凭空生成一个 available Bundle。真实 benchmark 成功而 Profile 采集失败时要保存两项独立事实，Profile 标 unavailable；P3.3 Gate 仍未过，禁止启动依赖真实 Profile 的优化。是否允许这一 Run 参与 baseline Best 应由固定政策明确，不得在看到结果后变更。

当前实现前已固定严格策略：Profile 失败归档原始 Benchmark 和正确性，Run 标 incomplete、分类 profile_failed、confirmed=false，不晋升。原 `AnalysisTools.profile` 会将 ProfileBundle 顶层 Evidence 的所有 artifacts 当 JSON 解析；为兼容这一行为，原始 XPlane 使用独立上游 Evidence，parsed report 使用另一个 trace_derived Evidence，其 input_ids 引用原始证据。ProfileBundle 只引用 parsed Evidence；原 manifest 递归校验 raw 工件。不能将 protobuf 混进顶层 JSON 列表。

Correctness 验证全部输出，包括 Decode cache。Workload 的每个 case 显式固定输出 name/shape/dtype/comparison。allclose 输出必须满足有限值和原容差；exact cache 按原字节比较，允许 opaque 区域含 NaN，但此时无意义的数值误差必须为 null 并给出原因。输入/Oracle 输出/Candidate 输出原工件绑定到 correctness Evidence，后续 consider 会重新核验这些字节。

## 远端事件和恢复设计

本地 intent 继续先于传输。通过用户已有 Colab bridge 提交一个固定 worker；worker 自己维护当前 Runtime 的 operation 文件/回执，而不是把 CLI exit code 或 stdout 当成成功。远端目录是执行 spool，不是第二实验数据库，正式事实仍回传原 ArtifactStore/Store。

1. operation_id 由原 Run + stage 确定；request_digest 覆盖全部固定输入。
2. 远端验证 Runtime session、request digest、原始输入工件和前置阶段回执；同 operation 异值拒绝。
3. 独占 claim 成功并持久化 accepted/running 后才执行。完成结果/工件 fsync/原子发布后才确认 completed。
4. completed 只回传原 receipt；running 继续查；当前已验证 Runtime 的 ledger 确认不存在才允许 definitely_not_started。
5. CLI timeout、网络断开、Runtime 不可达、旧 session ledger 丢失都返回 unknown，保持资源/预算，不创建新实验补跑。
6. 本地导入完成后用原确定性 Evidence/Bundle/RunEvent/consider ID 重放归档；中断不会重新执行已完成远端工作。

即使 worker 能去重，也不能称分布式恰好一次。TPU 计算完成但远端 receipt 落盘前进程死亡时，实际完成状态可能不可判定；此时明确 unknown，拒绝自动重跑。故障注入必须覆盖“远端完成回执已持久化、本地尚未导入”并用独立调用计数确认没有第二次计算。

## Runtime 范围设备隔离

原 Task lock 保留，只负责本地 journal 原子操作。新增一个以 Runtime session/设备身份为 key 的受控远端队列/锁，覆盖所有 Task 的 compile、correctness、warmup、benchmark 和 profile。锁持有到当前远端 operation 确认终结，不能在本地 bridge 超时后释放。首版串行化设备活动，避免建立复杂分布式调度服务。

锁只能约束合作 worker，不能证明用户手动 Notebook 或其他进程完全不存在。记录 before/after observation 和受控区间；发现并行工作/身份变化时标 invalid，不能终止用户任务。报告用“受控 worker 队列”描述已证范围，无证据时不声称物理 TPU 绝对独占。

## 兼容与验收测试清单

- synthetic request/receipt 的 canonical bytes、稳定 ID、旧 intake 和恢复结果保持；旧 230 项及原 MCP 回归继续运行。
- real context 被正确分发；saved/unknown backend 拒绝；real variant 不允许人工 latency/scenario；缺失/篡改 bundle 拒绝。
- loaded module 路径/入口/helper 与 Candidate 不同的负例在 Evaluator 拒绝；上传字节相同但导入另一个同名模块仍必须失败。
- correctness 固定输入/Oracle/容差；错误 Candidate、NaN/Inf、shape/dtype/边界条件；统计不能只接受传入的 passed。
- benchmark 必须完整 raw samples 和同步范围；JIT/transfer 不混写；不完整租约、环境漂移、未知状态不允许 valid。
- benchmark/profile operation 分离但同逻辑 Run；跨 Run/候选 Profile 拒绝；Profile 失败不伪造数据。
- 同一 Runtime 的两个 Task 不能并发占设备；本地 timeout 不释放远端活动；unknown 不提交新 request。
- 实际子进程终止/回执落盘边界的恢复：重复 lookup、重复 import、重复 integrate、考虑晋升均不重复执行，保留失败、cancel、repair、新 Run 和预算。
- 原 Round → snapshot → ABCDE 状态卡 → 完整 participating histories → 新 Analyzer response → 多 Plan 流程继续复用；第二轮的推理引用新的真实实验，不能复制第一轮方案。

本文件只完成系统 Source audit 和最小改动建议；P3.1 真实连接、P3.2 Baseline、P3.3 Profile 和最终两轮优化各自需要新证据，不由本文自动通过。
