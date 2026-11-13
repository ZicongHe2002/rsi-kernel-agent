# Phase 2A 独立代码审查记录

2026-11-11。Reviewer 使用无会话历史的独立上下文，读取用户规范、计划、4 个运行时文件、4 个测试文件和实现指南；只读工作树，复现只使用临时数据库与工件。

**最终结论：Ready for Phase 2A review；无未关闭 Critical、Important 或 Minor。** 完整测试随后由主代理验证，结果见 [实现报告](implementation-report.md)。

| Important 发现 | 复现与修复 | 独立复核 |
|---|---|---|
| 旧 RunEvent ID 换键重放返回成功但未追加状态 | defer → ready 后重用原 defer ID；原 `_insert` 返回既有记录。现有请求键仍可重试，其他事件 ID 复用拒绝 | 专项回归通过 |
| 派生计时工件完整掩盖上游 raw 工件损坏 | 原晋升仅检查 Bundle 直接证据。改为带 visited 集合遍历全部 `input_ids` 依赖 | 嵌套上游损坏回归通过 |
| 并发首次打开空数据库立即遇到 WAL 锁错误 | `PRAGMA journal_mode=WAL` 在此场景不一定等待 busy timeout。改为事务外对 SQLITE_BUSY/LOCKED 做有时限重试 | 30 轮 × 8 constructor：修复前 63 错误，修复后 0 |

独立执行的 5 项回归覆盖事件复用、并发 CAS、同键/终态 CAS、嵌套证据损坏和首次初始化，均通过。主代理另根据文档检查补充 schema/trigger 匹配校验，并完成整个套件。

Reviewer 确认：不可变关系保留精确父和失败历史；事务请求/CAS/严格较优晋升约束并发；快照冻结记录和投影，分页不误报完整；指南明确可信声明和合成测试边界。

明确未评判：真实 Kernel correctness/performance、实际已加载源码/编译产物身份、TPU/MLA/Colab 行为（均在后续阶段）；断电/分布式文件系统/恶意本地写入（超出已测信任模型）；大历史规模性能（完整 JSON 加载限制已记录）。这些不被当作本轮已验证能力。
