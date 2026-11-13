# 必须保留的历史失败与反例

2026-11-12。Source audit / 历史TPU结果。失败不代表当前代码仍有同一问题；修复也不代表所有shape或版本已覆盖。原始信息见 [136条实验记录](../../knowledge-site/manifests/phase3-2026-11-12/history-experiments.json)。

| 历史实验 | 失败或退化 | 有证据的解释 | 下一次的约束 |
|---|---|---|---|
| absorbed prefill | 大shape无性能胜出，旧逐元素gate失败 | 数学重结合改变BF16舍入；prefill运算宽度增大；部分raw丢失 | 不靠放宽oracle宣布成功；不能将Decode优势直接套prefill |
| qsub/streaming/explicit scratch | 静态spill降低但延迟升高 | 更多循环/向量工作是解释，静态references不是DMA计数 | 同时看完整latency和资源，不按单一spill指标晋升 |
| Result5 N4/N5 | compact state shape cast编译失败 | 保存报告明确Mosaic lowering限制 | CPU/前端export通过不等于TPU编译通过 |
| Result14 BDU01/R05/R06/R07 | 4项REJECT_LSE_COMPILE | Lane broadcast，O/LSE gate阶段失败 | 没有有效latency；不能写成数值失败或“0ms” |
| Result14 R03/R31/R32 | lane128 denominator约0.805–0.896ms | 原始设备样本确认变慢；无有效LLO，spill原因未知 | 不把推测写成Profiler observation |
| Result14 R41 persistent | 约1ms，仅比匹配R40快0.33–0.69%，远慢R25 | 全Q状态驻留/KV fusion没有自动带来整体收益 | 复用收益和状态成本共同评估 |
| Decode r2 R2_01–08 | 8项COMPILE_FAIL | DMA feature576不满足128对齐 | 必须验证实际storage/DMA切片；准备/packing在原契约内计时 |
| Decode r2 Google X1 | 16项COMPILE_FAIL | 动态slice维度无法证明tiling128整除 | 两步开关/FP32没解除布局限制；无性能结论 |
| Decode r2 Google X0 BF16 score | 8项CORRECTNESS_FAIL | 满长度pass但page/tile边界fail；F32S通过 | 固定atol=rtol0.01；保留精度失败，不进入排名 |
| Decode r3 M dynamic fast path | B3/B8同配置whole-call退化约4–5μs | 真实trace表明外层cond/copy/layout成本，custom-call近似不变 | 不把减少gather自动当加速；保留完整调用边界 |
| Decode main 20261104 B8 | 默认contiguous prepare不能编译 | 行切片后concat offset不一致；原失败对照和补丁pass齐全 | B1测试不足；增加非零batch行的native负例 |
| Result12 N04小差异 | 39ns/0.00509%名义领先 | 分布重叠，未证明收益 | 不把统计噪声当verified improvement |

历史分析还揭示了三类证据反例：1）配置YAML与实际override不同，必须读取运行manifest；2）host-wall、device-event、Profile期间latency不可混算；3）逻辑有效带宽/静态MXU/编译VMEM字段都不能升级成运行时硬件计数。

这些条目应进入Knowledge的风险和known failures，保留原始错误、source revision和适用条件；没有正式RSI Run所需绑定时，不伪造Run、Repair或失败父Candidate。
