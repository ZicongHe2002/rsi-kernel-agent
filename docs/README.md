# RSI-Kernel-Agent 文档索引

更新：2026-11-16。当前结论为 **P3.5 系统闭环 PASS_WITH_LIMITATIONS**，**稳定端到端性能 NOT_CONFIRMED**；范围是 MLA Decode contiguous BF16。

## 先读这里

| 文档 | 用途 |
| --- | --- |
| [P3.5 最终验收报告](phase3/phase3-final-acceptance-report.md) | 当前权威结论、两轮结果、原始证据、测试和限制 |
| [第一轮优化结果](phase3/phase3-optimization-report.md) | 4 个 Plan、5 个 Candidate 的真实结果 |
| [ABBA 复测结果](phase3/phase3-p35-abba-results.md) | program 有限复测改善与 host 漂移的独立解释 |
| [最终独立审查](../.artifacts/p35-final-acceptance-2026-11-16/independent-final-review.md) | 审查方法、发现、修复和签结 |
| [验收证据说明](../.artifacts/p35-final-acceptance-2026-11-16/README.md) | 已上传的紧凑证据、完整日志和本地原始数据边界 |

最终报告保持验收签结时的原字节。后续文档整理与发布不改写实验结果、预算、原回执或当时的验收结论。

## 使用与实现指南

| 文档 | 内容 |
| --- | --- |
| [真实执行](phase3/phase3-real-execution.md) | Backend、Colab Bridge、阶段与工件绑定 |
| [MLA runner](phase3/phase3-mla-runner.md) | 固定 runner、加载身份与执行契约 |
| [Workload 与正确性](phase3/phase3-workload-and-correctness.md) | attention、完整 cache、容差与覆盖矩阵 |
| [Worker 恢复](phase3/phase3-worker-recovery.md) | 原 operation 查询、回收、unknown 与信任边界 |
| [预算 grant](phase3/phase3-budget-grants.md) | 保留历史预留、追加限额与审计绑定 |
| [冻结确认协议](phase3/phase3-p35-confirmation-protocol.md) | ABBA 判据与独立 Run 的定义 |
| [基线报告](phase3/phase3-baseline-report.md) · [Profiler 报告](phase3/phase3-profiler-report.md) | 可信基线、负例、真实 XPlane 与指标范围 |

## 架构与已完成阶段

| 阶段 | 接口指南 | 当时的实现与验收 |
| --- | --- | --- |
| Phase 2A：核心与 Store | [核心契约](phase2a-core-foundation.md) | [2A 报告](../knowledge-site/manifests/phase2a-2026-11-11/implementation-report.md) |
| Phase 2B：Knowledge / Memory | [查询与历史](phase2b-knowledge-memory.md) | [2B 报告](../knowledge-site/manifests/phase2b-2026-11-11/implementation-report.md) |
| Phase 2C：Profiler / Analyzer | [分析会话](phase2c-profiler-analyzer.md) | [2C 报告](../knowledge-site/manifests/phase2c-2026-11-11/implementation-report.md) |
| Phase 2D+：Workflow / Controller | [确定性执行流程](phase2d-workflow-controller.md) | [2D+ 报告](../knowledge-site/manifests/phase2d-2026-11-11/implementation-report.md) |
| Phase 3：真实硬件与多轮反馈 | [最终验收](phase3/phase3-final-acceptance-report.md) | [带时间的实现记录](../knowledge-site/manifests/phase3-2026-11-12/implementation-report.md) |

旧阶段报告中的“后续阶段未开始”等表述属于其记录日期。当前整体状态以最终验收报告为准。

## 历史检查点与来源

这些文档保存当时的判断、失败和恢复过程，供追溯使用；当前没有由这些旧计划自动启动的新硬件任务。

- **P3.5 检查点**：[11 月 13 日](phase3/phase3-p35-progress-report-2026-11-13.md)、[11 月 14 日](phase3/phase3-p35-progress-report-2026-11-14.md)、[早期验证记录](phase3/phase3-p35-final-validation.md)。
- **Runtime 恢复计划**：[Runtime 4](phase3/phase3-p35-runtime4-recovery-plan.md)、[Runtime 5](phase3/phase3-p35-runtime5-continuation-plan.md)、[Runtime 6](phase3/phase3-p35-runtime6-continuation-plan.md)。
- **项目与环境审计**：[项目集成](phase3/phase3-project-integration-audit.md)、[环境](phase3/phase3-environment-audit.md)、[Colab 接通](phase3/phase3-colab-connectivity-audit.md)、[PallasKernels](phase3/phase3-pallaskernels-audit.md)。
- **Profile 与官方依据**：[能力审计](phase3/phase3-profiler-capability-audit.md)、[官方版本审计](phase3/phase3-official-reference-audit.md)。
- **历史知识**：[project2 恢复](phase3/phase3-project2-history.md)、[Decode](phase3/mla-decode-history.md)、[Forward](phase3/mla-forward-history.md)、[优化方法](phase3/optimization-methods.md)、[失败实验](phase3/failed-experiments.md)。
- **实施计划**：[Phase 3 原计划](phase3/phase3-implementation-plan.md)、[2A](superpowers/plans/2026-11-11-phase2a-core-foundation.md)、[2B](superpowers/plans/2026-11-11-phase2b-knowledge-memory.md)、[2C](superpowers/plans/2026-11-11-phase2c-profiler-analyzer.md)、[2D+](superpowers/plans/2026-11-11-phase2d-workflow-controller.md)、[P3.5](superpowers/plans/2026-11-13-phase3-p35-validation.md)、[B2 Memory](superpowers/plans/2026-11-14-p35-b2-final-memory.md)、[最终验收](superpowers/plans/2026-11-16-p35-final-acceptance.md)。

## 本次清理

删除了 `knowledge-site/manifests/phase2c-2026-11-11/progress-report.md` 和 `knowledge-site/manifests/phase2d-2026-11-11/progress.md` 两份冗余进度摘要；其内容由上表对应的正式报告与审查记录保存。旧 `changes.json`、`links.json` 及一次性生成脚本中的路径属于历史记录，保持原样。

原始用户请求、冻结协议、失败日志、独立审查、Snapshot/Run 身份和恢复证据继续保留。个人知识笔记、认证信息、运行数据库、下载环境及大型 NPZ/XPlane 不属于 GitHub 源码发布范围。
