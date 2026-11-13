# MLA Forward 历史知识

2026-11-12。本页来自 project2 现存报告、配方、源码和原始结果的离线审计；所有数值均为**历史**，不是本轮 Phase 3 TPU 测量。完整来源和 SHA 见 [实验清单](../../knowledge-site/manifests/phase3-2026-11-12/history-experiments.json)。

## 数学和计时契约

历史主线为七输入 expanded causal MLA：Q=CqWq、K=CkvWk、V=CkvWv，score=scale×(QKᵀ+QrKrᵀ)+causal mask，O=softmax(score)V，可选自然对数 LSE。完整 pipeline 包含投影与必要布局；不含下投影、RoPE 旋转、Wo 或 Decode cache append。对照必须同 shape、dtype、return_lse、容差、计时边界和软件环境。

absorbed 分支把 Qa=(CqWq)Wkᵀ、Z=P Ckv、O=ZWv。实数结合律成立，但 BF16 舍入不同；prefill 主 shape 的 pair 宽度从 320 增至 1088，不能从 Decode 缓存节省推断 prefill 收益。早期部分 raw ZIP 已丢失，trajectory 中 0.4163/0.5682 ms 等表只能作为转录历史，不能重建正式实测 Run。

## 研究演进

| 阶段 | 修改与实际观察 | 下一轮应保留的约束 |
|---|---|---|
| expanded tile / QK256 | 大 tile 改善 S1024；将 NoPE128+RoPE64 补齐至 reduction256 合并 dot | QK reduction256、KV score-output256、value dimension256 是三个不同维度 |
| current QK256 + visible mask bypass | 历史主对照；qsub512 降静态 spill references，但更慢 | 静态 spill 下降不等于 latency 下降；pad 后立即 slice 可能被 DCE |
| Result5 diagonal512 | 1024 对角 tile 仅执行合法的三个 512 panel；N6 0.504748 ms，current0.569008 ms | S4096 correctness 是对 anchor 的 differential，须结合小规模独立 Dense JAX；旧数据未充当本轮 baseline |
| Result6 N14 / E2 | paired Q512+diagonal256 与 KV head-group2 分别改善 attention / projection；N14 0.503940、E2 0.503528 ms | E2 attention 不变；不能把 projection 收益称为 attention 优化。近似 BF16 prescale 候选单列 |
| Result10–12 BDU01 | 首块初始化、Q-RoPE 转置、未归一化累加组合 | Result12 H32/JAX0.8.1；N04 与 BDU01 仅差39 ns，分布重叠，未证明新收益 |
| Result14 R25 | score[K,Q]、沿 K 归约、PV accumulator[Dv,Q]，末端恢复输出 | JAX0.7.2/H16；同轮 R20→R25 才是布局比较，不能对 H32 数字算加速 |

早期阶段数字和归因来自 `result/mla_fwd_v6e_optimization_trajectory.md`、`mla_fwd_v6e_result_5_report.md`、`mla_fwd_v6e_result_6_report.md`、`result/reports/ALL_METHODS_RESULTS_AND_CONCLUSIONS.md`；均已记录 SHA。该阶段没有重新核验全部底层 trace，证据级别低于下面逐文件恢复的 result14。

## Result14：源码和原始样本已核验

来源目录 `project2/result/imported/result14`，manifest 创建时间 `2026-09-14T09:50:09.518768+00:00`。单 TPU v6 lite，JAX/JAXLIB0.7.2、libtpu元数据0.0.23；B1/H16/S4096、Rq1536/Rkv512/Dn128/Dr64/Dv128、BF16。`workload_config.yaml` 是旧 H32 文件，实际 shape 取 manifest/accuracy，不能只按 YAML 推断。

40 文件源码清单全部匹配。`mla_fwd_research_layout.py` 直接实现布局族，另外保留 state/persistent/paired/square 实现，不能把所有候选都归到一个 `mla_fwd.py`。每个 experiment 保存完整 recipe、backend、control 和 recipe_parent_label；源码直接父仍 unknown。

| 同轮对照 | 完整 pipeline 中位数 | 采样 | 结论 |
|---|---:|---:|---|
| R20 square layout control，screen | 0.406563750 ms | 32 | 布局对照 |
| R25 transposed QK/PV，screen | 0.373640000 ms | 32 | 相对 R20 下降8.10%；同轮、同阶段 |
| R25 final | 0.373545625 ms | 128 | 单候选加密采样；没有同轮 BDU01 final |

R25 final 同时有 output-only O、O/LSE 通过记录和原始 CSV、Perfetto trace、HLO/Mosaic。原始 `device_event_raw_samples.csv` 的 pipeline 样本已提取并重新计算中位数；没有把 host-wall CSV 当作设备时间。26 screen 候选 22 measured、4 compile rejected；R25 final 是额外阶段记录，故结构化清单为27条。

R03 lane128 denominator 0.805030 ms、R04 score lookahead0.442934375 ms、R41 persistent约1.00 ms，均未超过相应对照/主线。没有有效 LLO/spill 数据，因此只能提出状态存活和同步成本假说，不能声称“已测得更多 spill”。Q 投影外围 copy 的独立中位数相加也不等于关键路径可回收时间。

## 最近可追溯默认配置

`result/mla_vmem_rebase_20261111/payload.zip` 含提交 `1912b994…` 的688个已核验源码文件；`downloaded` 的1044文件全部长度/SHA匹配。Forward实际导入 SHA `f9b07f0c…` 与当前权威 `mla_fwd.py` 完全相同。实际工作量 B1/H16/S4096，Q/KV1024、rank256、assume_a09_safe=true；完整 registry 调用包括 Q/KV/attention。

保存结果报告 CPU reference 全输出 8,388,608 元素通过、max abs0.0001220703125、atol=rtol=0.01。JAX/JAXLIB0.8.1、libtpu0.0.30、Python3.11.17。5 warmup 后20次**Profile期间**设备样本中位数0.398320039 ms，原始XPlane存在。这只能作为历史执行/正确性/Profile证据，不能与 result14 或新 Phase3 unprofiled benchmark 直接排名。

XProf80.0546875MiB来自编译 memory viewer 汇总，含逻辑 heap和独立scoped峰值；重叠buffer与不同存活时刻导致它不等于同时物理高水位。不能把它变成实测VMEM利用率或“需要80MiB才能编译”的结论。

首个新 baseline 可优先 Forward，但仍需全依赖身份、真实环境、独立负例、原始样本及独立Profile Gate。历史提示应转为有来源的假说；当前实现已经包含部分旧优化，不能无差别再应用一次。
