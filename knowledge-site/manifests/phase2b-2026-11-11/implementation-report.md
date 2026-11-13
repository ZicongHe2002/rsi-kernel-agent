# Phase 2B Knowledge / Memory 实现与验收报告

2026-11-11。本轮仅实施 2B，已完成本地验收并停在 2B 等待用户审查。2A schema v1 与既有网站/MCP 实现保持原样；2C、2D、3 未开始。

## 复用能力与变更文件

现有 MCP 的 search / fetch / create_note / update_note、完整分页、原始字节 revision、网站 HTTP 保存锁与现有认证均复用。真实参数、权限边界与连接检查见 [MCP 审计](mcp-audit.md)。搜索和读取在生产本地 MCP 做了只读 smoke；写入在原 MCP + 原 HTTP Handler 的临时库中测试，生产知识方法没有自动批量生成。

Memory 使用原 2A ExperimentStore、类型/ID、追加记录、幂等与版本化 snapshots，不创建第二个实验数据库，不迁移 schema。新方法存放在知识库 Markdown，方法到实验的引用以现有 Decision 追加保存。7 个 2A / 网站受保护源码的起始哈希见 [protected-baseline.json](protected-baseline.json)，验证时逐个比较。

| 文件 | 新接口/职责 |
|---|---|
| [knowledge.py](../../../src/rsi_kernel_agent/knowledge.py) | SourceDocument / SourceRef / MethodRecord；KnowledgeClient 的搜索、完整读取、方法保存/筛选、来源检查、主动发现；extract_method |
| [knowledge_mcp.py](../../../src/rsi_kernel_agent/knowledge_mcp.py) | StdioKnowledge，使用已安装 SDK 和原 mcp_server.py |
| [memory.py](../../../src/rsi_kernel_agent/memory.py) | MemoryReader.open_snapshot；MemoryView.status_cards / history_page / histories / search_history |
| [associations.py](../../../src/rsi_kernel_agent/associations.py) | MemoryWriter.bind_plan / select_parent / link_method，验证精确关系并追加 Decision |
| [test_knowledge.py](../../../tests/test_knowledge.py)、[test_knowledge_mcp.py](../../../tests/test_knowledge_mcp.py) | 方法/来源/分页/CAS及真实 MCP / HTTP 验证 |
| [test_memory.py](../../../tests/test_memory.py)、[test_associations.py](../../../tests/test_associations.py) | 全策略卡片、完整历史、只读/重启、谱系与来源绑定 |
| [test_phase2b_integration.py](../../../tests/test_phase2b_integration.py)、[phase2b_fixtures.py](../../../tests/phase2b_fixtures.py) | 临时知识库与 A/B 合成集成；内存测试凭据，不生成 `.auth` |
| [接口指南](../../../docs/phase2b-knowledge-memory.md)、[实施计划](../../../docs/superpowers/plans/2026-11-11-phase2b-knowledge-memory.md) | 实际 schema/API、语义/限制、先行方案与后续 2C 建议 |

当前路线图、Knowledge/Memory 文档状态、索引和周记录同步更新；Phase 2A 历史报告保留。精确前后文件清单与 SHA-256 见 [changes.json](changes.json)。

## Method Record schema

必含 `method_id`、`method_name`、`description`、`source_refs`、`source_revision`、`optimization_category`、`applicable_hardware`、`applicable_workloads`、`preconditions`、`implementation_requirements`、`expected_tradeoffs`、`known_risks`、`unknowns`、`local_experiment_refs`、`validation_status`；另有用于过滤的 `mechanism`。完整字段类型见接口指南。

SourceRef 保存 document_id / revision / locator / excerpt / availability / reason，source_revision 映射须与全部来源一致。原文 revision 是 MCP 返回的原始字节 SHA-256，不能重编码正文后冒充该版本。丢失或变化的来源分别显示 unavailable / stale，保留原引用和摘录。

方法以确定性 `RSI/Methods/method-<SHA256(method_id)>.md` 保存，Markdown 中一个 `rsi-method-v1` JSON 块。重复相同记录返回原版本；异值需明确 expected_revision；更新保留块外人工说明。错 revision 拒绝覆盖，损坏记录明确列为 unavailable。提取只对明确唯一的 Markdown 章节保留原文，不推断未提供的硬件效果或前置条件。

validation_status 限 source_supported / unverified / synthetic_only。来源出现于论文或官方说明不会自动获得 locally_verified。`local_experiment_refs` 只是方法中的未验证引用；MemoryWriter 的显式 plan/candidate/run/evidence 参数才按现有实验关系核验。可信 Benchmark 的评判属于后续阶段。

## 两阶段 Memory 与关联示例

调用方先显式建立快照；其后的读取不写数据库：

```python
snapshot = store.create_snapshot("task", "round:1")
view = MemoryReader(store).open_snapshot(snapshot["id"], page_size=4)
cards = view.status_cards("context")          # A、B、C、D、E 全部返回
histories = view.histories(("A", "B"))        # 同轮两条路线分别完整读取
page = view.history_page("B", offset=0, limit=10)
```

状态卡分别暴露 latest_attempt、last_correct_candidate、branch_best、task_best、frontier、paused、deferred、current_parent_candidate、recent_plans/evidence_refs。没有明确父选择时 parent 为 null，不能猜成 latest 或 best。晚到的旧 Run 报告不会被当作新尝试；正确但计时无效的候选可以是 last_correct，却不是 best。

集成 fixture 中 A/B 各 2 个 Plan、每 Plan 2 个 Candidate，三个来源方法（Overlap / Reuse / Contiguity）形成五条明确Plan关联，B2可关联多个方法；A 成功计时人为设为10 ms且有失败Run，B成功计时人为设为12 ms且有invalid Run；B22延期、B21保留探索、E暂停。两项计时始终标为 **synthetic，仅为软件测试**。关键卡片摘录：

```json
{
  "strategy_id": "B",
  "provenance": "synthetic",
  "branch_best": {"candidate_id": "B11", "run_id": "B-success", "latency_ms": 12},
  "task_best": {"candidate_id": "A11", "run_id": "A-success", "latency_ms": 10},
  "frontier": ["B21"],
  "paused": false,
  "deferred": [{"record_id": "B22", "kind": "Candidate"}],
  "current_parent_candidate": {"parent_id": "baseline"}
}
```

此处为字段摘录；真实完整运行输出见 [synthetic-example.json](synthetic-example.json)，含全部卡片、A/B完整历史及方法链接。该例A完整历史25条（21条owned、4条依赖），B为28条（24条owned、4条依赖）。history页的 owned_ids / dependency_ids仅描述当前页，owned_total / dependency_total是完整集合计数；单独末页不标complete。完整历史保留失败、invalid、repair、deferred、incomplete、Profile/证据/决策，以及共享契约和必要祖先依赖。源码实现可取得的内容仍是2A已有的身份/工件引用，不凭摘要虚构缺失代码。

知识到 Plan 的示例流程：

```python
source = await knowledge.fetch_document("Research/Pipelining.md")
method = extract_method(source, "Overlap", hardware=("synthetic CPU",), mechanism="overlap")
saved = await knowledge.save_method(method)
writer.bind_plan("A1", "baseline", "bind:A1")
link = writer.link_method(method, saved["id"], saved["revision"],
                          "A1", "baseline", "Explore source mechanism", "link:A1")
writer.select_parent("A", "context", "baseline", "Explicit baseline", "parent:A")
```

知识链接保存完整method_snapshot、source_refs和方法note revision，后续人工编辑不改历史。Plan初始父固定；repair的直接父可不同，但祖先必须到达固定基点，且关联额外记录candidate_parent_id。A更快不会取消B。

## 验证

完整根级套件 **85 项：84 通过、1 跳过、0 失败/错误**，其中新增 2B 的 40 项全部通过；既有只读 MCP 回归 **5/5 通过**。唯一跳过沿用 2A 的 Windows symlink 权限不足测试（WinError 1314）；junction 拒绝及其他工件测试通过，未改变系统权限。环境仍为 Python 3.13.14、SQLite 3.53.1、MCP 1.30.0、Pydantic 2.14.2，无安装/升级。

```text
Ran 85 tests in 19.524s
OK (skipped=1)

Ran 5 tests in 0.748s
OK
```

日志中 asyncio 对同步临时数据库/HTTP fixture 操作有 slow-callback 诊断；测试退出码均为0，没有将这些诊断隐藏为无输出。独立reviewer另外运行32项相关测试全部通过，最终无未关闭Critical/Important；状态卡大小与内存成本作为已记录的Minor限制保留。

最终命令、环境和完整结果见 [test-environment.json](test-environment.json)、[tests.txt](tests.txt)、[existing-mcp-tests.txt](existing-mcp-tests.txt)。真实生产只读探针见 [live-read-smoke.json](live-read-smoke.json)，其查询总数是当时状态，不是固定预期。

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -m unittest discover -s tests -v
# 在 knowledge-site 目录运行既有只读 MCP 回归：
& '.\.venv-mcp\Scripts\python.exe' -B -m unittest test_mcp.MCPTests -v
```

知识/Memory/关联模块测试先观察缺失实现或行为缺陷的失败，再实现；跨模块最终集成在组件各自通过后第一次组合执行即通过，没有将该组合测试虚称为缺失实现 RED。独立审查及修复记录见 [review.md](review.md)。

测试覆盖来源revision/BOM/分页改版、方法检索/去重/人工CAS/缺失来源、原HTTP拒绝错误token、ABCDE状态卡、A/B完整历史、失败延期保留、修复谱系、跨进程恢复、并发只读及关联错配拒绝。集成中源码不替换网站或认证；临时server仅以内存fixture身份执行原Handler，未创建`.auth`，不代表验证生产远端ChatGPT授权。

本周完成记录已补充，未勾选无关待办。本地 revision API 返回401，因此保留原字节快照、写前哈希核对并重新读取合并后原子替换，未读取认证文件。12份Markdown的105个相对链接均有效，结果见 [links.json](links.json)。

## 限制与后续

- search为实时子串匹配，无全库快照保证；complete表示取完可观察分页，并明确snapshot_consistent=false。
- 方法保存摘录和版本，不自动归档来源全文旧版本；local_experiment_refs不自动验证或升级为实测事实。
- Memory沿用2A完整加载快照的空间成本；历史分页控制响应大小，内部不是流式大规模索引。状态卡证据ID/frontier/deferred列表无固定上限，后续提示词需明确摘要策略。
- Plan父绑定是2B应用层Decision；绕过helper的低层写入仍可能追加不符合2B语义的记录，读取端会拒绝已知无效结构，不自动删除历史。
- MCP写超时可能已提交；创建可按确定性路径核对，更新需重读并合并。未验证远端ChatGPT隧道，不修改认证和权限来达成连接。
- Git CLI仍不可用；确认已有main/HEAD元数据，不声称工作树干净，未创建提交。

建议2C按接口指南末节实施：先定义离线输入包和证据绑定校验，再做可复算数学模型/单位/假设记录，读取每条参与策略完整历史后生成多个Plan草案，用保存或合成fixtures评估。**本轮只提出计划，不实现Analyzer，不访问Colab或MLA仓库，不运行TPU。**
