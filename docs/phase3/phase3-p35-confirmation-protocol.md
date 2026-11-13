# P3.5 独立复测协议与预算

2026-11-13。此协议在本轮新硬件结果产生前制定。已有 Store 晋升仅比较正确、有效且 confirmed 的 latency median，没有跨 Run 稳定性阈值。本协议独立确认有限重复中的表现，不改变原 `consider` 或已完成晋升。

## 顺序与范围

使用新 Runtime 中重新准入的原 B baseline 与 delayed-store-wait，按 control、treatment、treatment、control 顺序执行四个独立 Run。每个 Run 继续原五阶段与固定正确性、5 次 warmup、20 次正式 host 样本和独立 Profile operation；同一 Runtime、Context、输入、计时边界、解析器与环境。保留全部原始样本，不删除 outlier。两个重复必须保持各自 source/config/build 身份。

新环境准入包含 A baseline、B treatment、原 B baseline control 与 attention/cache 两个错误负例，共五个 Run。B control 沿用原 B Candidate 来源；不能把 A 的测量改标给 B。旧 Runtime 2 的结果保留。

## 预先固定的判断

机器规则文件是 `.artifacts/phase3-2026-11-12/p35-confirmation-policy.json`，版本 `p35-abba-v1`。在确认轮 Intake 前将其及准确 selection 写入原 Store 的不可变 policy Decision。

分别对 host median（ms）与 XPlane program median（μs）应用：

1. 配对 C1/T1 和 C2/T2 的 `(C−T)/C` 均至少 2%。
2. treatment 的两个 Run median 均严格低于两个 control median。
3. 每个角色的两个 Run median 满足 `(max−min)/median ≤ 5%`。

任一角色超过 5% 漂移，该维度为 `inconclusive_drift`；未超漂移但不满足改善条件为 `not_confirmed`；全部满足为 `replicated_observed_improvement`。host 与 program 分别报告，可只有一个改善。prepare/attention 时长只作描述，不自动证明 DMA overlap 或资源利用率。

2%/5% 是本轮预先约定的工程判据，不是已有测量精度或统计显著性结论。每角色仅两次独立 Run，不能把每次 20 个相关样本当作 40 次独立实验；不声称置信区间、普遍稳定加速或其他 shape 也改善。身份、输入、顺序、原始工件或回执不完整时拒绝分析，不能归为已验证改善。不会看结果后修改阈值。

## 原预算与分账

只读审计确认原 Controller 限额为 16 executions / 28800 声明秒，Round 1 使用 5 / 9000，剩余 11 / 19800。声明秒不等于实际计费或 wall-clock。

| 用途 | 探索预算预留 | 执行前条件 |
|---|---:|---|
| ABBA | 4 / 7200 声明秒 | 新环境五项准入通过 |
| Round 2 新候选 | 最多 4 / 7200 | 新 Snapshot 包含 ABBA 结论与完整 Round 1 历史；计划必须有实际区别 |
| Single-tile 独立边界诊断 | 2 / 3600 | 合法新 Workload/Context，原 baseline 与 single-tile 对照 |
| Repair 预留 | 1 / 1800 | 原深度/次数限制，新 Candidate |

五项环境重新准入继续已有 `prerequisite_reservation.v1` 单独记账；保存每项实际声明成本，不称免费，也不重置或重复消费探索预算。若 Round 2 计划数量或修复需求变化，先按剩余预算明确 deferred，优先保留真实第二轮新候选及原始数据。

## 证据进入下一轮

确认轮结束后，复核四项原 Controller 完成结果、原始 NPZ、全部工件哈希、计时数组、保存 parser 结果与 Run 绑定。共享报告放入唯一 ArtifactStore；Task Decision 与两个参与 Strategy 的 association note 引用报告并携带统计结论。Round 2 在新 Snapshot 中读到这些记录后才能准备与执行。无收益或漂移结论也可以进入第二轮；不存在要求模型先宣布赢家的门槛。

独立复核不会把重算保存 parser 结果描述成重新解码 XPlane protobuf，也不会将 runner 的输入 cache 不变性检查声称为第二次独立设备验证。
