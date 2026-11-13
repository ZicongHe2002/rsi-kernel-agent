# Intake / Round / Synthetic Demo 实施记录

2026-11-11。本文为组件实施与验证记录，最终全量结果以本阶段实现报告为准。

## 实际接口

- `async intake_publication(store, publication_id, round_id, variant_specs, knowledge=None)` 消费原 2C `analysis_result.v1` 最终完成 Decision。回读 manifest/result 工件、重放 Analyzer exchanges、核对原 Plan/精确父/逐 Run AnalysisBundle。每 Plan 的 accepted/rejected/waiting、原因和 job 列表保存在一个原 Store Decision 事务中，没有部分 intake 前缀。
- `validate_variant(store, variant, label=None)` 复用执行模块的固定字段/工件/有限数值/预算校验，再核对变体 label 与发布提案原文完全一致。不同 Plan 的变体数量独立，数量及顺序与各自发布的 variants 对应。
- `async prepare_round(store, knowledge, task_id, context_id, strategies, round_id, knowledge_refs, *, parents=None, parent_policy='branch_best')` 返回 manifest/request/snapshot/cards/histories。branch_best 从固定输入 snapshot 分别选择每条策略的 Best；无 Best 时使用此前显式父选择。explicit 要求逐策略提供 ParentCandidate。选择记录通过原 MemoryWriter 追加，随后建立包含精确父与选择的新 snapshot，再调用原 capture_inputs。
- `async run_demo(directory, *, website_dir=None)` 要求新目录，运行两轮完整软件流程；写 `report.json`。统一 CLI 由 workflow 主入口包装该函数。

Intake 和 Round preparation 均持有每 Task 的原新建 workflow OS 锁，与 Controller 互斥。重复相同 intake/round 返回已保存结果，异值复用身份拒绝。某 publication 已有 accepted Plan 后，不能通过改 round_id 再产生实验；必须使用新分析 publication，或显式 Controller retry。waiting 结果是不可变历史：依赖恢复后建立新 round；来源 revision 改变时应重新分析。

Knowledge 通过现有客户端检查方法与来源实时 revision。Plan 直接 refs、observation_ids 的引用以及 calculation_ids 的证据输入均参与检查。缺失/过期的引用等待；非法变体/工件损坏拒绝并保留原因。此前已归档的旧来源不替换成新内容。实时检查只能证明检查时观察到的 revision，不保证整个知识库原子快照，也不锁住后续人工编辑。

统一固定契约含 Task 完整记录（包括 contract_revision）、Workload.spec、EvaluationContext 完整记录和固定 evaluator policy。模型验证文字不能替换这些契约。每 job 保存精确 parent_evaluation_id，用于稳定比较；是否 Best 晋升仍由原 store.consider 决定。

## Demo 的真实性边界

Demo 使用独立目录中的 SQLite、工件、候选工作区、源码方法 Markdown 与会话记录。StdioKnowledge 启动原 `knowledge-site/mcp_server.py::build_server`，仅给它隔离 content 目录；没有第二套 MCP/Knowledge 实现，没有 HTTP 保存、认证读取或 `.auth` 创建。

固定响应明确标为 recorded synthetic demonstration，并通过原 start_session/submit_response 完成工具读取和最终方案验证。它是软件验收 fixture，**不是实时模型优化能力测量**。同一生产流程允许宿主用当前对话模型给出响应。

第一轮 A/B 各两 Plan，变体数量 2/1/1/2；同时包括成功、编译失败、正确性失败、timeout 和 invalid measurement。编译失败通过一个明确的 recorded repair response 形成新 Candidate，保留原失败。第二轮从新 Memory snapshot 重新读取 A–E 卡片、A/B 完整历史和对应新父 Profile，再发布和执行四个 Plan。

基线 10/12 ms、最终 A 7 ms / B 9 ms 全部是人为设置的 synthetic 软件数据；B 保留 Frontier。没有执行源码、Kernel、TPU、Colab 或真实 XPlane 解码。

## 测试先行及已观察结果

新组件测试先断言缺失模块并观察真实失败：Intake 8 项、Round 4 项、Demo 1 项。实现后另补两个实际缺陷回归：同 publication 跨 round 重复实验、Plan 间接 observation 引用漏实时检查；均先失败，再修复。Round 持锁互斥测试也先观察未拒绝，再添加锁。

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 -m unittest discover -s tests -p test_workflow_intake.py -v
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 -m unittest discover -s tests -p test_workflow_rounds.py -v
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 -m unittest discover -s tests -p test_workflow_demo.py -v
```

组件运行观察：Intake 10/10 通过（22.422s）；Round 最终 5 项覆盖精确新父/缺失 Profile/旧轮冻结/全部卡片/互斥；Demo 1/1 通过（78.281s）。Demo 运行较慢，主要为真实 SQLite 持久写、追加完整 workflow 状态、完整历史与 manifest 重放。asyncio slow-callback 诊断原样输出，不是硬件延迟。

一次中间 Intake 套件失败来自测试在三组非法变体案例中复用同一 publication：新增全局去重正确拒绝后两组。测试改为每组通过真实 publish_result 建立独立 publication，保留原本“非法局部变体不影响其他 Plan”的预期，没有降低断言。

所有解释器调用沿用现有虚拟环境；初次沙箱 uv trampoline 拒绝后用受限自动审批执行同一解释器，无安装或升级。完整根级回归、审查与最终 Demo 导出由集成阶段执行并记录。
