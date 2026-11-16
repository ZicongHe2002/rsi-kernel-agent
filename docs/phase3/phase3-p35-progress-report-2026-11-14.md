# Phase 3 / P3.5 Runtime 7 第二轮 B2 与独立诊断进展报告

<!-- p35-final-acceptance-20261116 -->
> **2026-11-16 最终验收更新**：第二轮原四候选与独立诊断的原始证据已复核，修复后的根级及辅助完整回归均通过。声明范围内的 **P3.5 系统闭环 PASS_WITH_LIMITATIONS，稳定端到端性能 NOT_CONFIRMED**，详见[最终验收报告](phase3-final-acceptance-report.md)。本文保留 11 月 14 日各检查点；以下旧段落中的未完成表述仅适用于其原记录时间。
<!-- /p35-final-acceptance-20261116 -->

<!-- p35-runtime7-b2-complete-20261114T121949 -->
> **2026-11-14 最新进展｜Runtime 7 B2 两项剩余候选已完成真实执行**：`query-in-attention` 与 `b1-fused-prepare` 均完成 generate、compile、correctness、benchmark、profile、evaluate 五阶段，三组正确性通过，原始 NPZ/cache 核验 **2 verified / 0 pending / 0 failed**。`query-in-attention` 的 host median 为 **0.236630 ms**，同 Context 父版本 **0.223225 ms**，分类 `correct_slower`；`b1-fused-prepare` 为 **0.213700 ms**，分类 `correct_faster`。每个候选只有一个 Run，不能据此宣称稳定端到端收益或完成独立复测。
>
> B2 已形成完成闭包并写回原 Store/Memory：完成 **Snapshot version 26 / 1505 records / high-watermark 1530**，Snapshot ID 为 `snapshot:4aecf683cc9e49089b539dabe10fedd8`；Final Memory 报告 `status=verified`，`original_second_round_scope_complete=true`。Controller 当前累计预算为 **17/18 executions、30600/32400 声明秒**，剩 **1 execution / 1800 秒**。这表示原四候选第二轮范围已完成；P3.5 仍需把有限单 Run 结果与既有 ABBA 边界一起解释，稳定收益和最终验收结论尚未确认。
>
> 证据：[B2 完成快照](../../.artifacts/phase3-2026-11-12/continuation-e4b6a0fcf34a6c37d083/completed.json)、[B2 原始核验](../../.artifacts/phase3-2026-11-12/continuation-e4b6a0fcf34a6c37d083/local-verifications/20261114T121032140454Z.json)、[B2 Final Memory](../../.artifacts/phase3-2026-11-12/p35-b2-final-memory-20261114T121949131352Z.json)、[B2 规范](../../.artifacts/phase3-2026-11-12/p35-runtime7-b2-spec.json)。以下 A2、诊断及更早段落保留为历史检查点。
<!-- /p35-runtime7-b2-complete-20261114T121949 -->

**历史检查点｜2026-11-14 A2 与诊断完成时**：Runtime 7 两项 A 恢复候选与两个独立 single-tile 诊断 Run 均已完成真实五阶段执行及原始 NPZ 核验。诊断补齐了固定 small/boundary 矩阵中的单 tile 零长度覆盖；当时完成 Snapshot 为 version 23 / 1396 records / high-watermark 1421，A2＋诊断子里程碑完成。B2 最新结论见上方时间块。

该历史检查点采用 **02:59（Asia/Shanghai）** 保存的正式核验报告与 **Controller revision 209** 完成状态。A2 结论采用 **01:42 / revision 180**；准入于 **00:46:27** 完成，**01:08:50** 尚无候选派发文件的观察在第三节明确保留为历史。诊断核验开始于 **02:59:24**，文件名记录保存时点 **02:59:32**；未据此推定精确硬件结束时间。最终 Memory 报告于 **03:00:03** 开始核查，保存文件时点为 **03:03:39**，结论 verified。B2 已在上方单独记录；以下正文继续保留该历史检查点，不把旧时点状态覆盖为当前事实。

较早事实保留在 [11 月 13 日进展报告](phase3-p35-progress-report-2026-11-13.md)和[实现总报告](../../knowledge-site/manifests/phase3-2026-11-12/implementation-report.md)。本报告新增日期入口，不改写此前各检查点。

## 一、已确认结论与范围

| 项目 | 本次可以确认 | 仍不能确认 |
| --- | --- | --- |
| Runtime 7 四项准入 | **4 verified / 0 pending / 0 failed**；两个错误负例被拒绝，A/B 父版本通过 | 准入与候选结果分别留存，不构成新 ABBA |
| 原始证据复核 | 131 个去重工件，515,869,939 bytes；四项原始 NPZ 重算 | 未重新解码全部 XPlane protobuf，也未新增设备侧 input-cache 独立复证 |
| 第二轮输入 | **Snapshot version 19 / 1199 records / high-watermark 1224**，含新准入和历史归档 | Snapshot 不是驱动启动后的当前 Store 实时计数 |
| Analyzer | 两个 A Plan，`analysis.review=[]`、`winner_selected=false`，独立源码已封装并执行 | 没有在测量前强选赢家 |
| 第二轮 A2 执行 | 两项完整五阶段、三组正确性通过；**2 verified / 0 pending / 0 failed**，默认重算原始 NPZ | 每个候选只有单独一个 Run，不宣称稳定收益；原两项 B 目标仍未执行 |
| 原 Memory 与预算 | **final Memory verified**，绑定 Snapshot 23 / 1396 / 1421 与四个已 NPZ 核验 Run；revision 209，已用 **15/16、27000/28800** | 当前检查点投影一致；历史执行证据未全部重验，不等于 P3.5 全部 Gate 通过 |
| 独立 single-tile 诊断 | 两角色真实五阶段通过，**2 verified / 0 pending / 0 failed_verification**；76 工件、5,828,013 bytes，原始 NPZ 重算 | 仅补齐固定两 case 的单 tile 覆盖；不外推 LSE、更广矩阵或稳定性能收益 |
| 两项剩余 B 路线 | 有限 B2 辅助与独立审查已交付 | 没有实际 B2 grant 或 B2 执行成功结论 |
| P3.5 | **A2＋诊断子里程碑完成，整体进行中** | `original_second_round_scope_complete=false`；两项 B 未完成，不能把子里程碑等同于原四候选目标或稳定收益 |

正式范围仍为 **MLA Decode contiguous BF16**；原 small/boundary/default 三组正确性契约保持不变，正式 Benchmark/Profile 仅为 `decode-small`。Forward、paged、其他 dtype 和更广 mask/window/cap 不在本轮已验收范围。

## 二、Runtime 7 四项真实准入

Runtime 为 `tpu-v6e1-s-euw4a0-3qldb90w629mv`。固定隔离环境为 Python 3.11.17、JAX/jaxlib 0.8.1、libtpu 0.0.30、TensorFlow 2.20.0、xprof 2.22.1，单 TPU v6 lite、单进程。同步 JAX 探针返回 **140.0**，原 parser 可导入；探针与准入结果分别留存。

| 准入项 | 本次核验结论 | 原始数组核验 |
| --- | --- | --- |
| attention 错误负例 | 固定 Oracle 拒绝，保留真实 incorrect 结果 | `recomputed_from_raw_npz` |
| cache 错误负例 | 固定 Oracle 拒绝，保留真实 incorrect 结果 | `recomputed_from_raw_npz` |
| A 修复基线 | 原三组正确性及完整五阶段通过 | `recomputed_from_raw_npz` |
| B delayed-store-wait | 原三组正确性及完整五阶段通过 | `recomputed_from_raw_npz` |

只读核验于 **00:45:40 开始、00:46:27 完成**，报告 `runtime7_prerequisites_verified`，没有桥接调用或 Store 写入。131 个工件共 **515,869,939 bytes**；核验包括保存的原始 correctness NPZ 和 Profile 统计。四项完整准入不能替代对新候选的真实验证。

| 当前环境父版本 | small host median（ms） | program median（μs） | prepare median（μs） | attention median（μs） |
| --- | ---: | ---: | ---: | ---: |
| A 修复基线 | 0.211950 | 6.638125 | 0.694375 | 5.074375 |
| B delayed-store-wait | 0.223225 | 6.196250 | 0.696250 | 4.615000 |

host 是完整 public-call 同步延迟，program/prepare/attention 来自真实 trace 与原 parser。以上是本 Runtime 的单次准入 Run，**不把 A/B 数值差异当作稳定性能收益**；新候选必须使用当前 Context 的相应父结果比较。

证据：[实测环境](../../knowledge-site/manifests/phase3-2026-11-12/environment-isolated-runtime7.json)、[同步探针](../../knowledge-site/manifests/phase3-2026-11-12/isolated-probe-runtime7.json)、[四项完整核验](../../knowledge-site/manifests/phase3-2026-11-12/runtime7-prerequisite-verification.json)、[原带时间核验](../../.artifacts/phase3-2026-11-12/runtime7-local-verifications/20261113T164628095048Z-e1d3f9e959a34000baaa19a03507bef8.json)。

## 三、新历史、Analyzer 与两项 A 源码

恢复轮为 `real-runtime7-recovery-round:2`，继续原 Task `mla-decode-v1`，正式 Context 为 `runtime-context:b60862eb713e457a38d5a704bb3d7f1ceae68a118e919a326b0da13f50689b62`。

| 输入或会话事实 | 已保存值 |
| --- | --- |
| Snapshot | version **19**，1199 records，high-watermark **1224** |
| Snapshot ID | `snapshot:dbaf050176f740d0af8bfb4dad880814` |
| Snapshot SHA256 | `67658bd13462e8b12df8b2f2a73b609f5584fc2175ad3b8bb830a180ad259b77` |
| 状态卡 | 3 条 |
| A 路线完整历史 | 414 条，其中 owned 394、依赖 20，complete=true |
| 原会话响应 | 3 次 response |
| 工具调用 | 11 次：source/history/profile 各 1，Knowledge 5，计算 3 |
| 审核 | `review_enabled=true`、`analysis.review=[]`、`winner_selected=false` |
| 模型运输 | `conversation_session`，没有新增独立模型 API |
| manifest digest | `94395c3dad66d92daca85333e0ba035f6775f9bf7dcec2e56a0aade793293eef` |

两项均从当前 Runtime 的 A 修复父版本独立出发。父源码 SHA 为 `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`。封装各保留 375 个 bundle 文件，改动路径仅 `pallas_kernels/kernels/mla_decode.py`，Oracle、配置、原输出和计时契约保持原绑定。

| Plan / variant | 根据已有反馈选择的变化 | 本检查点状态 |
| --- | --- | --- |
| R2-A-length-row / `length-row` | lengths 中间表示由 `[B,H,T]` 缩为 `[B,1,T]`，attention 内广播 | 已完成真实五阶段与原始核验，`correct_faster`；见第四节 |
| R2-A-length-smem / `length-smem` | 去掉 prepare 的 lengths 中间输出，attention 从原始 `[B]` SMEM 长度读取并广播 | 已完成真实五阶段与原始核验，`correct_slower`；见第四节 |

两项的依据是第一轮较小 tile 的 program 回退、compact-new-row 未显示明确整体收益，以及 ABBA 的 host 漂移。因此保留 token tile、必要 prepare/cache 输出和固定 Oracle，改为探索 length 表示。逻辑 payload 计算是设计依据，不当作实际 VMEM/DMA 测量或收益预测。这一分析时点尚缺原 single-tile 分支的独立零长度诊断；后续已取得第五节的固定矩阵硬件结果。

**历史检查点｜01:08:50**：当时本地目录尚无 `execution-input.json`、`intake.json`、`controller-status.json`、`completed.json`，该次文档观察没有查询远端 operation。这个旧时点只说明当时本地文件状态；现已由第四节实际五阶段回执、完成文件及正式核验更新。`packet.json` 的 complete 本身仍只表示 Analyzer 会话完成。

证据：[准备输入与完整历史](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/prepared.json)、[Analyzer 结果](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/result.json)、[会话记录](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/session.json)、[候选索引](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/candidate-artifacts.json)、[两项 variants](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/variants.json)。

## 四、两项 A 的真实结果、核验与 Memory 反馈

两项均完成 generate、compile、correctness、benchmark、profile，原 small/boundary/default 三组正确性通过，`timing_validity=valid`、`confirmed=true`。正式只读核验于 **01:42:03** 开始，保存为 `20261113T174212998080Z-r180.json`：**2 verified、0 pending、0 failed_verification**，核对 **126 个去重工件、512,419,990 bytes**，两项均为 `npz_comparison_status=recomputed_from_raw_npz`，状态文件在核验期间未变化。

| 同 Runtime / Context 结果 | 原 Evaluator 分类 | small host median（ms） | program median（μs） | prepare median（μs） | attention median（μs） |
| --- | --- | ---: | ---: | ---: | ---: |
| A 修复父版本 | 当前 Context 的父计时 | 0.211950 | 6.638125 | 0.694375 | 5.074375 |
| `length-row` | `correct_faster` | **0.204825** | **6.626250** | 0.689375 | 5.041250 |
| `length-smem` | `correct_slower` | **0.216060** | **6.504375** | 0.787500 | 4.931875 |

两项各保存 20 个原始 host 样本，沿用原 5 次 warmup、所有输出同步、不剔除异常值政策。`length-row` 的单次 host median 低于父版本，但 program 接近父版本；`length-smem` 的 program 较短而 host 更慢。这些来自不同计量范围，不能用 program 较短替代完整 public-call 延迟，也不能由单 Run 确认稳定收益或 length 表示变化的物理因果机制。

结果通过原 Evaluator、`Store.consider` 与 Memory 归档。`length-row` 完成时的原 promotion Decision 为当前 Context **branch_promoted=true、task_promoted=true**；`length-smem` 保留正确但更慢的结果，两个 promotion 标志均为 false。较慢结果与完整证据没有删除，旧 Runtime 的 Best 不改标签迁移。这里记录原完成时晋升，不把晋升等同于稳定加速；诊断后的最终 Memory 投影核验另见第六节。

完成状态为 **revision 180**，两项 jobs 均 completed/evaluate。完成 Snapshot 为 **version 20 / 1296 records / high-watermark 1321**，ID `snapshot:5639025ecd344f66833454a4a4b796c2`，digest `a4a83f2fc8d555f468a05d3bd33ba30ac4d7a8bbaa31bb7e2c3b0ad43acb6713`。Snapshot 19 是 Analyzer 输入，Snapshot 20 是本次真实反馈归档，两者不混作同一时点。

这批反馈已形成“第一轮/ABBA 依据 → 新历史下的两项 length 改动 → 真实 TPU 结果 → 原分类与 Memory 晋升/非晋升记录”。后续应读取这两项新结果再评估两条 B 路线；原四候选总体目标仍只完成本次 A2 子批次。01:42 的 A2 完成时点尚无诊断成功 Run；随后第五节已取得两项真实诊断结果。本报告检查点仍无 B2 grant 成功结论。

证据：[正式两项原始核验](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/local-verifications/20261113T174212998080Z-r180.json)、[原 Controller 完成状态](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/controller-status.json)、[原 Memory 完成 Snapshot 与结果](../../.artifacts/phase3-2026-11-12/real-round-2-runtime7/completed.json)。本次仍未独立重解码 XPlane protobuf，设备执行时输入 cache 不变沿用原 runner 检查。

## 五、独立 single-tile 零长度诊断已完成

诊断轮为 `p35-runtime7-single-tile-diagnostic:1`，在同一 Runtime 7 使用独立 Context `p35-zero-context:67d3cd86dd782a7a6b3e0db6c84036a6d66522ee1d6e5c11006ac2cbe95c0bde`。baseline 与原第一轮 single-tile 源码各一个独立 Run，均完成 generate、compile、correctness、benchmark、profile；原 Evaluator 均为 **correct_inconclusive / passed / valid / confirmed=true**。

| 固定诊断 case | Batch / Heads / capacity | latent / RoPE / block | 实际覆盖 |
| --- | --- | --- | --- |
| `decode-small` | 1 / 16 / 1024 | 512 / 64 / 1024 | lengths `[0]`，全无效 attention 行，opaque NaN tail/padding 与完整 cache |
| `decode-boundary` | 3 / 16 / 256 | 128 / 64 / 256 | lengths `[0,1,129]`，包含零长度与合法长度；capacity 等于 block，进入单 tile 专用分支 |

两种源码使用同一诊断配置。attention 仍按原 `atol=rtol=0.01` 检查；cache 按完整 BF16 原始位比较，保留约定 opaque NaN 区域。正式核验 **2 verified、0 pending、0 failed_verification**，核对 **76 个去重工件、5,828,013 bytes**；两项均为 `recomputed_from_raw_npz`，所有五阶段回执 succeeded，状态文件在核验期间未变化。

| 独立诊断角色 | host median（ms） | program median（μs） | 原分类 |
| --- | ---: | ---: | --- |
| 修复 baseline | 0.219115 | 6.604375 | `correct_inconclusive` |
| 原 single-tile | 0.256860 | 6.620000 | `correct_inconclusive` |

每项保存 20 个 host 原始样本，但这仍是每角色一个 Run，且 workload/context 与原正式三 case 不同。这里记录诊断计时，不用它替代原优化性能分母或 ABBA，也不把 confirmed 解释为稳定收益。已补齐的是这两个固定 case 对原 single-tile 分支的直接硬件覆盖，不能外推所有长度、形状或配置；LSE 未由接口暴露，未测试。

原始 NPZ 核验重算 attention 与完整 cache 输出，包括 opaque cache 字节。设备执行时输入 cache 不变仍沿用原 runner 检查，因为没有保存运行后的输入 cache；cache finite metadata 未额外重算，原 XPlane protobuf 未重新解码。诊断的完成结果已通过原 Evaluator/Memory 写回，并形成 **Snapshot version 23 / 1396 records / high-watermark 1421**，ID `snapshot:22492511ed554ee6ace4e2f093a993ae`，SHA256 `a17b228ea2c89250d2758f037042789df7c996efe83d8faedd6faf7495346532`。

完成状态 **revision 209**，两项合计成本 **2 executions / 3600 声明秒**；累计 **15/16、27000/28800**，剩 **1 次 / 1800 秒**。随后最终 Memory、各 Context 的 Best/Frontier/Current Parent 与预算核验取得 verified，详见第六节；B2 grant 尚无实际 apply 成功结论。

证据：[正式诊断原始核验](../../.artifacts/phase3-2026-11-12/single-tile-diagnostic-e18e6e9bf4a98216/local-verifications/20261113T185932641868Z-r209.json)、[完成 Snapshot 与结果](../../.artifacts/phase3-2026-11-12/single-tile-diagnostic-e18e6e9bf4a98216/completed.json)、[原 Controller 状态](../../.artifacts/phase3-2026-11-12/single-tile-diagnostic-e18e6e9bf4a98216/controller-status.json)、[冻结诊断契约](../../.artifacts/phase3-2026-11-12/single-tile-diagnostic-e18e6e9bf4a98216/diagnostic-packet.json)。

## 六、A2＋诊断子里程碑的最终 Memory 核验

[最终 Memory 报告](../../.artifacts/phase3-2026-11-12/p35-final-memory-20261113T190339063283Z.json) 为 **status=verified**，`missing_verification_reports=[]`、`missing_verified_runs=[]`。它绑定 A2 完成 Snapshot 20 与最终 Snapshot **23 / 1396 records / high-watermark 1421**，重新关联四个新 Run 的原结果及已保存原始核验报告；四项均有 `npz_comparison_status=recomputed_from_raw_npz`，这次 Memory 核验复用这些已完成的核验结论，没有再读 NPZ 数组或重新运行 TPU。

| 投影范围 | 已核验结果 |
| --- | --- |
| Runtime 7 正式 Context | A Branch Best / Task Best 为 `length-row`；A Latest Attempt / Last Correct 为 `length-smem`，两个候选均在 Frontier；Current Parent 保留本轮显式 A 修复父对象 |
| 同正式 Context 的 B 路线 | Branch Best 仍为本环境重新测量的 `delayed-store-wait`；尚无本轮 B2 新候选结果 |
| 独立诊断 Context | Branch/Task Best 为诊断 baseline，两角色均在 Frontier；显式 Current Parent 仍是原 single-tile 源 Candidate，不改写正式优化 Context |
| A 完整参与历史 | **599 条：owned 576、依赖 23，complete=true** |
| 原 Journal 与快照 | revision 209；累计 15/16 executions、27000/28800 声明秒；四个新 Run 共预留 4/7200，早前 11/19800 保留 |
| 检查时实时投影 | 当前计数 1396 / high-watermark 1421，与最终 Snapshot 同水位，`projection_comparison=equal_at_same_watermark` |

该报告是只读 Memory/身份/投影对账，`store_writes=0`、`network_calls=0`。历史 Best/Frontier/Parent 按原 Context 和 Decision 做结构核对，**没有重新验证全部历史执行证据**；大 Analyzer manifest 只匹配原 immutable publication 引用，没有重新散列正文。本次也没有重算 NPZ、重新解码 XPlane 或新增设备执行。

因此可以确认 **A2＋两个诊断的子里程碑完成**。报告同时明确 **`original_second_round_scope_complete=false`**，待执行候选仍为 `query-in-attention` 与 `b1-fused-prepare`；不能把 verified 解释为原四候选目标、所有 P3.5 Gate 或稳定性能收益已经完成。

## 七、既有结果与 Runtime 失联边界

| 已发生事件 | 保留的事实与限制 |
| --- | --- |
| Runtime 2 第一轮 | 4 Plan / 5 Candidate 完成真实五阶段与三组正确性；单次 Best 晋升不等于稳定加速 |
| Runtime 3 ABBA | C–T–T–C 四个 Run 完成；program 两对 median 降低约 **6.50% / 5.92%**；host 为 `inconclusive_drift`，联合 `inconclusive` |
| Runtime 3 原第二轮中断 | 四候选零 durable 远端请求/回执；首项 cancelled/incomplete、另三项 not_run；原身份和已扣预算保留 |
| Runtime 4 correctness 未知中断 | 原 attention 准入按 unknown/incomplete 归档，已有请求/回执保留；没有伪造正确性结论 |
| Runtime 5 恢复轮失联 | 四项零 durable 远端请求/回执经独立核验；首项 cancelled/incomplete、另三项 not_run；Snapshot 14 / 986 / 1011，revision 151，已扣预算不退 |
| Runtime 6 完整准入后失联 | 四项准入结果保留；探索 Job、Run、durable request/receipt 均为零。归档只加两条 Decision 与 Snapshot，**没有创建或取消不存在的候选 Run** |

Runtime 6 归档 Snapshot 为 **version 17 / 1094 records / high-watermark 1119**，原 Controller 仍 revision 151、已用 11 executions / 19800 秒。Runtime 6 已准备的 Analyzer/源码不计作第二轮执行，也不重贴标签为 Runtime 7 的硬件结果。历次失联原因不在现有证据中确定，不扩称设备故障或 Kernel 失败。

证据：[第一轮报告](phase3-optimization-report.md)、[ABBA 报告](phase3-p35-abba-results.md)、[Runtime 5 原归档](../../.artifacts/phase3-2026-11-12/real-round-2-runtime5/runtime-loss-archive.json)、[Runtime 5 独立核验](../../.artifacts/phase3-2026-11-12/runtime5-loss-independent-verification-20261113.json)、[Runtime 6 完整准入失联归档](../../.artifacts/phase3-2026-11-12/runtime6-completed-prerequisite-loss-archive.json)、[Runtime 6 派发前只读清单](../../.artifacts/phase3-2026-11-12/runtime6-pre-dispatch-loss-readonly-inventory-20261113.json)。

## 八、B2 完成闭环、预算与恢复软件验证

诊断完成后的有限 B2 批次已按同一 Runtime 7 / Context 完成。最终 Controller/Memory 对账为 **17/18 executions、30600/32400 声明秒**，余 **1 次 / 1800 秒**；旧失联预留不退款，准入成本单独记账。B2 结果写回完成 **Snapshot version 26 / 1505 records / high-watermark 1530**，Final Memory 为 `status=verified`，`original_second_round_scope_complete=true`。

| 批次 | 已用或计划成本 | 状态与继续条件 |
| --- | ---: | --- |
| 本轮两个 A 恢复候选 | **已用 2 executions / 3600 秒** | 两项真实执行与原始 NPZ 核验已完成，结果写回 Memory |
| 两个独立 single-tile 诊断 | **已用 2 / 3600** | 两项固定矩阵硬件执行与原始 NPZ 核验已完成；独立 Context 不替代原优化结果 |
| 两项 B2 候选 | **已用 2 / 3600** | 两项真实五阶段执行与原始 NPZ 核验已完成；每项单 Run，不能外推稳定收益 |

本批次实际将探索限额扩展至 **18 executions / 32400 声明秒**，spent 为 17/30600；声明秒不是实际 TPU 账单或 wall-clock。剩余 1 次执行不自动等同于独立复测预算，继续派发前仍须先核对 operation、Context、父版本和剩余契约。

新增 [诊断窄恢复入口](../../.artifacts/phase3-2026-11-12/resume_p35_diagnostic.py) 只接原已发布、已 intake、已被 Controller 接受的两项诊断作业，复用原 operation、driver lock、check_scope 与 Controller。它流式核对大 manifest/result 字节，避免再次展开历史和 publication/intake；保留原 13→15、总限额 16 的预算约束，不新增 Run、retry、repair 或 grant。独审发现的终态 status 文件滞后、验证期间 Runtime 配置替换两个 Important 已通过红→绿测试修复。

该新入口最终版本 **9/9 本地 fixture 通过，158.843 秒，exit 0**；原 `check_scope` 兼容专项 **2/2 通过，16.993 秒，exit 0**。它们使用临时 Store 和替代外部 backend，验证原 session/intake/Controller/回执软件行为，不计作新的 TPU Run。最终独审无未关闭 Critical/Important；独立 9 项保存结果摘要，未保存完整 raw log，不能把旧版本 11 项中曾失败的混合日志称为最终全绿。

有限 B2 辅助已完成本地 21 个不同测试及独立审查，并已在满足前提的同一 Runtime/Context 上完成两项真实候选。软件准备不能替代候选的真实证据；本报告也不据此宣布无限自动循环或任意跨 Runtime 恢复完成。

证据：[诊断恢复交接](../../.artifacts/phase3-2026-11-12/p35-diagnostic-resume-handoff-20261114.md)、[恢复独审](../../.artifacts/phase3-2026-11-12/p35-diagnostic-resume-independent-review-20261114.md)、[最终九项结果摘要](../../.artifacts/phase3-2026-11-12/p35-diagnostic-resume-independent-result-20261114.json)、[scope 兼容日志](../../.artifacts/phase3-2026-11-12/p35-diagnostic-resume-scope-compatibility-20261114.txt)、[B2 有限批次交接](../../.artifacts/phase3-2026-11-12/p35-b2-continuation-handoff-20261114.md)、[B2 整体独审](../../.artifacts/phase3-2026-11-12/p35-b2-continuation-independent-review-20261114.md)、[B2 核验独审](../../.artifacts/phase3-2026-11-12/p35-b2-verifier-independent-review-20261114.md)。本次文档更新未重新运行根套件，不将历史根测试称作本日完整回归。

## 九、下一项可交付里程碑

两个 A、两个独立诊断、两项 B2 真实反馈及最终 Memory 对账已完成。下一项应在不重复旧 operation 的前提下，决定剩余 1 次执行是否值得用于受控独立复测；若继续申请 Runtime，先核对当前设备、原 operation 和剩余预算，再按原协议推进。

继续按原授权与有限 grant 契约推进剩余两个 B 候选；新 Analyzer 必须读取本次 A2、诊断与 Memory 反馈，不直接复制旧结论。没有可用 v6e 时，继续沿用户已授权的原唯一申请流程申请；取得设备后完成新环境与重新准入。恢复任何 unknown operation 都先查原身份，completed 只回收，running 继续查询，只有 definitely_not_started 才提交原请求。

**当前结论：Runtime 7 四项准入、第二轮四项候选、独立两项 single-tile 诊断均已取得真实执行和原始 NPZ 核验；完成 Snapshot 26 与 Final Memory 对账均为 verified，原四候选第二轮范围已完成。B2 每个候选仅有一个 Run，既有 ABBA 的 host 漂移仍限制结论；P3.5 最终验收与稳定端到端加速尚未确认。**
