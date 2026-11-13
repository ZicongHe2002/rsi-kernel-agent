# Phase 2B 独立审查记录

2026-11-11。Reviewer 使用无会话历史的独立上下文，读取本轮规范、计划、新源码/测试和指南；只读checkout，复现仅用临时数据，不创建`.auth`、不修改2A或网站源码。

**最终结论：Ready for Phase 2B review。无未关闭 Critical / Important。** 主代理完整回归和工件核验见 [实现报告](implementation-report.md)。

| 发现 | 修复与回归 |
|---|---|
| Important：Plan 中已执行的旧候选掩盖新候选延期 | 逐个检查尚未执行子候选；有待执行候选则保留 Plan deferred，已执行 Run 不被重标；失败回归后修复 |
| Important：history_page limit=1 仍返回全历史 ID | owned_ids/dependency_ids 改为当前页范围，保留全局 owned_total/dependency_total；页大小回归通过 |
| 健壮性：人工 NaN/1e309 方法 JSON 中断全部检索 | 非有限JSON错误归一为KnowledgeError，坏条目列为unavailable；有效方法继续返回，真实stdio/HTTP回归通过且坏原文保留 |
| 文档审查发现并独立复核：直接追加的 typed 方法链接来源可能互相矛盾 | 读取端核验完整MethodRecord、方法ID、确定性路径、revision、source_refs和关系；5种错误来源与额外字段回归通过 |

另修正严格 schema_version 整数检查；bool/float 不冒充 v1。Plan 固定初始父与 repair 直接父分开，祖先可达性经过失败回归。新增主要功能组件先观察 RED 再实现；综合集成在组件完成后第一次组合即通过，未虚称该项经历缺失实现 RED。

Reviewer 独立运行 **16 Memory + 13 Knowledge + 3 真实 MCP/HTTP = 32 项全部通过**。确认复用原服务/数据库、来源与假设不升级为事实、状态概念独立、A/B历史和失败/修复依赖保留。

保留的 Minor 限制：状态卡证据/frontier/deferred数量无固定上限；完整快照及依赖索引仍有随历史增长的内存成本。指南已说明，分页不等于内部流式存储。

未评判：实际TPU收益/Colab/MLA（不在2B）；生产远端认证/写可用性（临时测试不能证明）；MethodRecord内任意local_experiment_refs真实性（仍为未验证资料字段）；来源事实真实性与旧全文恢复（只保留摘录/版本）；任意task级note语义（调用方定义）；Git clean状态（CLI不可用）。这些范围未被宣称为已验证。
