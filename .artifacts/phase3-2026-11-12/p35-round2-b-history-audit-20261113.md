# Round 2 B 冻结历史审计（2026-11-13）

本审计只读取冻结的 `real-round-2/prepared.json`、`packet.json`、先前确认轮 prepared、不可变 artifact 与相关状态卡计算源码；未打开真实 Store/SQLite、未联系 TPU/bridge、未提交 Analyzer/Plan。正式 Analyzer 阅读和 Round 2 准入由根代理执行。

## 冻结范围与完整性

- B：`mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd`。
- snapshot：`snapshot:ceba41a03b4d4e5b974d1865fa0699e9`，version 10，record_count 776，high_watermark 801，digest `737cdcd9750c67a8e0c3724b784016179ba7efea903efa2f5cb9ab1a1f227a29`。
- prepared 原始文件 SHA-256：`c789aaf64309ddd3b5a61b554f55cc4900dc1ee60d44d861bc5da7529be8d8e5`。
- B history `complete=true`、offset 0、next_offset null，共 501 条，501 个唯一 ID；owned 371、dependencies 130，二者无交集且并集正好等于全部记录 ID。
- 对照 `confirmation-f686274a57bb7ebb/prepared.json`，之前已完整审计的 315 条记录全部逐条完全相同，无缺失、无更改；新记录为后缀 186 条。
- 全部记录类型：Decision 288、Evidence 113、RunEvent 26、Run 13、ProfileBundle 13、EvaluationBundle 13、Candidate 9、EvaluationContext 6、Plan 6、ParentCandidate 6、AnalysisBundle 3、Workload 2、Strategy 2、Task 1。
- 新增类型：Decision 122、Evidence 36、RunEvent 8、Run/Candidate/ProfileBundle/EvaluationBundle 各 4、Plan 2、AnalysisBundle 1、ParentCandidate 1。
- 新 Decision 明细：plan_parent 2、workflow_candidate 4、workflow_state 58、execution_request 20、execution_receipt 20、evaluation_intent 4、promotion/frontier 共 8、workflow_result 4、confirmation_association 1、parent_selection 1。36 条新 Evidence 均 available/completed/supported；每个 ABBA Run 有 generate/compile/correctness/benchmark/profile 五阶段证据及 correctness/timing/raw-profile/profile 证据。
- 独立复核 packet 与 prepared 的 B history/request/parent/Run/benchmark/Profile/source/config 等 12 项绑定，全部相等。对 B history 子集中的 58 个 workflow_state 检查 state_digest/revision/ID；对 20 个 execution_request、20 个 receipt 检查目标、引用和 digest；对 4 个 result 检查 Run/Candidate/Evaluation/Profile 与 Evidence 递归闭包，异常为零。这里的 58 条不是全局 journal 的所有 revision。
- 20 个 stage receipt 均 succeeded，每个新 Run 恰好五阶段，阶段 artifact 与 Profile evidence 均落在对应 Evaluation 的证据闭包中。独立流式验证 171 个唯一 artifact 的 SHA-256 和 size，总计 514837388 bytes，异常为零。此检查没有重跑 Oracle/NPZ 数值比较或真实 receipt 完整业务验证。

B 的 owned 历史包括种子与 Round 1 的两项优化（delayed store wait、compact new row），不是 A 的三项 Round 1 优化候选。A 完整历史另有 268 条。B 内嵌 workflow journal 能包含任务级九个 job 的状态，但不能将其中 A 的三项当成 B owned Candidate/Run。

## 当前父候选与状态卡

Round 名称仍是 `real-runtime2-round:2`，但实际 Context 为 Runtime 3：

- context：`runtime-context:559e042239cb327b2d894d6535b09b9a7b406dbfff2dc55e5852311c430ccab3`。
- runtime：`tpu-v6e1-s-euw4a1-39sjw4nm2h4b9`。
- workload：`mla-workload:9b54967173a4302d81eb956f8bdecb44bf807077d43eaf29d0b5c95977227c4a`。
- 新 Round 2 ParentCandidate：`wf-parent:88fc2af19b8d773ad8ce15664ff31573f958e8a3` → 原 delayed 候选 `wf-c:afa5b476cde0e73bb0bedb604b2d5c51`。
- source：`0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34`。
- config：`078d6739d75ab25fadee5c0f5b71b6862497d782e2da7295f6d77c00241555f6`。
- build：`c83a19210cad11dd991d700629b4a83695af0409dd4312f66eb13788f1a829a1`。
- source bundle：`1b7e59a51fd2e502c8e12adc0dcd8df89bb8dc673607d10f7d4666e936260a14`。
- Parent 选择 Decision：`parent-select:7aeb53eae5c5562db02e8513484c245aa65423333b6fe005ccdfea5f4d90d292`，原因 `Deterministic round parent policy: branch_best`。
- branch_best 与 task_best 均仍为 Runtime 3 admission Run `runtime-run:a6edb49816560c20fd9b88c5bf6879163af7eef9dfeff05d6178eda87489b6cc`，Evaluation `evaluation:8a55115d713750fd00c56448ae00906b56c0440fbc5821779b5a84da8976b762`，median 0.21720500001265464 ms；Profile 同后缀。
- last_correct_candidate 是 C2：Candidate `wf-c:73f61cad427a1b9762fbdfa349700516`，Run `wf-r:8395d67f949b364daf53a8197620bef7`。
- latest_attempt 是 T2：Candidate `wf-c:c864fc271b2913190488f7c250526fb0`，Run `wf-r:396d629c0dee5027ce556961ec76b841`，succeeded/finished。
- latest_attempt 按 Run 插入顺序选择（memory.py 的 Run `index.order`）；last_correct 按成功 EvaluationBundle 更新。不能把 latest_attempt 解读成真实执行或完成顺序。
- frontier 为四个 ABBA 候选，deferred 为空，paused=false；暂停与明确恢复事件保留。recent_plans_complete=true，recent_plans_total=5（B seed、两个 R1 优化、两个 ABBA role Plan）。

## 历史结果与不晋升范围

| 记录 | Context 范围 | host median ms | 解释 |
|---|---|---:|---|
| 原 authoritative Run `mla-run:02c54a…` | 原环境 | 无 | compile_failed，correctness/timing unknown，Profile unavailable |
| baseline repair `22398773…` | Runtime 1 | 0.2207150005233416 | correctness passed；不能跨 Runtime 比较 |
| A repair `4686bd6e…` | Runtime 2 | 0.19319499995162914 | B 历史中的依赖 |
| B seed `c339eda9…` | Runtime 2 | 0.21536499980356894 | B R1 的父基线 |
| B delayed `9693f8c2…` | Runtime 2 | 0.18379500011178607 | correct_faster，当时 branch/task promoted |
| B compact `fc2da20b…` | Runtime 2 | 0.22187500007930794 | correct_slower，eligible 但 branch/task 均未晋升；保留探索路线 |
| A repair `f96b0179…` | Runtime 3 | 0.23240000007263006 | 新 Context 的独立 admission |
| B delayed admission `a6edb498…` | Runtime 3 | 0.21720500001265464 | 当前 branch/task Best |
| B original control `f43fa956…` | Runtime 3 | 0.22891499997967912 | eligible，但未替换 Best |
| ABBA C1 `52bab238…` | Runtime 3 | 0.22209999997357954 | correct_slower，未晋升 |
| ABBA T1 `5457d241…` | Runtime 3 | 0.22394500047084875 | correct_slower，未晋升 |
| ABBA T2 `396d629c…` | Runtime 3 | 0.2712400000746129 | correct_slower，未晋升 |
| ABBA C2 `8395d67f…` | Runtime 3 | 0.24252000002888963 | correct_slower，未晋升 |

四个 ABBA Run 均 correctness passed、timing valid、confirmed=true、Profile available，promotion Decision 均 eligible=true、branch_promoted=false、task_promoted=false。它们的 `correct_slower` 是相对于共同父 admission 0.217205 ms 的固定评估分类，不等同于 ABBA 配对统计结论。

B seed Candidate 为 `strategy-seed-candidate:c642e60add3890d7092175549edd57a26ea1441a5dd4f54f54195cd8cb036270`，source `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`，build `f224e0eda278e5150d8a592337ced97b1a8009a6d1576475dba618848df52bfd`，bundle `adf92f4b0c2dac129f6908ba8700c36fbd4143ea09a5774a426f31fc55d5cd13`。baseline repair 把完整 newKV batch 先 pad 再切行，是兼容性修复而非优化。R1 exact parent `wf-parent:2ceae14208b284845423989d29e280295b3cdd5e` 绑定此种子。

Compact Candidate 为 `wf-c:77014d402daf890f724cb8be8220b97f`，source `c9e270a86764555f38cbbfe658857d9c48bf9d0e8163a1b7d4edc41959236c0d`，build `bed93ea23e62342406579fd2d33aa338293df6be9d814abb8006567c43d69231`。历史 journal 的 blocked/recovered 事件是控制器执行恢复证据，不能写成数值失败。新 ABBA journal 也包含 outcome_unknown 与 reconciliation，但最终四个 Run 均已有 succeeded/finished 及固定评估。

## ABBA 关联与统计限制

B history 第 498 条（从零计）为 `p35-confirmation-association:16aab6048bbeac5c67284d64a0ceacf30b01fdf24adfcfe465982b213dfdc7d0`，类型 `p35_confirmation_association.v1`。它引用 confirmation `p35-confirmation-result:36fab5ac46dadc75ee2bdd9e0925941ede08f5210a04df9fa6bc30fc19fc8589`，报告 artifact `bd4abd9b250ba3a76f333f004c2d2429ce02d0c1a3470b39a431f3a782e5e6e7`，871816 bytes。artifact 原始字节 SHA-256、大小均匹配；关联 summary 与完整报告 statistics 完全相等。

B history 本身没有全局 result/policy Decision，也没有 A association；这是策略历史范围，不能声称全局记录是其中的顶层记录。B 的 association 提供报告与确认结果的可追溯引用。

固定 policy：最低配对改善 2%，同 role 两次 Run 的最大 spread 5%。执行顺序 C1/T1/T2/C2，每 role 仅 2 个独立 Run；每 Run 20 个相关样本，不可把 40 个样本当 40 个独立实验。

- host 总结 `inconclusive_drift`：两对 reduction −0.8307071128%、−11.842322…%；control/treatment spread 分别 8.789979…%、19.101952…%；三项标准均 false。
- program trace 为 `replicated_observed_improvement`：两对 reduction 6.495840…%、5.921798…%；control/treatment spread 0.113400…%、0.725441…%。这是当前小样本下观察到的 trace 指标变化，不能替代端到端 host 计时。
- 总 outcome 为 inconclusive；claims 中 confidence_interval、statistical_significance、generalizes_beyond_two_runs、store_promotion 均 false。

## Round 2 可用证据与边界

当前 admission Profile 有 60 个事件：program/prepare/attention 各 20；相应 median 为 6.205 / 0.719375 / 4.61875 μs。Host 和 trace 分别度量不同范围，不能相加或直接解释为同一端到端时间。

可使用 source/config、编译/正确性/benchmark receipt、host samples、规范化 trace intervals 与 ABBA 反证提出假设；没有独立 DMA wait、关键路径、VMEM/MXU occupancy 或元数据瓶颈测量。原始 protobuf 未独立解码，identity 状态为 declared_identity_only。

成功历史 Run 的正确性使用原固定 CPU Oracle：small/boundary/default 三组，atol/rtol 0.01，并检查完整 BF16 cache 字节和输入不可变性。计时只覆盖 small、5 warmup 后 20 samples，输出同步；这些范围不能外推到未测形状，也不能跨 Runtime 合并。

本审计不证明新的 Round 2 代码在 TPU 上可编译、正确或更快；这些必须由后续固定真实执行产生证据。

## 候选静态复核补充

本轮另对四个候选目录做了只读 AST/字节范围复核。A 的 `round2-length-row` 与 `round2-length-smem` 均以 `baseline-repair-1`（source `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`）为精确父，打包 source_ref、parent_source_ref、config_ref 与文件字节匹配；其 attention 主体从 offsets/mask 到 cache DMA、online recurrence、归一化均与父 AST 相同。两者只改变 lengths 传输：A-row 将 `[B,H,T]` 缩为 `[B,1,T]` 后在 attention 广播；A-smem 去掉 prepare 的 lengths 中间量，使用 SMEM raw `[B]` 并在 attention 广播。两者 AST parse/compile 均通过；真实 SMEM/VMEM lowering、数值、VMEM 容量与 TPU 计时仍未验证。

B 的 `round2-query-in-attention` SHA-256 为 `be122d552e40e9501055a9643ca349871cfae726f7740c27e1e03cc99e1c258f`，`round2-b1-fused-prepare` SHA-256 为 `dcd7c717156e21141294cf2eb7ce881fd408d833fd9af9f71dad987966ddba73`。两者均以延迟 store-wait 父 source `0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34` 为父；均保持 factory/config、输入校验、非 contiguous 代码和 attention 主体后缀不变并通过 AST/compile。Query 仅把 raw q（`(1,H,D)`）移入 attention 再 pad；B1 仅在静态 `batch==1` 旁路 prepare，在 attention 对完整 `(1,D)` newKV 先 pad 再 broadcast，`batch>1` 的原 prepare 路径保持。四个候选都没有 TPU compile、数值或性能证据。
