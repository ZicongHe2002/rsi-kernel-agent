# 历史优化方法与适用边界

2026-11-12。本页方法来自 project2，有来源支持，当前Task效果均未验证。10 项方法已通过原Knowledge体系保存为 `source_supported`，不能标为本轮 locally_verified。精确出处与SHA见 [恢复清单](../../knowledge-site/manifests/phase3-2026-11-12/history-experiments.json)；来源缺失或版本变化须保留stale/unavailable语义。

| 方法ID/机制 | 实现要求与适用条件 | 资源成本、风险、未知项 | 历史证据 |
|---|---|---|---|
| hist-fwd-qk256 / dot geometry | NoPE128+RoPE64拼接并pad reduction至256；保持scale、mask和输出 | 增加padding运算/存储；编译器可能DCE；不等于KV-output256或Dv256 | Forward trajectory阶段3；旧主线稳定小收益，部分底层证据本轮未逐个核验 |
| hist-fwd-causal-panel / causal work pruning | 对角宏块划分合法panel；保留online-softmax递推、LSE | 小panel增加归约/循环；低spill不一定更快 | Result5 N6、Result6 N14报告；当前实现可能已包含 |
| hist-fwd-layout-r25 / layout | score[K,Q]、P沿K归约、accumulator[Dv,Q]；计入最终layout恢复 | 布局转换/广播/精度；JAX版本约束；不能从LLO缺失推断spill收益 | result14同轮R20→R25，32样本0.40656375→0.37364ms；source/trace/raw均已核验 |
| hist-fwd-projection-group / data reuse | 多head共享输入的KV projection grouping/fusion；完整pipeline比较 | 更宽live state与输出布局，可能抵消复用 | Result6 E2报告；result14 R21/R22/R24配方，部分Q事件未识别 |
| hist-fwd-state-lifetime / online recurrence | 首块直接初始化、未归一化numerator/denominator、paired Q | 数值递推必须等价；state增大、编译broadcast限制；组合收益不能相加 | BDU01历史与result14四项LSE编译失败；不应盲复刻 |
| hist-decode-dma-alignment / DMA layout | 实际storage576→640、128对齐，再DMA；准备工作留计时内 | padding/copy成本；不能只改逻辑shape；不同batch/layout编译限制 | Decode round2 R2失败，round3 A系列真实通过；已核验来源与结果 |
| hist-decode-fp32-score / numerical correctness | 原BF16-score边界失准时保留FP32 score，固定容差 | 更大state/带宽；不能以牺牲边界正确性换速度 | round2 G_X0八项失败，F32S对照通过，原始gates已保留 |
| hist-decode-dma2 / prefetch pipeline | 固定tile/精度/布局后对比单/双buffer并等待设备完成 | 额外buffer/synchronization；单tile不能证明跨tile重叠 | round3 K2048 DMA2仅约1.39%差；K4096仅一tile，归因未确认 |
| hist-decode-prepare-padding / layout correctness | 先整批pad再选择batch行，避免非concat维offset mismatch | JAX/Mosaic版本相关；cache和数值语义不能改 | 11-04明确原失败→修复通过对照；当前源码不同需重验 |
| hist-profile-accounting / evidence interpretation | full-call与nested事件分离，按时钟域和区间去重，保留原样本 | union不等于critical path；静态LLO/VMEM不等于实测利用率 | Decode r3与11-11XProf原始证据；通用于后续Analyzer事实降级 |

每个导入方法应关联原报告片段和原始manifest的哈希摘要，写明“历史shape/软件环境限定”。Plan需先比较当前源码是否已经实现该机制，再引用当前Profile提出新假说。没有当前证据时不能把上述清单自动变成优化队列。

Knowledge 来源笔记已保存至 `RSI/History/phase3-project2-2026-11-12.md`，10 项方法使用现有确定性 `RSI/Methods/method-<SHA256(method_id)>.md`。原 KnowledgeClient/MCP 已完成保存与回读，并通过 revision CAS 修复来源笔记的相对链接、同步全部 MethodRecord 的来源版本；原摘录及块外说明不变，全部 source_status 为 available。首次导入与维护回执见 [history-knowledge-import.json](../../knowledge-site/manifests/phase3-2026-11-12/history-knowledge-import.json)。这些知识记录没有伪造正式实验引用，也不代表当前 Task 性能已经验证。
