# 开放方法发现与逐路线设计：阶段交付记录

2026-11-16（Asia/Shanghai）。本页为 P3.5 之后的增量开发记录，不替换 [P3.5 Final Acceptance](phase3-final-acceptance-report.md)，也不是新增 TPU 授权。

## 现状核对

| 能力 | 开发起点 | 本阶段证据边界 |
| --- | --- | --- |
| ExperimentStore / ArtifactStore、Memory / Knowledge、固定 Evaluator、Controller / Colab 恢复 | 已实现，P3.5 真实链路已测 | 继续复用原数据库、对象和协议；不改正确性、容差、完整 cache 或计时规则 |
| 开放来源检索与机制判定 | 工作树已有 discovery 与测试，尚有失败 | 真实本地 stdio 检索与 12 项方法索引已核对；外部 WebSearch / WebFetch 返回 API 400，未伪造开放网页发现 |
| 逐路线独立设计与统一执行 | 文档设计；设计协调器缺失，Analyzer 强制每路至少两份 Plan | v2 单路线 manifest、可变 PlanSet、全路线 assessment 和设计完成门禁已加入；最终验证见下文 |
| 初始 5–8 有效探索轮 | 工作树已有计数模块，尚未集成状态卡与控制边界 | 沿用稳定 strategy ID、Decision 与原执行结果；真实轮数与 synthetic 分开 |
| 新 D 的真实多轮反馈、稳定性能收益 | 未验收 | 本次没有新增 TPU Run，不能称已完成 5–8 轮或稳定加速 |

本次只读核对原库 `.artifacts/phase3-2026-11-12/experiments.sqlite3`：1505 records / high-watermark 1530、Snapshot 26、Journal 247；账本 17/18 executions、30600/32400 声明秒，剩余 1/1800。当前 23 个 Controller jobs 为 15 completed、8 cancelled，无活动 job。实际只有 A/B 两条优化路线，第三条 Strategy 是 Oracle negative controls，不是 C。

Colab control-plane 实际返回 `assignments=[]`；不申请设备、不 bootstrap、不提交。Runtime 4 的 `operation:079d2d129595c886b74f35bdc44630a39e7ab03cc9817e67719659c733f64c3a` 仍缺 correctness 回执；其 Run 保留 incomplete。设备列表为空不证明操作 definitely_not_started。原 grant 绑定历史 B2，不把单次余额解释为任意新路线授权。

本节数字来自 [实时只读审计](../../.artifacts/next-stage-2026-11-16/current-state-audit.json) 的明确查询时点；后续本地准备如追加记录，另列增量，不回写上述历史数字。交付前再次逐条散列原 1505 条记录，并重算 Snapshot 26 的完整 SHA，均与原证据一致；Plan/Candidate/Run 仍 24/28/52，requests/receipts 仍 170/169，Journal 247。仅追加本阶段准入、设计与延期 Decision，详见 [原证据保全审计](../../.artifacts/next-stage-2026-11-16/final-store-preservation-audit.json)。

## 已实现的接口与复用理由

- `discovery.search_sources / archive_source / build_review_inventory / expand_review_inventory / classify_method`：沿用 KnowledgeClient / StdioKnowledge，记录 query、来源全文或明确缺项、阅读位置、revision / content SHA、上游版本、限额和 unknown；来源可先保存。机制核查独立于路线分析，覆盖所有状态路线的索引、Plan、代码与按需展开反馈；同机制复用 ID，new-to-task 与 global-new 分开。
- `AnalysisRequest / AnalysisInputManifest` v2：原字段兼容扩展，单路线输入；原 v1 回放不变。自己的历史与显式父依赖分页覆盖，记录 expected/read/missing/dependencies；全局 journal 只提供明确标记的本路线投影，保留原记录 digest，不称已读取其它路线全部载荷。
- `workflow.design`：冻结全路线 assessment、单个 Memory snapshot 和逐方法 revision；按稳定的路线登记顺序关闭独立设计，保留全部 Plan 或有理由的无 Plan 结果，发布 `design_complete.v1` 后才允许 Intake；全部 Plan 都有合法 accepted Intake 才调度，拒绝/等待的实现须补齐。新路线进入下一批，不在已执行轮次补造设计完成记录。v2 的失败 Intake 可追加纠正，已接纳实现和作业 ID 不变，不重复收费；原失败记录保留。
- `Analyzer / PlanSet`：v2 支持一份或多份 Plan；零 Plan 必须有 needs_research / deferred / inapplicable / awaiting_conditions 理由。`capture_request` 只登记，不调用 TPU、不生成可用 Evidence。
- `exploration`：5–8（默认 5）的准入策略固定，保存 lineage、机制差异与版本；同一全局轮每路线最多一个有效事件，幂等重放；正确性失败可形成失败反馈，不是性能成功。真实计数要求原固定 Evaluator 和持久 Colab 执行意图，无意图的 CPU 协议回执不会增加真实计数；测试中刻意模拟完整 Colab producer 的样例只验证协议，不是硬件证明或真实探索证据。
- `MemoryView.status_cards` 增加冻结的 `initial_exploration`；原 latest_attempt / last_correct / branch_best / task_best / parent / frontier / paused / deferred 不替换。
- `Controller / scheduling`：保留原预算和恢复协议；同批先给每条路线一次机会，再考虑额外候选，避免新路线追赶历史 served 次数时挤掉旧路线。保护期不能因慢直接 pause；研究或资源延期保留剩余机会。
- v2 repair 绑定原 Plan、失败 Run、准确 diff 和 host 审查，config 固定；真实 build 仅随入口源码更新 bundle digest，Oracle / runner / 其他依赖不变。语义审查仍是 host 判断，不是机器证明；新优化假设和依赖改动进入新设计批次。
- CLI 增加 design-*、knowledge-search 与 exploration-*，复用原 Store；单独检索不创建实验库，设计／反馈命令不自动执行、不创建 grant。

## 验证结果

**最后一次根级复验：475 项，472 passed、3 skipped、0 failures、0 errors，738.484 秒，exit 0。** 最终版本日志为 [final-root-tests-v4.log](../../.artifacts/next-stage-2026-11-16/final-root-tests-v4.log)；测试/源码身份、账本、实际模型与硬件边界见 [最终验证 JSON](../../.artifacts/next-stage-2026-11-16/final-verification.json)。辅助回归 383 次执行／376 个不同测试 ID 全部通过，详见下文。没有 commit/push。

使用现有 `knowledge-site/.venv-mcp/Scripts/python.exe`（Python 3.13.14）。PATH 的 `python` 指向 WindowsApps 占位程序，首次返回 49，未运行测试；未安装或升级依赖。

初始完整回归实际运行 393 项：2 failures、1 import error、3 skips。失败为全局方法来源缺失仍称 new、全局等价 unknown 仍准入 fork；导入错误为已有 design repair 测试依赖缺失的 design 模块。以上原始失败保留，不改旧断言来消除问题。

针对性红→绿包括：缺设计协调器；CPU 协议回执被计为真实探索；A 已有大量 served 时 D 的多候选挤掉 A；状态卡无保护策略；补采请求没有登记工具；执行后仍可重新冻结同轮。另有新增设计测试最初给 B 留空 hypotheses、准入夹具缺少 state、老 Intake 没有顶层 plan_id 三类集成问题；前两项补完整测试输入，最后一项兼容读取旧结果，均不放松裁判或测试预期。

阶段性软件证据：Discovery 36 项、输入 v2 12 项、旧输入 20 项、Analyzer 11 项、修复及旧 Controller/恢复/真实协议 CPU 契约合计 55 项、设计 CLI 6 项、设计流程 9 项、探索计数 21 项均曾定向通过；这些有重叠，不相加当作独立总数。中间全仓 460 项（457 pass、3 skip）通过，随后审查修复后的 475 项回归发现唯一旧 Intake `plan_id` 缺项，兼容修复后旧真实协议 15 项通过，最终 475 项全仓再次通过（472 pass、3 skip）。

本地文档变更器另有 3 项测试通过：保留旧 P3.5 状态与正文、不勾选或重排周安排、拒绝覆盖用户新改内容；实际更新仍使用运行中网站的 GET/PUT revision 检查。最后一次历史辅助复验 383 次执行／376 个不同测试 ID 全部通过、1612.684 秒、exit 0，见 [辅助日志](../../.artifacts/next-stage-2026-11-16/auxiliary-tests.log)；包含 7 项继承导入造成的重复条目，不把执行总数说成独立不同实验。

独立审查还复现并修复旧 publication 绕设计批次、停止路线仍入队、无反证取消保护、拒绝/部分 Intake 无法纠正、研究问题跨 Plan 借反馈、纯修补刷轮、helper-only 漏计、停止路线恢复超准入容量，以及跨路线同 Plan 兄弟候选泄漏、不完整旧来源被升级成完整等问题。逐项复现/修复边界见 [审查处理记录](../../.artifacts/next-stage-2026-11-16/review-resolution.json)。输入隔离最新 13 项、Discovery 最新 37 项和旧 Knowledge 13 项均已定向通过。

全部 unittest 结果是软件/CPU/synthetic 机制验证，不是新 TPU 正确性、性能或保护轮次。原 P3.5 的真实证据继续独立保留。

## 真实来源与可追溯实例

[真实 Knowledge 检索](../../.artifacts/next-stage-2026-11-16/live-knowledge-search.json)：经 stdio 执行“流水线 / Pallas / 归约”，15 个去重文档 ID 中读取 8 份全文，另记截断/未读项。不是 fixture，不限六项种子；没有将本地检索说成开放网页搜索。

[全库方法索引](../../.artifacts/next-stage-2026-11-16/live-knowledge-method-index.json)：12 项方法、12 个 source refs 可用，来自 2 个不同版本化源文档。索引 complete 不代表跨服务原子快照。

外部能力单独核查：WebSearch 和 WebFetch 的模型处理工具均返回 API 400；普通只读 HTTP 对项目文档中已知的 JAX 官方 TPU pipelining URL 返回 200，154396 bytes、SHA256 `901bad3535c367b8d164d01f9d393a869926d59b5a4b02645d480bb6bb78d588`，保存 ETag/Last-Modified。它是已知官方来源重新读取，不是搜索成功或新方法发现，见 [直接读取回执](../../.artifacts/next-stage-2026-11-16/official-jax-live-20261116/receipt.json)。没有上传本机文件。

[独立机制核查](../../.artifacts/next-stage-2026-11-16/method-review-audit-summary.json) 对比 12 方法、24 Plan、28 Candidate、11 份不同源码；当前 contiguous A/B 的 scores[H,T]、axis1 归约和 acc[H,D] 未实现 R25 的持久 scores[T,H]、axis0、acc[D,H]。复用 `hist-fwd-layout-r25`，分类为 `global_relation=same`、`task_relation=new_to_task`，不是新造全局方法。新 D `mla-strategy:transposed-score-state-v1` 已追加准入，准确 parent 为 A 的 `wf-parent:a37a1b7abd28b0681f9fd736f13eb97e37f15612`；保护 0/5、策略版本 `user-policy-20261116-initial-exploration-v1`，目标版本支持、正确性与收益仍 unknown。见 [准入](../../.artifacts/next-stage-2026-11-16/live-D-admission.json) 与 [本地准备副作用核验](../../.artifacts/next-stage-2026-11-16/live-preparation-effects.json)。原执行 journal/best/frontier 不变。

候选判定与初始准入已完成；但实际独立 A 上下文只读到 62 页中的前 6 页，服务端返回 HTTP 403 `Insufficient user quota` 后停止；用户要求继续后沿原上下文恢复一次，又收到 HTTP 502，未得到新的阅读回执或最终 response，`actually_read_all=false`。没有最终 response、没有新 Plan publication；B/D 未启动，`design_complete` 未发布，Candidate/Run 未创建、TPU 提交为 0。中断证据见 [A 阅读记录](../../.artifacts/next-stage-2026-11-16/analysis-A/reading-audit.json)，冻结批次见 [设计记录](../../.artifacts/next-stage-2026-11-16/live-design-batch.json)。这是具体未完成环节，不用协调者已经看过的摘要冒充路线独立完整阅读。

因此本次可追溯实例为：真实 stdio Search → 历史 Source 的固定 revision → 复用 `hist-fwd-layout-r25` → 独立机制核查 → D 准入与 A/B/D assessment、同 snapshot 的输入 manifest → **Analysis 未完成（账户额度）** → **Plan/Candidate/Run/新反馈未产生**；原 Memory 仅追加准入和设计输入，不伪造新实验结果。待执行成本明确为每个拟候选 1 execution / 1800 声明秒；新 D＋至少一旧路线至少 2 / 3600，当前既有余量 1 / 1800 且设备为空，尚缺资源范围与设备条件。

## 实际修改文件

| 文件/接口 | 增量与复用理由 |
| --- | --- |
| [knowledge.py](../../src/rsi_kernel_agent/knowledge.py)、[discovery.py](../../src/rsi_kernel_agent/discovery.py) | 保留原未提交 search/fetch 限额与 pins 扩展；补机制核查/来源覆盖，不替换 MCP 或方法库 |
| [memory.py](../../src/rsi_kernel_agent/memory.py) | 原状态卡新增冻结探索视图；不迁移原 SQLite schema |
| [inputs.py](../../src/rsi_kernel_agent/profiler/inputs.py)、[context.py](../../src/rsi_kernel_agent/profiler/context.py) | 单路线 v2、精确依赖闭包、可重建共享输入和读取覆盖；保留 v1 |
| [analyzer.py](../../src/rsi_kernel_agent/profiler/analyzer.py)、[outputs.py](../../src/rsi_kernel_agent/profiler/outputs.py)、[tools.py](../../src/rsi_kernel_agent/profiler/tools.py) | 原会话机制上扩展可变 PlanSet、分页与只登记 CaptureRequest |
| [design.py](../../src/rsi_kernel_agent/workflow/design.py)、[exploration.py](../../src/rsi_kernel_agent/workflow/exploration.py) | 原 Decision/Artifact 上的设计批次、准入与有效反馈，不另建实验数据库 |
| [intake.py](../../src/rsi_kernel_agent/workflow/intake.py)、[controller.py](../../src/rsi_kernel_agent/workflow/controller.py)、[scheduling.py](../../src/rsi_kernel_agent/workflow/scheduling.py) | 设计/每 Plan 就绪门禁、兼容纠正、有限修复与公平调度；复用原预算、恢复、Evaluator |
| [workflow/__main__.py](../../src/rsi_kernel_agent/workflow/__main__.py) | 新增设计/检索/探索命令，旧命令保留 |
| [tests](../../tests) | 保留起点测试并补发现、设计、输入、计数、修复、CLI 与兼容回归；未改原裁判断言 |
| [README](../../README.md)、[文档索引](../README.md)、本报告与七份知识站文档/周记录 | 分开写已实现、软件已验证、待实际模型/TPU验收，旧 P3.5 原报告不改 |

没有 git commit 或 push；初始四份未跟踪历史进度 JSON 和所有原始实验文件保留。知识站与 `.artifacts` 本来在 `.gitignore` 中，未擅自改变发布策略。

## 真实执行过的验证命令

根级全套（多次红/绿结果分别保留，最终以 `final-verification.json` 指定日志为准）：

```bash
knowledge-site/.venv-mcp/Scripts/python.exe -B -m unittest discover -s tests -v
```

历史真实协议的本地辅助回归（CPU 夹具，不调用 Colab 设备）：

```bash
knowledge-site/.venv-mcp/Scripts/python.exe -B -m unittest discover -s .artifacts/phase3-2026-11-12 -v
```

```bash
knowledge-site/.venv-mcp/Scripts/python.exe -B -m compileall -q src/rsi_kernel_agent
```

```bash
git diff --check
```

```bash
knowledge-site/.venv-mcp/Scripts/python.exe -I -B -X utf8 .artifacts/next-stage-2026-11-16/test_document_updates.py -v
```

本地真实输入还实际运行了 `audit_route_inputs.py`、`audit_context_encoding.py`、`prepare_live_design.py`、`prepare_delivery_state.py`；前两项只读原记录/生成工件，后两项仅追加准入、设计输入和明确中断说明。它们不是 TPU 执行脚本；原始失败与修复日志见 [选定测试证据索引](../../.artifacts/next-stage-2026-11-16/selected-test-evidence.json)。

## 针对性验收对应

| 用户要求 | 软件验证 | 真实边界 |
| --- | --- | --- |
| 同义去重、new-to-task 复用、真新机制、已实现识别、unknown 入库 | `test_knowledge_discovery.py`：机制比较、partial source 与来源更新、已存准入比较 | 本次真实实例复用 R25，不声称找到全局新机制；外部搜索 unavailable |
| 全路线 assessment、method/strategy 多对多、老快照 | `test_workflow_design.py`、`test_knowledge_discovery.py`、`test_memory.py` | 实际 A/B/负例/D 全部有 assessment；C 不虚构 |
| 实际输入隔离、完整覆盖、显式父依赖 | `test_route_inputs.py`、`test_context_encoding.py`：同 Plan sibling 负例、共享 journal 投影、lossless roundtrip | A/B/D 实际输入已捕获；实际模型阅读未完成，不能记整条验收通过 |
| 未收束零提交、全部合法 Plan 汇总 | `test_workflow_design.py`、`test_design_boundaries.py`、`test_design_cli.py` | 实际批次未收束，后端新提交 0 |
| 新 D 慢仍继续，旧 A 有假设仍推进 | `test_old_a_and_slow_new_d_get_plans_through_configured_protection` 连续 6 批两路线软件反馈；`test_workflow_scheduling.py` 验证首个机会公平 | 新真实 D 为 0/5，余 5，资源 deferred；不把 6 批 synthetic 写成真实完成 |
| 多 Candidate、重复计时、unknown、重启/改名不刷新 | `test_workflow_exploration.py`：31 项含 repair-only、反馈 Plan 归属、helper bundle、幂等与容量 | 依赖授权 Colab producer，不是硬件证明；历史 Runtime4 unknown 不变 |
| 仅余一次、多方案不超额、不建 grant、Best/parent 不变 | `test_workflow_design.py`、`test_workflow_budget.py` 与旧恢复回归 | 原账本 17/18、Best/Frontier 未动；无新增 grant |
| 真实来源链追溯 | 真实 StdioKnowledge query/fetch + 12 项版本化方法索引 + 机制核查 + 原 Store 准入 | Search→Source/Method→Strategy 有本次证据，后续 Analysis/Plan/Candidate/Run 明确未完成 |

## 当前批次与恢复入口

修正后的交付批次为 `design-batch:cf9f3a54deea9bec482625a4a80aee3e12cfdfc8`，round `next-stage-design-20261116-checked`；Snapshot 28 的输入记录数 1509 / 水位 1534，随后追加批次与中断说明后原 Store 为 1511 / 水位 1536。Journal 仍为 247、账本与 Best/Frontier 未变；[当前交付状态](../../.artifacts/next-stage-2026-11-16/delivery-state.json) 保留精确输入、路线计数、成本与缺项。旧 Snapshot 26/27 和中断 A 会话均保留，不原地改写。随后另追加 D 的资源延期 Decision，计数仍 0/5、余 5；见 [资源延期回执](../../.artifacts/next-stage-2026-11-16/delivery-resource-deferral.json)，不是因为第一次较慢而停止，也没有修改 Journal。

| 路线 | 本批 assessment | 实际分析/保护计数 | 准确父版本 |
| --- | --- | --- | --- |
| A | hypothesis：保留完整 cache 与精度，核查首块初始化等可区分假设 | 原会话阅读中断，未发布 Plan；legacy 不追溯伪造新策略轮数 | `wf-parent:a37a1b7abd28b0681f9fd736f13eb97e37f15612` |
| B | hypothesis：沿 delayed-store-wait 继续评估准备数据生命周期 | 未开始；legacy 历史与原计数分别保留 | `wf-parent:88fc2af19b8d773ad8ce15664ff31573f958e8a3` |
| D | hypothesis：R25 布局作为 new-to-task 独立路线 | 0/5，剩余 5，protected；已追加资源 deferred，无真实反馈 | 同 A 的准确父记录，显式跨路线依赖 |
| Oracle negative controls | inapplicable：属于裁判负例，不作为优化路线 | 保留全部失败与证据，不冒称 C | 不创建新优化父链 |

实际捕获清单（软件遍历完整，不等于模型已读）：A 599 条＝576 自有＋23 依赖；B 817＝585＋232；D 232＝3＋229。B/D 的父候选相关 A 依赖明确保留，不自动继承 A 的整个会话。修正前后真实输入的所有 record ID、原记录 digest、渲染记录、source 与 Knowledge 一致，变化仅批次/snapshot 身份和覆盖元数据；见 [逐项比较](../../.artifacts/next-stage-2026-11-16/corrected-input-comparison.json)。同 Plan 兄弟候选泄漏在新增软件反例中被拒绝，历史输入不据此倒签为完整模型阅读。

恢复时先使用已冻结批次启动 A 独立分析，不新建第二实验库；A 关闭后才读取 B，最后 D。原 `next-round` 保留为 v1 历史接口，新流程使用 `design-*`。示例仅准备本地输入，不执行 TPU：

```bash
PYTHONPATH=src knowledge-site/.venv-mcp/Scripts/python.exe -B -m rsi_kernel_agent.workflow --db .artifacts/phase3-2026-11-12/experiments.sqlite3 --artifacts .artifacts/phase3-2026-11-12/artifacts design-route --batch design-batch:cf9f3a54deea9bec482625a4a80aee3e12cfdfc8 --strategy mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e --session .artifacts/next-stage-2026-11-16/checked-analysis-A
```

不得把这个命令当作已完成的实际会话，也不要重复用于已存在的 session 目录。所有路线形成 PlanSet/具体原因后，才调用 `design-complete`，独立构造候选并由 Intake 校验；执行仍要重新核对当时授权、grant、原 operation 与设备。

## 文档与本周记录同步

已通过运行中知识站的 `/api/note` GET/PUT revision 条件更新用户指定的七页及 `待办清单/一周待办 · 2026-11-16.md`，共 8 页；随后重新 GET，内容 SHA 和返回 revision 全部与回执一致。原 P3.5 首段、历史正文和完成记录保留，周待办未勾选。分别写入已实现、软件已验证、实际模型/TPU 待验收；模型 403/502 中断没有写成完成分析。

[逐笔变更回执](../../.artifacts/next-stage-2026-11-16/document-update-receipts.json) · [更新后复核](../../.artifacts/next-stage-2026-11-16/document-update-verification.json)。首次复核脚本错误地要求周记录也包含数字 HTTP 状态码，失败后按周记录的“模型中断、未发布 Plan”语义重新核验；没有因此覆盖文档或放松 revision 校验。

## 当前可交付结果

软件实现、测试、真实本地检索/机制判定、D 准入和完整输入捕获可交付；没有提交或推送代码。**实际独立模型全量分析、候选实现、新旧路线 TPU 反馈和 5–8 轮验收尚未交付**，不把它们藏在软件通过结论中。恢复入口、准确父链、方法版本、输入批次与资源条件已固定；先恢复可用模型服务，再顺序完成 A/B/D，未设计完成不得执行。

## 保留限制与下一步执行条件

- `knowledge_corpus_snapshot_consistent=false`、`combined_atomic_snapshot=false` 保留。
- 真实输入首次捕获 A 为 268,495,962 bytes、B 为 369,072,721 bytes；全局 journal 重复载荷导致不可直接交给模型。新增 `rsi-shared-json.v1` 无损共享节点表示后分别为 2,941,392 / 3,817,356 bytes，逐字 canonical 重建与原 SHA 一致；原 Store、manifest 和历史不删改。`ContextReader.index/read/coverage` 支持节点/字符分页，记录缺页和区间，不把软件读取日志称为语义理解证明。
- 软件捕获完整分页不等于 LLM 逐页阅读已完成；真实输入大小、实际分析阅读与未覆盖项分别报告。
- JAX 0.8.1 是保存的真实环境；latest 文档 API 不能直接视为该版本可用。本机当前项目 Python 无 JAX/numpy；静态语法检查不是 TPU lowering 或正确性。
- 真实新路线＋至少一条旧路线需要匹配环境、有效既有范围授权及足够成本；不能由剩余一次额度推出已满足。没有新增 grant、设备申请或上传私有源码。
- 新路线 5–8 轮尚未真实完成；稳定端到端收益仍 NOT_CONFIRMED。系统优于随机搜索或单路线需要独立固定预算对照，不在本阶段宣称。
