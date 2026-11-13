# Phase 2B Knowledge and Memory Implementation Plan

> **For agentic workers:** 用户已授权本轮实施 2B；采用测试先行、并行独立模块及无会话历史的代码审查。缺少的执行技能不安装。完成本计划后停止等待审查，不实施 2C、2D、3。

**Goal:** 复用现有知识库 MCP 和 2A SQLite，使方法检索、来源追踪、全策略状态卡、各参与策略完整历史及知识到实验关联可用。

**Architecture:** Knowledge 通过既有 MCP 搜索/分页读取/带 revision 写入 Markdown 方法记录；Memory 从显式创建的 2A 快照生成只读视图。关联与精确父选择保存为现有追加 Decision，schema 维持 v1，知识库与实验数据库分开。

**Tech Stack:** 现有 Python 3.13、stdlib、已安装 MCP 1.30.0 / Pydantic 2.14.2。无新依赖，既有网站/MCP/认证实现和 2A 文件保持原样。

**Spec:** [本轮用户要求](../../../knowledge-site/manifests/phase2b-2026-11-11/sources/phase2b-request.txt)、[2A API](../../phase2a-core-foundation.md)、[Memory 语义](../../../knowledge-site/content/学习方法/rsi-memory.md)、[Knowledge 语义](../../../knowledge-site/content/学习方法/rsi-knowledge-search.md)。

## Global Constraints

- 只实现 2B；不生成优化 Plans 的 LLM、不实现 Analyzer、执行队列或真实 Kernel Adapter。
- 不访问/执行 Colab，不改 MLA 仓库，不运行 TPU；不修改 `.auth`、账号权限、全局配置，不替换 MCP 认证。
- 不删除历史文档，不重写 2A 历史记录/schema/报告，不安装或升级任何依赖/Skill。
- source-supported 不等于本地测量；合成实验始终带 synthetic context。
- 所有策略都可参与同一轮，不因某策略最佳更快而取消其他策略。

## 实际复用与审查重点

现有 `knowledge-site/mcp_server.py` 提供 search / fetch / create_note / update_note。search 是实时子串匹配，无语料快照 token；fetch 每页带原始字节 SHA256 revision（含 BOM），offset 以字符计数。写入经 loopback HTTP、既有 local_headers 与网站 revision lock，health 校验同库路径；409 必须重新读取合并，不能盲重试。stdio 为私有 owner 边界，search/fetch 不套用网页成员 ACL。生产连接由可信调用方固定脚本/解释器，不暴露为模型可控路径。

2A 已有 Task/Strategy/Plan/Candidate/ParentCandidate/Run/Evidence/Bundle/Decision 及快照。现有 `Decision(kind='note', details=...)` 足以保存版本化关联；无需 schema 变更或迁移。2B 的写入 helper 验证这些 details 的引用与归属；旧无此语义的 note 不自动解释成事实。

1. 文档读到一半改版或 BOM：逐页核对 id/revision/字符覆盖，冲突显式拒绝，不自算规范文本摘要冒充原始 revision。
2. 人工编辑/同方法重复保存：确定性 note ID、明确 CAS、异值冲突；读取不创建记录，不覆盖未知人工文本。
3. 最新/正确/最佳/父选择混淆：在同快照/context 独立派生，父必须来自明确 Decision，缺失为 null。
4. 分页漏页/重复或遗漏跨分支祖先：核对总覆盖和 strategy_counts，完整历史包含 owned 与必要 dependency 引用，摘要不替代原记录。
5. 错策略/Plan/Candidate/Run/证据关联：现有类型与关系核验，方法完整内容及 note revision/source refs 固定在追加 Decision，不因资料变化悄悄改历史。

## Task 1: Knowledge MCP 薄层

**Files:** 新建 `src/rsi_kernel_agent/knowledge.py`、`knowledge_mcp.py`；测试 `tests/test_knowledge.py`、`test_knowledge_mcp.py`。

**Interfaces:** `KnowledgeClient(async_call_tool)` 的 async search、fetch_document、save_method、get_method、find_methods、discover、source_status；`StdioKnowledge(website_dir, python_executable, entrypoint=None)` 复用现有 stdio server。`SourceDocument`、`SourceRef`、`MethodRecord`、method_to_dict/from_dict、method_note_id、extract_method。

MethodRecord 含全部用户要求字段与 mechanism：method_id、method_name、description、source_refs、source_revision 映射、optimization_category、applicable_hardware/workloads、preconditions、implementation_requirements、expected_tradeoffs、known_risks、unknowns、local_experiment_refs、validation_status。每个来源保留 document_id、revision、locator、原文摘录及可用性。方法保存于专用 `RSI/Methods` Markdown note，ID 转确定性安全标题。

- [x] 先写并运行失败测试：搜索/完整分页、改版/BOM、缺失来源、确定性去重、按分类/硬件/机制/适用范围检索、人类 revision 冲突。
- [x] 实现最小方法契约、显式章节提取和查询/发现；默认未知限制，不杜撰优化结论或自动升级 locally_verified。
- [x] 经真实 stdio MCP 验证搜索/读取；使用临时内容和测试内存认证夹具验证现有 HTTP Handler 的写入/冲突，不创建或改动任何 `.auth`。

## Task 2: 两阶段 Memory 只读视图

**Files:** 新建 `src/rsi_kernel_agent/memory.py`；测试 `tests/test_memory.py`。

**Interfaces:** `MemoryReader(store).open_snapshot(snapshot_id,page_size=100)` → `MemoryView`；`status_cards(context_id,recent_plan_limit=5)`；`history_page(strategy_id,offset=0,limit=100)`；`histories(strategy_ids)`；`search_history(query='',strategy_ids=None,context_id=None,kinds=None)`。

快照由调用方显式 `store.create_snapshot(task_id,key)` 创建，读取不自动写库。返回 detached read views，固定 snapshot ID/version/digest/high-water mark。状态卡展示所有策略的 latest_attempt、last_correct_candidate、branch_best、task_best、frontier、paused、deferred、current_parent_candidate、recent_plans 及 evidence_refs。历史页保留全部成功/失败/invalid/repair/deferred/incomplete 和决策，标明本策略 owned 与必要依赖，不无声截断。

- [x] 写并观察失败：ABCDE 全卡、A/B 同轮历史独立、旧 Run 新报告不冒充最新尝试、正确但慢/计时无效候选、显式父与最佳不同。
- [x] 写并观察失败：分页漏页/重复/变版拒绝、跨策略祖先/来源链接保留、缺失策略拒绝、快照之后写入隔离、并发只读不写记录。
- [x] 实现状态派生、完整历史与明确 filtered search；以多策略 fixture 验证不硬编码 A–E。

## Task 3: 关联、集成与交付

**Files:** 新建 `src/rsi_kernel_agent/associations.py`；测试 `tests/test_associations.py`、`test_phase2b_integration.py`；指南 `docs/phase2b-knowledge-memory.md`、报告/清单位于 `knowledge-site/manifests/phase2b-2026-11-11/`。

**Interfaces:** `MemoryWriter(store).bind_plan(plan_id,parent_id,key)` 固定 Plan 的确切父；`select_parent(strategy_id,context_id,parent_id,reason,key)` 明确当前父；`link_method(method,method_note_id,method_note_revision,plan_id,parent_id,reason,key,candidate_id=None,run_id=None,evidence_ids=())` 追加源方法快照与关系；其余 pause/defer/note 沿用现有 Decision append。

- [x] 写失败测试：关系错配/跨任务拒绝、Plan 父绑定冲突、重复请求无重复、方法记录不变成本地性能事实；实现并验证。
- [x] 建真实 MCP + 临时网站 + SQLite 合成集成例：A/B 多 Plan/候选、成功失败延期、数个来源方法、明确方法到 Plan 链接、独立历史重建与进程重启。
- [x] 运行全部根级单元/集成回归和相关现有 MCP 只读测试；保存命令、完整输出、环境/skip 和源文件哈希。
- [x] 独立 reviewer 审查，缺陷先失败回归后修复；复跑受影响及最终完整套件。
- [x] 提交复用能力、文件/API、方法 schema、卡片/历史/关联示例、结果和限制；只提出 2C 计划，不实施。
- [x] 用 revision 或 hash 合并本周完成记录；保留2A历史报告，更新当前路线图状态；停止等待用户审查。
