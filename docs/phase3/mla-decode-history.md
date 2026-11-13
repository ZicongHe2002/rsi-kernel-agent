# MLA Decode 历史知识

2026-11-12。来源类别：Source audit / 历史 Real TPU artifacts 离线核验；本轮没有新运行 Decode。原始来源均在 `C:\Users\fengluo\Desktop\project2`；SHA、配方和样本见 [实验清单](../../knowledge-site/manifests/phase3-2026-11-12/history-experiments.json)。

## 接口和比较边界

研究中的 dense 路径从连续 BF16 q/kv 到 latent O，包含 packing/layout；native 路径从常驻分页 cache与新KV开始，包含append、attention及更新cache，但不计初始历史cache打包。两者都不是 Forward的七输入投影pipeline。历史 latent512+RoPE64、H16、scale1/sqrt192只是这些实验的配置，不是当前系统唯一合法shape。

## 第二轮，2026-09-18

`result/decode/result-decode-2` 保存68次候选：36 MEASURED、24 COMPILE_FAIL、8 CORRECTNESS_FAIL。4组source_snapshot各23个文件的SHA全部匹配；每组results.json保存源码、参数、环境、正确性和device样本。单TPU v6 lite，Python3.13.15、JAX/JAXLIB0.8.1、libtpu0.0.30；N4096、BF16；3 warmup、7 device samples。

| 边界/shape | 历史候选 | 中位数 | 限制 |
|---|---|---:|---|
| dense B1 | D04_k1024 | 15.733 μs | 该轮dense最快，不与native算加速 |
| native B1 | G_T1_X0_F32S | 11.943 μs | 与T0差仅0.024μs，不足证明调度开关优越 |
| native B3 | G_T0_X0_F32S | 28.554 μs | shape依赖 |
| native B8 | G_T1_X0_F32S | 43.610 μs | 与自身边界的SGLang对照比较 |

R2_01–08切出576列触发DMA128对齐错误；转置cache的16次失败是动态slice tiling证明失败。Google非转置BF16-score的8项在边界gate失败，F32S对照通过。不能给编译失败候选填0ms，也不能把只通过满长度计时输入当作边界正确性通过。

## 第三轮，2026-09-18

`result/decode/result-decode-3`保存39次候选，均MEASURED；5组source_snapshot哈希全部匹配。每项5 warmup、11个whole-call设备样本；本轮提取并复算所有保留的429个样本。该轮的既有`review_metrics.json`记载独立XPlane复核；本次保存并哈希核验原始XPlane，没有再次声称运行TPU或重新解码全部trace。

| 边界/shape | 候选 | whole-call中位数 | 判断 |
|---|---|---:|---|
| dense B1/N4096 | D04_k1024 | 15.621328 μs | 同轮对照 |
| dense B1/N4096 | A_K4096_DMA2 | 12.746 μs | 约18.40%下降，含完整padding/准备；不全归因DMA2 |
| native B1/N4096 | G_T1_X0_F32S | 11.925 μs | 原基线仍最快 |
| native B3/N4096 | M_T1_K2048_B1 | 28.178 μs | 仅1.39%小收益，需独立复测 |
| native B8/N4096 | G_T1_X0_F32S | 43.983 μs | 原基线仍最快 |
| native B8/N16384 | G_T1_X0_F32S | 123.804 μs | 逻辑有效带宽1221.88GB/s，不是实测HBM利用率 |

A系列将576列pad到640后DMA；pad在计时JIT内。D04与A的中间精度不同，只能称相同最终容差下的实现比较。K4096/N4096只有一个tile，仅凭DMA2名称无法证明跨tile重叠。主要收益是准备路径减少，而不是attention custom-call本体大幅减少。

M动态all-active路径虽去掉部分gather/sort，却引入cond、copy、布局/metadata物化。B3/B8同配置的whole-call常退化约4–5μs，custom-call union基本不改善。嵌套cond包含其内部操作；不能重复相加。union不等于纯attention，outside不等于全是DMA或CPU时间。

## 默认 contiguous 编译修复与当前差异

2026-11-04 `result/mla_main_check_20261104/STATUS.md`及原始JUnit记录：main `d336a54…` 默认B8/H16/L512/R64/N16384、block1024失败，错误为 `result/input offset mismatch on non-concat dimension`；小B1和paged通过。原因是先切batch行再补零，非concat维offset不一致。

`result/mla_decode_fix_20261104`保存补丁和提交元数据：base `d336a54…` → fix `0f552ae…`。补丁先将完整newKV `[8,576]` pad至`[8,640]`，再取行；新增B3回归、原版失败对照、修复版默认/小配置/paged/边界通过。`commit.json`明确区分CRLF工作树摘要与LF commit blob。本次未把声明的Git parent升级为亲自遍历DAG的结论。

2026-11-11 rebase head `1912b994…`的默认Decode另有完整运行证据：O `[8,16,512]`通过，maxabs0.001953125；完整cache83,886,080元素按位相同，输入cache不变；20个Profile样本中位数0.555598789ms。该latency是全contiguous registry调用/长N16384/含cache返回，与9月resident分页native的123.804μs不构成可比回归。

**当前权威仓库Decode摘要 `e53b0e5c…` 与上述实际验证的 `2fc23aa9…` 不同，归一化换行后仍不同。** 必须重新审计差异和建立当前baseline；旧修复结果不能替代当前正确性。最新XProf16.55078125MiB是compiler-memory汇总，不是实测物理高水位；配置115.2MiB预留也不是实际使用量。

下一轮优先保留负例：不等长/0/负长度、page与tile边界、NaN未使用cache、完整cache/输入不变、batch非零行prepare。对齐修复是正确性/可编译性工作，不自动声称性能优化。
