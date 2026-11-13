# Phase 2B：Knowledge 与 Memory 接口

本阶段复用现有知识库 MCP 与 [Phase 2A 核心存储](phase2a-core-foundation.md)，提供方法检索、来源追踪、所有策略状态卡和各参与策略完整历史。Knowledge 仍在知识库 Markdown 中，Memory 仍在原实验 SQLite 中；没有第二个实验数据库，也没有 schema 迁移。

本页描述实际接口和边界；本轮最终文件、测试数量、状态卡/历史/方法关联的合成输出见[实现报告](../knowledge-site/manifests/phase2b-2026-11-11/implementation-report.md)。[MCP 审计](../knowledge-site/manifests/phase2b-2026-11-11/mcp-audit.md)记录既有服务的真实 schema、权限与连接行为。

## 复用范围与模块

| 文件 | 职责 |
|---|---|
| [knowledge.py](../src/rsi_kernel_agent/knowledge.py) | 方法契约、完整文档读取、方法保存/检索、来源状态和显式章节提取 |
| [knowledge_mcp.py](../src/rsi_kernel_agent/knowledge_mcp.py) | 已安装 MCP SDK 到既有 stdio server 的连接薄层 |
| [memory.py](../src/rsi_kernel_agent/memory.py) | 2A 快照的只读状态卡、分策略历史、分页和明确过滤的历史搜索 |
| [associations.py](../src/rsi_kernel_agent/associations.py) | 用现有追加 Decision 固定 Plan 父关系、当前父选择及方法到实验的关联 |

2A 的 `contracts.py / artifacts.py / store.py`、SQLite schema v1、记录 ID、快照、幂等和不可变规则继续使用。既有网站、MCP server、认证代码及权限配置不被替换。MCP 依赖仅在 stdio 连接边界加载；使用 Memory 不需要启动网站或 MCP。

## Knowledge 连接与读取

`StdioKnowledge(website_dir, python_executable=None, entrypoint=None)` 是 async context manager，返回 `KnowledgeClient`。默认使用当前 Python 和 `website_dir/mcp_server.py`；也可明确提供已有 `knowledge-site/.venv-mcp/Scripts/python.exe`。启动参数是 `-B -u`，工作目录固定为 website_dir，MCP session 超时为 30 秒。解释器、目录和可选 entrypoint 必须由可信部署代码提供，不能来自检索文档或模型任意输入。

连接先核对服务器名称 `Zhixu Knowledge` 和四个工具的必要字段；解析 `isError` 后读取 structuredContent 或文本 JSON。不会调用知识库启动器、重建隧道、修改 Codex 配置或安装依赖。`KnowledgeClient(async_call_tool)` 也可接收已经建立的 MCP 调用函数，该函数须返回 JSON dict。

| async API | 返回与行为 |
|---|---|
| `search(query='', page_size=50)` | 完整取完此次实时 listing 的页，返回 results / total / complete / snapshot_consistent |
| `fetch_document(note_id, expected_revision=None, page_size=24000)` | 校验连续字符分页及同一 revision 后返回 `SourceDocument` |
| `discover(queries=('',), page_size=50)` | 合并多查询命中的文档 ID，完整读取文档，返回 documents / sections / unavailable；不创建方法 |
| `save_method(method, expected_revision=None)` | 创建或带 CAS 更新确定性方法 note；返回其 ID/revision |
| `get_method(method_id)` | 读取并校验方法 note，返回 `MethodRecord` |
| `source_status(method)` | 独立查询每个来源当前版本与可用性，不修改捕获记录 |
| `find_methods(keyword='', category=None, hardware=None, mechanism=None, applicability=None)` | 筛选方法，并返回来源状态、无法读取的方法和完整性标志 |

`SourceDocument` 字段是 `id,title,category,revision,content`。fetch 的 offset/limit 按 Unicode 字符计数。每页都检查 ID、offset、revision、total_chars、title/category 和 next_offset；文档中途变化抛 `RevisionConflict`，调用方须重新完整读取，不能把不同版本拼接成全文。

原 MCP revision 是原始文件字节 SHA-256，可能包含 UTF-8 BOM；解码后的正文不能重新编码后冒充同一个摘要。`expected_revision` 可绑定 search 返回的版本，版本变化会明确失败。

search 是现有服务的实时子串检索，覆盖标题、分类和正文，没有语料快照令牌。客户端检查页长度、总数、重复 ID 和连续 offset，但相同总数也不能排除编辑时发生重排。因此 `complete=True` 仅表示返回页覆盖本次可观察的 listing，始终附 `snapshot_consistent=False`。discover / find_methods 也保留这个区别；无法读取的文档/方法列在 unavailable 中，不假装来源不存在或保证全库一致快照。

`find_methods` 的文本比较不区分大小写：keyword 匹配整个方法结构，category 匹配 optimization_category，hardware 匹配 applicable_hardware，mechanism 匹配 mechanism，applicability 匹配 applicable_workloads 与 preconditions。它是可解释的子串过滤，不是语义检索、硬件兼容性证明或性能排序器。

## Method Record schema v1

方法以专用 `RSI/Methods` Markdown 文档保存，其中恰好一个 `rsi-method-v1` 代码块包含 `{"schema_version":1,"method":...}`。人工说明可写在块外，正常 CAS 更新只替换该块，保留前后文本。方法 JSON 损坏、重复块或未知 schema 会拒绝，避免默默清除人工编辑。

| 字段 | 类型与含义 |
|---|---|
| `method_id` | 稳定 ID；1–128 字符，字母/数字开头，其余限字母、数字及 `._:-` |
| `method_name` | 非空方法名称 |
| `description` | 非空描述；章节提取时保存选中原文 |
| `source_refs` | 非空 `tuple[SourceRef,...]`；每个引用有 document_id、revision、locator、excerpt、availability、reason |
| `source_revision` | `document_id → captured revision` 的精确映射，须与全部 source_refs 一致 |
| `optimization_category` | 非空优化分类 |
| `applicable_hardware` | 硬件名称 tuple；未知时可为空 |
| `applicable_workloads` | 工作负载 tuple；不自动推断支持范围 |
| `preconditions` | 前置条件 tuple |
| `implementation_requirements` | 实现要求 tuple |
| `expected_tradeoffs` | 预期取舍 tuple，不等于已测效果 |
| `known_risks` | 已知风险 tuple |
| `unknowns` | 尚未验证事项 tuple |
| `local_experiment_refs` | dict 的 tuple；只允许 plan_id / candidate_id / run_id / evidence_id 字段 |
| `validation_status` | `source_supported / unverified / synthetic_only`；不接受自动宣称本地性能已验证 |
| `mechanism` | 非空机制标签，默认 `unknown`，供检索使用 |

`SourceRef` 的 captured revision 是小写 SHA-256；locator 和 excerpt 必须非空。availability 允许 available / unavailable / stale，后两者必须给 reason。一个 MethodRecord 不允许同文档 ID 对应互相矛盾的 captured revision。tuple 写入 JSON 时变为数组；`method_to_dict` / `method_from_dict` 验证并复制字段。

`local_experiment_refs` 在 Knowledge 层只检查字段与 ID 形状，**尚未证明引用存在或归属正确**。正式知识到实验关联由下文 `MemoryWriter` 验证并写入 Decision。仅填写实验 ID、引用论文或官方文档，都不会把 source claim 升级为实测结论。

`method_note_id(method_id)` 返回 `RSI/Methods/method-<SHA256(method_id UTF-8)>.md`。固定路径避免名称差异、特殊字符或重复请求生成多个方法文件。无 expected_revision 时：相同记录返回已有 ID/revision；异值已存在时抛 `RevisionConflict`。有 expected_revision 时先完整 fetch 并核对，再调用既有 update_note CAS。创建超时后只接受实际读取到的完全相同方法；更新超时仍报告结果未知，调用方需重新完整 fetch 核对并按新 revision 合并，不盲重试覆盖不同内容。

`extract_method(document, heading, *, optimization_category='unclassified', hardware=(), workloads=(), mechanism='unknown')` 只提取明确指定且唯一的 Markdown ATX 标题章节，忽略 fenced code 中的假标题。它保留标题到下一个同级/更高级标题前的文本，ID由文档ID和标题决定，来源保留当时revision和 `heading:<标题>` 定位。它不是 LLM 方法生成器；不会从论文措辞杜撰局部效果、硬件范围或前置条件，默认 unknowns 明确需要适用性及实验验证。调用方可在已有依据下补充结构字段，不需要逐条人工批准。

来源版本可追踪，但当前实现只保存**引用、revision和摘录**，没有自动归档来源文档完整旧版本。`source_status` 在当前revision不同返回 stale，缺失或暂不可读返回 unavailable及原因；旧摘录仍保留。需要完整历史源码/文档归档时应在后续明确增加对应能力，不能从现有revision声称旧全文可下载。

## Memory 两阶段读取

调用方先明确建立 2A checkpoint，再读取该固定版本：

```python
snapshot = store.create_snapshot(task_id, "round-1:memory-snapshot")
view = MemoryReader(store).open_snapshot(snapshot["id"], page_size=100)
cards = view.status_cards(context_id)
histories = view.histories(("A", "B"))
```

`create_snapshot` 是明确写入。`open_snapshot`、状态卡、历史页和搜索都是只读，不自动再创建快照或访问会变化的 live best。open_snapshot 会取完 2A 分页，核对 ID/version/digest/record_count、连续 offset、无重复记录、投影及各策略归属计数；覆盖不完整抛 `MemoryIncomplete`。视图内部存规范 JSON，返回值每次分离复制，调用方修改结果不会改变已捕获视图。

### Stage 1：所有策略状态卡

`MemoryView.status_cards(context_id, recent_plan_limit=5)` 返回快照中的**全部策略**，不硬编码 A–E，也不先按任务最佳筛掉其他策略。

| 字段 | 真实语义 |
|---|---|
| `snapshot_id / snapshot_version` | 状态卡固定的历史版本 |
| `strategy_id / context_id / provenance` | 路线、比较范围及 synthetic/saved/real 来源 |
| `latest_attempt` | 按 Run 创建顺序或从未运行 Candidate 创建顺序确定的最近尝试；附当前状态；后到的旧Run报告不会把该旧Run伪装成新尝试 |
| `last_correct_candidate` | 本context最新写入的 correctness=passed EvaluationBundle对应候选；可较慢、timing无效或未确认，不等于最佳 |
| `branch_best / task_best` | 此快照中分别保存的分支/任务最佳投影；任务最佳可能来自另一策略 |
| `frontier` | 此策略此context的探索候选，不从最佳结果推断 |
| `paused / pause_decision` | 最新策略级 paused/resumed Decision；不因别的策略更快自动暂停 |
| `deferred` | 当前Run状态为deferred，或尚有未执行候选的Plan/Candidate延期Decision；已完成的兄弟候选不清除剩余候选的延期，原因和引用保留 |
| `current_parent_candidate` | 最新明确的 parent_selection.v1 Decision；缺失为null，不猜成latest/last_correct/best |
| `recent_plans` | 该策略最近Plan；total与complete另列，截断明确 |
| `evidence_refs` | 当前策略此context的Evidence ID |

没有Run的Candidate、Plan和策略暂停是任务级事实，没有独立context归属；已有Run、正确性、最佳结果和证据按指定context读取。已有 Run 的状态事件保持权威，不因 Plan 延期而重标已执行 Run。状态卡用于导航，不能代替下一阶段完整历史。evidence_refs 枚举当前context全部相关证据ID，frontier/deferred也没有固定数量上限，因此卡片不是严格大小受限的模型输入；后续调用方可生成明确摘要，但须保留完整历史的访问方式。

### Stage 2：每个参与策略的完整历史

| API | 含义 |
|---|---|
| `history_page(strategy_id, offset=0, limit=100)` | 该路线完整记录集合的一页；返回total/offset/next_offset/complete、当前页的owned_ids/dependency_ids及全局owned_total/dependency_total |
| `histories(strategy_ids)` | 同一快照内每个指定策略的完整历史dict；A与B可共同参与 |
| `search_history(query='', strategy_ids=None, context_id=None, kinds=None)` | 策略拥有记录的明确过滤结果，始终 `filtered=True, complete_history=False` |

完整历史包含策略自己的全部Plan、Candidate、Run、Bundle、Evidence、事件与Decision，不只取成功或最近结果。shared Task/Workload/context、精确ParentCandidate及跨策略祖先/必要运行证据作为dependency保留；owned_ids明确归属，不能把依赖记录算成自己的新尝试。repair保留实际父候选及祖先链，failed/invalid/incomplete/deferred都可追溯。

只在 offset=0且该页覆盖全部记录时 complete为true；末页next_offset=None不代表该单页完整。分批阅读应固定同一snapshot ID/digest，核对连续offset、记录总数和所有页。摘要/状态卡只作为导航，不删除或替代原始记录。分页API仍依赖2A先完整加载task snapshot；当前Memory索引与结果也在内存中，复杂依赖遍历可能反复扫描，不是无限规模或常量内存检索系统。

## Knowledge 到实验的显式关联

`MemoryWriter(store)` 调用既有追加机制保存版本化 `Decision(kind='note')`，不改变SQLite schema。

| API | 记录 |
|---|---|
| `bind_plan(plan_id, parent_id, key)` | 固定唯一 `plan_parent.v1`，ID由plan_id确定；建议在建立Candidate前调用 |
| `select_parent(strategy_id, context_id, parent_id, reason, key)` | 追加 `parent_selection.v1`，明确此策略/context当前采用的父 |
| `link_method(method, method_note_id, method_note_revision, plan_id, parent_id, reason, key, candidate_id=None, run_id=None, evidence_ids=())` | 追加 `knowledge_link.v1`，保留方法完整结构快照、源引用及知识note revision |

helper检查引用已存在、目标类型和Task/Strategy/Plan/Candidate/Run/Evidence归属。由于原记录不可变，验证后的对象身份不会被后来覆盖。方法note路径须等于确定性method_note_id，note revision须为SHA-256；该引用仍是调用方从Knowledge取得的版本声明，helper不另行联网验证。

Plan的初始父关系固定；修复Candidate的**实际直接父**可不同，但必须沿精确祖先链回到Plan的固定父。关联同时记录 parent_id（Plan基点）和 candidate_parent_id（此Candidate直接父），不会为允许repair改写原Plan或假称repair直接来自基线。可选Run必须属于所给Candidate，Evidence必须属于所给Run。

所有方法关联都保存当时method_snapshot和source_refs；后来修改知识note不重写既有Decision，也不会自动晋升方法。方法快照内的 local_experiment_refs 仍是未验证的跨资料引用；MemoryWriter 验证的是显式传入的 plan/candidate/run/evidence 参数，不会自动把方法内每个任意实验 ID 升级为已证实关系。运行与证据仍保留自己的provenance，历史AI hypothesis也维持原claim_status，不能因为进入Memory就变成fact。

低层 `ExperimentStore.append(Decision(...))` 的通用details仍可被其他调用方直接使用；它不拥有2B的全部typed语义。生产关联应使用MemoryWriter；MemoryReader对识别的2B Decision再核验关联、完整MethodRecord、方法ID/确定性路径/revision和来源一致性，遇到无效已知结构拒绝完整视图，不静默丢弃。未知版本/其他note仍是原始记录，不能假装已理解其语义。该兼容策略是应用层验证，不是SQLite迁移或恶意数据库写入防护。

## 验证与边界

测试包括完整/改版/BOM来源读取、方法去重与人工冲突、来源缺失、ABCDE状态卡、A/B独立历史、修复祖先、完整分页、并发只读及进程重启。真实stdio集成使用原MCP server和HTTP Handler、临时Markdown与SQLite，仅在测试内提供内存服务token，不创建`.auth`；并验证无效token拒绝写入。生产账号、认证和服务密钥保持原状。实际通过数量与剩余跳过以实现报告为准。

KnowledgeError表示知识不可用/无效；RevisionConflict表示版本或固定身份冲突。MemoryError表示非法读请求；MemoryIncomplete表示不能证明快照完整一致。暂无语义向量索引、自动外部资料抓取、旧全文归档、LLM提案器、Analyzer或Controller。Knowledge检索和方法存取不执行任何Kernel/Colab/TPU测量。

## Phase 2C 推荐计划（未实施）

1. 明确offline Analyzer输入包：当前候选代码、Task/workload/context、保存的Profile/Benchmark证据、固定MemoryView和带revision的Knowledge检索结果；先写缺失/错绑定/过期来源的拒绝用例。
2. 只读取已保存工件，核对来源、Run/Candidate/context绑定和证据输入链；分别输出事实、估计、假设与预测，不把source_supported当本地性能。
3. 保存数学模型、单位、假设、输入证据ID、公式/计算版本和可复算输出；用手工可核验的合成算例及缺失证据场景测试。
4. 面向每个参与策略读取完整历史与明确父关系，生成多个有依据的Plan草案；禁止在实验前强制只选一个策略或赢家。Plan落库继续复用2A/2B约束。
5. 独立审查离线评估案例与输出协议，报告局限后再讨论执行集成；不在2C提前运行MLA/Colab或声称真实TPU profiling验证。

本轮交付止于Phase 2B，以上只是后续实施建议。
