# RSI 知识库工程索引

> 2026-11-11｜仓库维护入口。网站可读正文位于现有content目录；未创建嵌套knowledge-site。

| 入口 | 内容与状态 |
|---|---|
| [文档索引与权威图](content/学习方法/rsi-document-index.md) | 当前模块、实践、归档和缺失来源 |
| [系统总览](content/学习方法/rsi-kernel-agent.md) | 多策略闭环；Profiler → Analyzer |
| [Phase 2–3路线图](content/学习方法/RSI实践/rsi-phase2-roadmap.md) | 2A核心基础 → 2B知识/Memory → 2C分析 → 2D工作流 → 3最终MLA/Colab；2D+ 已本地验收；Phase 3 真实集成进行中，完整硬件闭环未验收 |
| [Phase 3进展报告](manifests/phase3-2026-11-12/implementation-report.md) · [P3.5验证记录](../docs/phase3/phase3-p35-final-validation.md) | 2026-11-13 19:07：首轮与ABBA已完成；host漂移，稳定端到端收益未确认。Runtime4已取得，准入待恢复；第二轮真实实验与单tile诊断尚未完成 |
| [Phase 2D+实现报告](manifests/phase2d-2026-11-11/implementation-report.md) · [接口指南](../docs/phase2d-workflow-controller.md) | 确定性 Controller、持久回执恢复、原接口两轮 synthetic 软件闭环；已通过本地验收，等待用户审查 |
| [Phase 2C实现报告](manifests/phase2c-2026-11-11/implementation-report.md) · [接口指南](../docs/phase2c-profiler-analyzer.md) | 保存Profile、四类数学工具、会话模型多Plan与固定评估 |
| [Phase 2B实现报告](manifests/phase2b-2026-11-11/implementation-report.md) | 原MCP方法/来源、全部策略卡片与独立历史；2B新增40项测试通过 |
| [Knowledge/Memory API指南](../docs/phase2b-knowledge-memory.md) | 方法schema、完整分页、关联示例和2C建议 |
| [Phase 2A实现报告](manifests/phase2a-2026-11-11/implementation-report.md) | 核心契约、SQLite/工件、状态与快照；44通过、1项环境跳过 |
| [核心API指南](../docs/phase2a-core-foundation.md) | 数据字段、schema、合成示例与恢复边界 |
| [MLA集成审计](manifests/mla-integration-audit.md) | 精确实现、reference/测试/配置保护、已有capture/parser |
| [MLA源码清单](manifests/mla-source-manifest.json) | 原仓库源码哈希、两文件allowlist、内嵌oracle保护 |
| [Colab CLI审计](manifests/colab-cli-audit.md) | 真实命令/返回/超时/恢复/路径/工件契约 |
| [Colab来源清单](manifests/colab-source-manifest.json) | 只读核对过的CLI源码和说明指纹 |
| [验证报告](manifests/phase1-validation.md) | 链接、图片、Mermaid、公式、来源保留及限制 |
| [变更报告](manifests/phase1-change-report.md) | 精确文件变更和当前完成边界 |
| [变更哈希清单](manifests/phase1-changes.json) | 修改前后SHA-256；新增项before=null |
| [文件/来源/资产清单](manifests/site-inventory.json) · [外部来源](manifests/source-manifest.json) | 路径、大小、状态、哈希、链接与来源版本缺口 |
| [权限政策](access/ACCESS_POLICY.md) | 仅记录边界，不施加ACL |
| [认证迁移提案](access/AUTH_STATE_MIGRATION_PLAN.md) | 未实施，`.auth`保持不动 |
| [.gitignore提案](access/gitignore.proposed) | 未激活；验证范围见报告 |
| [连接说明](connections/README.md) | MCP含写入，账号连接与Colab执行分开 |
| [原文与审批快照](manifests/phase1-2026-11-11/before.json) | 原文哈希及snapshot路径，避免内容丢失 |

现有[网站README](README.md)描述实际网站功能。`rsi-codex-knowledge-site-bootstrap`仍是支持模板包，未整体迁入或删除。完整Phase 0结论在会话中；本次源码审计补充了原仓库以外的真实MLA与Colab资产，不将旧“MISSING”误用于这些新资源。
