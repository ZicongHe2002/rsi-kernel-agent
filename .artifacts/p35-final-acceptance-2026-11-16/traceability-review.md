# P3.5 Analyzer / 多轮反馈独立审计

2026-11-16。Reviewer：独立子代理 `accept_traceability`。结论：在保留工件、可信生产者与固定 workload 范围内，第一轮真实反馈进入第二轮 Analyzer、多 Plan、独立候选、执行和原 Memory 的谱系可核验；没有发现本范围内未关闭的 Critical / Important。该结论不证明模型优于随机搜索、Knowledge 的因果收益或稳定性能改善。

本次没有远端调用、Store 写入、新 Run、TPU 申请或预算消耗。审计读取 SQLite `mode=ro`、`PRAGMA query_only=ON` 的同一事务，只将报告和辅助脚本写入本目录。机器结果见 [traceability-facts.json](traceability-facts.json)，复现入口见 [traceability-audit.py](traceability-audit.py) 与 [source supplement](traceability-source-supplement.py)。初始三个已完成会话核验退出 0，13.298 秒；随后补充原中断会话的分析谱系，独立作为 analysis-only 记录。日志见 [主要核验](traceability-audit.log)、[原分析](traceability-original-analysis.log)、[源码补充](traceability-source-supplement.log)。

## 审计方法与范围

阅读 Phase 2A、2B、2C、2D+ 原实现报告，核对当前 `profiler/inputs.py`、`tools.py`、`outputs.py`、`analyzer.py`、`session.py`、`memory.py`、`artifacts.py` 的相应接口。原架构为唯一 ExperimentStore / ArtifactStore、冻结 Memory、原会话 Analyzer、publication、Intake、Controller、Evaluator / consider；本次没有重建另一套运行系统。

本次实际执行的检查包括：

- 对四个 manifest 及对应完成 Analyzer result 读取完整原始字节，重算 SHA256 与长度；session 三个响应、result.exchanges、原 proposal、publication 的 result/manifest 引用逐项相等。
- 按冻结 Snapshot high-watermark 从当前只读 Store 重建所有输入 record 引用集合，逐条匹配 kind/digest；参与 history 的每份 payload 另重算摘要。依据当前原 Memory 的 ownership / dependency 规则，以数据库关系独立重建完整历史集合和顺序，核对 owned/dependency/total、offset=0、next_offset=null、complete=true，未将精简状态卡当完整历史。
- 重做所有保存的 source/history/profile/knowledge/calculate 工具调用并比较完整结果；Profile 工具此处重做的是保存 parser 的 normalization，独立 XPlane 解码由 `accept_raw_evidence` 另报。计算调用复算使用现有确定性数学工具，逻辑字节并非物理利用率测量。
- 对每个有 predicate 的 observation 逐级解析原 catalog/path，精确核对类型和值；原解释仍是 hypothesis。最终 analysis.review 均为 `[]`、review_enabled=true、winner_selected=false。
- 核对 publication、Intake、Plan、Candidate、Run、Evaluation、promotion Decision 及摘要绑定；Store 序号满足输入 Snapshot → publication → Intake → Candidate → Run。
- 重新比较九个已执行候选与精确父版本的 source bundle，均 375 文件；差异只涉及 `pallas_kernels/kernels/mla_decode.py`（两种 tile 变体 source 相同，变化在 config）。重新哈希相关文件；Oracle / runner / build 中其他固定字段保持父绑定。
- 原第二轮四个候选 source/build 摘要与最终 Runtime 7 实际候选逐项相同，运行身份、Context、父计时另有新绑定。没有把原 Runtime 3 未执行候选记作完成硬件 Run。

所有对应 Snapshot 的完整字节摘要及与原 Store 重建字节的一致性，由同日 [Memory 审计](memory-audit-result.json) 另行核验，包括 v4 / v10 / v19 / v25 / v26。

## 四份会话的准确区分

| 会话 | 固定输入 Snapshot / records / HW | 完整参与历史 | 响应 / 工具 / 数学 | 本会话实际 Plan / Candidate 范围 |
| --- | --- | --- | --- | --- |
| `real-runtime2-round:1` | v4 / 198 / 223 | A 97=87+10；B 130=34+96 | 3 / 20 / 8 | 4 Plan / 5 已执行候选 |
| `real-runtime2-round:2`，**Runtime 3 原中断分析** | v10 / 776 / 801 | A 268=256+12；B 501=371+130 | 3 / 18 / 7 | 4 Plan / 4 未远端执行候选；仅作设计来源 |
| `real-runtime7-recovery-round:2`，A2 | v19 / 1199 / 1224 | A 414=394+20 | 3 / 11 / 3 | 2 Plan / 2 已执行候选 |
| `real-runtime7-b2-continuation-round:2`，B2 | v25 / 1400 / 1425 | B 718=486+232 | 3 / 13 / 4 | 2 Plan / 2 已执行候选 |

每个会话的 3 次响应确实为读取工具请求、数学工具请求、最终 proposal。A2 当前 PlanSet 只有两项 A，B2 当前 PlanSet 只有两项 B；历史 history 内的旧 Plan 不计入这些数量。Runtime 7 是**原第二轮四候选按 A2 / B2 两批真实执行**，不以两个批次夸大为额外两轮新优化。

| 会话 | Manifest SHA256 | Result SHA256 |
| --- | --- | --- |
| R1 | `5aff21e4175670eae1546c56e4897f12dfb3c289d129ab8db0cd8381453e8bec` | `eb6bd22efc91995a2080fcd106079d92369041d04900b813c94e5716466b0fcb` |
| 原中断 R2 | `2b2d47f007957bac6f045bba6a0dac44f91b5c19e42f38ef36932bc6a7b60c44` | `35f21ddac31994ddbff987e06edb6f6bc50ec374f439ebda3648c93df3352118` |
| R7 A2 | `94395c3dad66d92daca85333e0ba035f6775f9bf7dcec2e56a0aade793293eef` | `e31ae88a8a102fae109e09f465fbe4fe44f85828168a304d786b83de7c8a08ea` |
| R7 B2 | `86dc88246fa5dd6cdc3187f5b07344029b447a80c8dfcf1b02fcfbba132e08ea` | `321109a99ed911b18527e6215768af34c48c521379329100541c4fa8bb5dcc55` |

R1 publication 为 `analysis:0b52562aaeff8d8712bc5800:complete`（seq236），Intake seq237。原中断 R2 publication 为 `analysis:a71c1bbe71f21e8e749b5d13:complete`（seq813），Intake seq814。A2 publication 为 `analysis:e6e69bd67e25af0fcb37431c:complete`（seq1231），Intake seq1232。B2 publication 为 `analysis:abeb92d20a458b717e0a386a:complete`（seq1432），Intake seq1433。

## Knowledge 的来源与可支持范围

四份 manifest 都冻结六个 Knowledge 方法，内容字节及来源版本可核验；R1/B2 实际分别读取全部六项，原 R2/A2 实际读取五项。不要把“已固定到 manifest”与“另调用 knowledge 工具读取”混称。

| 方法 | 实际方法 revision | 用途与边界 |
| --- | --- | --- |
| `hist-decode-dma2` | `f9753641322bbe0b0f060f5a91c74ecf9d601e8ec9e0b19d96e027ea9b7a4ef3` | 固定 tile/精度/布局比较，单 tile 不能证明跨 tile DMA overlap |
| `hist-decode-prepare-padding` | `8169a9a74469f23bf9d5d9e00449842e75130f6722191e0f50b4252b39df6c3c` | 先整批 pad 再抽 batch 行，避免 non-concat offset mismatch；当前版本须重验 |
| `hist-fwd-state-lifetime` | `f8eb9a93be5e03ac6018deb9158da52f8bfb1c1ea0d2b4975a3a90530b36a794` | 在线递推、首块状态及生命周期思路；历史 LSE 编译失败与组合不可加限制保留 |
| `hist-profile-accounting` | `c9511365e6c5ad3b2d94b4725df1e64094c546944613f0b87469b0222f5733bd` | full-call 与 nested event 分离；union 不等于 critical path；静态 VMEM 不等于利用率 |
| `official-jax-synchronized-benchmark` | `54dd5817591eda66b0e81fe7282da49ed857877fef29c84facc3b07e99600e6b` | JIT / warmup / async dispatch / block_until_ready / H2D 边界 |
| `official-tpu-profile-mode-version-gates` | `b7a9b8e0db33719d6884f2f0ec94f22128e6f190a96c5a13fc55f16f9bfaa553` | 版本和硬件限制；未据新文档升级冻结环境或宣称新 counter 可用 |

前四项引用 `RSI/History/phase3-project2-2026-11-12.md`，源 revision `691a21c5eac34ad28638ae7c24b487b3280dad71230e8a5362c970e74dd2e6f5`；后两项引用 `RSI/References/phase3-official-2026-11-12.md`，源 revision `5658e0180d4ab3ab739b46209a51611ef3593a665acec4ddb521bd00deaa6f77`。所有 source capture 的 available/current_revision/pinned revision 一致，原摘录完整保存于机器结果。本文只核对相关方法及其固定来源，未重新扫描 project2 全目录或独立重验全部 136 个历史实验。136 条恢复记录、12 项历史方法属于既有来源背景；本次实际使用的方法范围以上表为准。

Knowledge 分别固定来源版本，但 `knowledge_corpus_snapshot_consistent=false`、`combined_atomic_snapshot=false`，这是原 manifest 明示边界。方法提供思路和风险，不构成对本 Task 的硬件测量或效果因果证据。

## 四个 R2 候选的逐项链路

所有 R7 正式候选同 Context：`runtime-context:b60862eb713e457a38d5a704bb3d7f1ceae68a118e919a326b0da13f50689b62`。A 精确父为 `wf-parent:a37a1b7abd28b0681f9fd736f13eb97e37f15612`，对应修复 baseline；B 精确父为 `wf-parent:88fc2af19b8d773ad8ce15664ff31573f958e8a3`，对应 R1 delayed-store-wait 源 Candidate。A/B 分别使用 Runtime7 重新准入 Run 计时 0.211950 / 0.223225 ms，不把 Runtime2/3 历史计时当分母。

### 1. `length-row`

原观察是 R1 `tile512` Run `wf-r:ff302e1edbba1534f33b983fdecaebdd`、`tile256` Run `wf-r:5a817d8cbe14b528e5447301c17b537a` 均为 correct_slower（program 8.09 / 11.1275 μs）；`single-tile` Run `wf-r:44028b3ac9bd4026c7961396e3a67ab8` 也为 correct_slower（program6.580625 μs）。A2 原响应 `A-R1-tile512` / `A-R1-tile256` / `A-R1-single-tile` 的 predicate 对应原 workflow-result，不是本次审计后补理由。

原解释 `A-changed-direction`：“Keep original tiles and recurrence; isolate length representation as a new optimization axis.” 原 hypothesis `A-interface-cost` 同时列出编译器已折叠、layout/临时量回退、host 波动等替代解释。Plan `R2-A-length-row` → `analysis:e6e69bd67e25af0fcb37431c:plan:2395b694213876c2`，将 `[B,H,T]` 长度张量缩为 `[B,1,T]`，attention 中广播。模型字节 65536→4096，只是逻辑接口。

已重算源码差异位于 [候选源码](../phase3-2026-11-12/candidates/round2-length-row/mla_decode.py) 第2914、2924、2933–2934、3011行，source SHA `133f074cfb7a5a5a50babf31dc26d0da00c8b6c77d8b84137aa6ac3fb350cf0d`。真实 Run `wf-r:2a1f9801ce722ab6f2a0e491571b4f5c`，三 case passed，host0.204825 ms，program6.626250 μs，correct_faster。原 Decision `decision:2830347a4f4043368604cfd5364fc5e5` 为 branch/task promoted；反馈进入 Snapshot20，最终含于 Snapshot26。program 与父6.638125 μs 接近，单 Run 不足以确认假设的物理机制或稳定 host 收益。

### 2. `length-smem`

引用同一组 R1 Run 和原 `A-changed-direction` / `A-interface-cost`，但独立从同一个 A 修复父开始。Plan `R2-A-length-smem` → `analysis:e6e69bd67e25af0fcb37431c:plan:0b4e095507c4dc66`，删除 prepare 的 lengths 输入与中间 lp 输出，attention 直接读取原始 `[B]` SMEM 长度并广播。模型接口65536→4 bytes，不是实际 VMEM/DMA 节省。

已重算差异位于 [候选源码](../phase3-2026-11-12/candidates/round2-length-smem/mla_decode.py) 第2902、2915–2926、2930–2931、3008、3025行；source SHA `8ad552c3649fd8e70f0a8b3eee01565235854ab86df84de9efbaf0eb600c3722`。真实 Run `wf-r:fca16eb0837162a4df980f457c3f26df`，三 case passed，host0.216060 ms，program6.504375 μs，correct_slower。原 Decision `decision:f9bb3ac01e1644f1ac03d5ac86ec2479` 不晋升，完整反馈和 Frontier 保留。真实反馈形成“program 较短但 public-call 更慢”的反例，不能用前者替代后者，也不能断言慢因一定是 SMEM。

### 3. `query-in-attention`

其最早明确第一轮理由在**原中断 Runtime3 R2** 的 `B-R1-delayed-store-wait`、`B-R1-compact-new-row`、`B-changed-direction`。分别绑定 R1 Run `wf-r:9693f8c2c46382039b4386db7c6ddbe1`（correct_faster）和 `wf-r:fc2da20b2a1679868f21869279e90949`（correct_slower）。原文：“R1 compact-new-row reduced the logical prepared payload but did not improve program materially. ABBA supports the delayed schedule in program only and shows nearly unchanged prepare. Retain delayed wait while testing query-placement and static B1 fusion as distinct alternatives; do not repeat compact-new-row or sum hypothetical gains.”

本次验证这份原分析的冻结 manifest/result、完整 A/B 历史和 predicate，并确认源码/父身份与 R7 相同。R7 B2 新会话再次读取718条 B 完整历史，显式检查 ABBA host drift、A2 两结果、诊断两结果和 Final Memory/有限 grant，通过 `B2-interface-vs-schedule` 重述“移动/融合 prepare 可能减少接口工作，也可能增大 attention 布局和调度成本”。**B2 当前文本没有逐字重复最初 R1 数值；上述 R1 理由明确归属原分析，不伪称在 B2 又产生一个新理由。**

当前 Plan `R2-B-query` → `analysis:abeb92d20a458b717e0a386a:plan:c7c412b5be64be99`。保留 delayed wait 和其余 prepare，将 Q padding 移到 attention；逻辑 query接口20480→18432 bytes。[候选源码](../phase3-2026-11-12/candidates/round2-query-in-attention/mla_decode.py) 第2902、2914–2925、2929–2932、3007、3026行；source SHA `be122d552e40e9501055a9643ca349871cfae726f7740c27e1e03cc99e1c258f`。真实 Run `wf-r:bcde96344bd5eacd7e9b35468716156c` 三 case passed，host0.236630 ms，program6.831250 μs，prepare0.675625、attention5.333125 μs，correct_slower；原 promotion false/false。反馈写回 Snapshot26。该配置下完整 host 与 program 都回退，否定“缩小接口必然更快”，未识别唯一物理原因。

### 4. `b1-fused-prepare`

最早理由同上一项，引用同一 R1 delayed/compact 两 Run、ABBA，R7 B2 则读取新 A2/诊断反馈并保留失败备选解释。Plan `R2-B-b1-fusion` → `analysis:abeb92d20a458b717e0a386a:plan:a9a2b3167cb4428e`，静态 B==1 省去独立 prepare 调用，在 attention 内完成必要 Q/new/lengths 准备；B>1 保留父路径。模型接口1396736→19588 bytes，不等于 measured 流量或真实内存分配。

[候选源码](../phase3-2026-11-12/candidates/round2-b1-fused-prepare/mla_decode.py) 第2917–2932、2936–2947、3022–3029行；source SHA `dcd7c717156e21141294cf2eb7ce881fd408d833fd9af9f71dad987966ddba73`。真实 Run `wf-r:fb51ab1de12a6f5fbb079485c3d73086` 三 case passed，host0.213700 ms，program5.890000 μs、attention5.088125 μs，独立 prepare event **absent**，不能填0。correct_faster；原 promotion branch=true、task=false，A 的 length-row 仍为 Task Best。反馈写回 Snapshot26。

结果支持该固定 B1 small 的单 Run 改善观察，不能确认稳定 host 加速。原 B2 validation/unknowns 已明确：B1 fusion 仅由正式 small B=1 case 触发；boundary B=3、default B=8走父 fallback。此前独立诊断是另一份 single-tile 源码和 Context，**不能证明本融合分支的 B1 零长度、opaque-NaN 或 B1 多 tile 情况**。

## 审计发现与结论边界

没有发现删除慢结果、混用 A/B 父、把旧 Runtime 计时改标签、先选唯一赢家或 Oracle/Benchmark 为获利改变的迹象。四个第二轮源码机制在中断前已设计，恢复后读取新 Snapshot、重新建立当前 Context 父计时并分两批执行；所以能证明历史反馈驱动的两轮工作流，不能宣称每次 Runtime 重建都产生新独立优化轮。

记录的工具调用返回完整历史并引用其中精确结果，能够支持“系统交付并使用了这些固定历史”的证据；它不能证明模型在内部逐字理解所有历史、全部代码语义，或排除宿主上下文限制。来源 digest、predicate 与 replay 也不证明自然语言因果解释为真。

本审计核对的是 Analyzer / 输入 / Plan / 源码谱系；原始设备正确性、XPlane、样本数值和最新 Memory 投影由同日另外两份独立审计覆盖。测试通过与最终系统验收由主报告结合新完整软件回归决定，不以本报告代替其他 Gate。
