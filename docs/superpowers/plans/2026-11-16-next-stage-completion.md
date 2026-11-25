# Next-stage Completion Implementation Plan

**Goal:** 完成用户下一阶段 Prompt 在现有权限范围内的软件实现、验证、真实来源核查及可执行交付准备。

**Architecture:** 复用原 Store、版本化 manifest/Decision、conversation_session、Intake、Controller 和固定 Evaluator。只修复已复现的边界并补必要的兼容接口；真实执行独立检查授权与预算。

**Tech Stack:** Python 3.13 本地、unittest、SQLite、现有 Knowledge MCP；目标 TPU 历史环境 JAX 0.8.1。

**Spec:** 用户附件 `17ede4a8-3b23-4451-b7ee-2afe73763593/Pasted text.txt`，归档到 `.artifacts/next-stage-completion-2026-11-16/request.txt`。依据用户明确要求直接执行，不增加计划确认关口。

## Global Constraints

- 保留当前工作树、P3.5 历史和失败记录；不另建第二个实验数据库。
- 不改 Oracle、容差、计时、晋升、预算或原 operation 身份。
- 不把 Prompt 或剩余一次额度当作新增 TPU 授权。
- 保持 v1/v2 历史输入和 publication 可重放；不宣称跨服务原子快照。
- 所有本地测试使用临时 Store，synthetic 不能计为硬件反馈。

## Review Focus

1. 旧多路线 publication 的本路线分析可读，其他路线内容仍隔离。
2. 同 revision 的不完整来源保留缺项且可读取已保存正文。
3. 暂时故障后的同参数 Intake 重试不重复创建已接受 job 或消耗预算。
4. 同摘要集合下的路径/入口变化不能被错误去重；旧事件去重仍有效。
5. 大输入能通过有界工具完整分页；旧 Runtime 历史不阻塞当前任务，本地已完成回执仍可回收。

## Task 1: Route input and Knowledge provenance

**Files:** `profiler/inputs.py`, `profiler/tools.py`, `tests/test_route_inputs.py`，必要新增独立回归模块。
**Interfaces:** 沿用 `capture_inputs/load_manifest`；新增字段需有格式版本或能力标记，旧 manifest 重放不变。

- [ ] 新增失败用例：两批设计后上一轮 hypothesis/risk 可读；旧多路线结果按路线投影；部分来源 coverage 和正文可见。
- [ ] 运行对应 unittest 并保存红日志。
- [ ] 修复依赖闭包、原始工件验证及来源元数据传播。
- [ ] 回归旧输入、隔离、知识和 publication 测试。

## Task 2: Recoverable Intake and exploration identity

**Files:** `workflow/intake.py`, `workflow/exploration.py`，对应边界/计数测试。
**Interfaces:** Intake 可追加重试但已接受实现/job 不变；探索身份包括规范文件映射与入口，兼容历史身份版本。

- [ ] 先验证临时 Knowledge 故障恢复、路径交换、入口切换的失败用例。
- [ ] 最小修复，保留原 waiting 和历史反馈事件。
- [ ] 测试同 key 重放、部分 accepted、重复回执与配置/文件顺序变化。

## Task 3: Usable context paging and runtime CLI

**Files:** `profiler/context.py`, `profiler/analyzer.py`, `profiler/session.py`, `workflow/__main__.py`，对应编码/session/CLI 测试。
**Interfaces:** 增加跨节点有界分页及可控会话预算，旧协议重放不变；`make_backend` 保留逐请求 Runtime 检查。

- [ ] 先新增多节点大输入完整读取与多 Runtime 历史的失败用例。
- [ ] 实现分页、CLI 配置和必要的后端选择修复。
- [ ] 验证哈希、连续范围、覆盖统计、超限拒绝、未知远端操作不重发。

## Task 4: Live state, sources and delivery preparation

**Files:** `.artifacts/next-stage-completion-2026-11-16/`；原库只读核对，必要准备追加沿用原机制。

- [ ] 当前 Store/账本/授权/operation 核对，保存查询时间和能力差距表。
- [ ] 实际本地 Knowledge 搜索及外部能力核对，保存全文身份与 unknown。
- [ ] 顺序推进 A/B/D 独立分析与候选准备；每个未完成环节给出实测缺项，不伪造读取或执行。
- [ ] 无真实执行权限时固定候选顺序、准确 parent、成本与所缺条件。

## Task 5: Acceptance and documentation

- [ ] 运行根级 `python -B -X utf8 -m unittest discover -s tests -v`；必要时跑历史辅助兼容回归，保存源码身份和完整日志。
- [ ] 独立审查最终改动并解决问题。
- [ ] 更新阶段交付、文档索引及七篇知识站文档，保留旧历史；通过 revision 校验更新周记录。
- [ ] 分开报告软件、真实检索、实际模型设计、TPU 轮次与稳定收益。
