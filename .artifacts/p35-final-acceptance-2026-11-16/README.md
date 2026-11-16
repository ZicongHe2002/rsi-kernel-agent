# P3.5 最终验收证据

验收日期：2026-11-16。先读[最终验收报告](../../docs/phase3/phase3-final-acceptance-report.md)；项目导航见[文档索引](../../docs/README.md)。

## 已发布的紧凑证据

| 内容 | 入口 |
| --- | --- |
| 原始 Run、NPZ 与 XPlane 审计 | [说明](raw-audit.md) · [逐 Run 结果](raw-audit-results.json) · [脚本](raw-audit.py) |
| Memory、Snapshot、预算与恢复 | [说明](memory-audit-report.md) · [完整结果](memory-audit-result.json) · [恢复核验](memory-audit-recovery.json) |
| 第一轮到第二轮的分析与源码谱系 | [说明](traceability-review.md) · [事实](traceability-facts.json) |
| 独立最终审查 | [签结记录](independent-final-review.md) |
| 根级完整回归 | [结果](root-tests.result.json) · [stdout](root-tests.stdout.txt) · [stderr](root-tests.stderr.txt) |
| 辅助完整回归 | [结果](auxiliary-tests.result.json) · [stdout](auxiliary-tests.stdout.txt) · [stderr](auxiliary-tests.stderr.txt) · [去重计数](test-discovery-accounting.json) |
| 首次失败与最小修复 | [原失败结果](regression-attempt-1/auxiliary-tests.result.json) · [预算夹具](budget-fixture-debugging.md) · [异步清理](traceability-recovery-debug.md) |
| 官方参考与交付一致性 | [解释复核](official-reference-interpretation.md) · [当时的交付核验](delivery-verification.json) |

本目录保留签结时的原始报告、JSON、测试日志及红→绿证据。首次失败没有删除或替换成成功，测试运行的源文件摘要也予以保存。

## 数据与复现边界

GitHub 发布包含紧凑审计结果与脚本，不包含完整实验数据库、内容寻址的大型 NPZ/XPlane、下载环境、认证状态或个人周清单。因此可在 GitHub 阅读证据链、审查代码与日志；完整原始数组/Trace 审计仍需要原工作区的工件目录和 SQLite Store。

JSON 中的本地绝对路径、原 Snapshot/Run ID 和哈希是当时审计的身份记录，不是可从 GitHub 下载的完整数据清单。`delivery-verification.json` 保存 12:11 的交付状态；之后的文档索引整理不改写该历史记录。源码与实验契约保持验收时版本，当前发布核验另行保存。

以下内容留在本机：个人周记录及其更新脚本、进程运行标记、临时进度文件、探索性的数据库检查输出。原始任务、失败日志、修复前源码和正式审查结果属于证据，继续发布。

本地重新运行审计前先阅读脚本约束；测试或审计文件中的 `verified` 不能替代缺失的原始工件。本次文档发布不会申请 TPU、创建新 Run 或修改实验 Store。
