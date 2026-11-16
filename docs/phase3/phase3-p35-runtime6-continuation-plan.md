# P3.5 Runtime 6 继续执行计划

> 历史计划。后续 Runtime 7 已完成原第二轮与诊断；当前状态见[最终验收](phase3-final-acceptance-report.md)。本文保留当时的准入、恢复和预算依据，不作为新的执行指令。

2026-11-13 23:01（Asia/Shanghai）。继续完成第二轮真实反馈与独立单 tile 诊断；原四个恢复候选的总目标仍未完成。本文采用 23:01:40 的本地准入 checkpoint，不把同步探针、准备工作或本地测试计为硬件准入通过。

## 已核验的起点

Runtime 5 恢复第二轮的四个 Run 已证明没有 durable 远端提交请求或回执。原 Journal / Evaluator 已完成归档：首项 `length-row` 为 cancelled / incomplete，另外三项保留 not_run；原 operation、源码、Context 和历史结果完整保留，Task 未取消，也没有退回首项预留成本。

归档为 **Controller revision 151、Snapshot version 14 / 986 records / high-watermark 1011**。原探索预算为 **11/16 executions、19800/28800 声明秒**，剩余 **5 次 / 9000 秒**。独立只读核验确认旧 13 个作业保持原样、1920 条关系闭包完整；22:59:28 的 live Store 为 999 records / high-watermark 1024，与较早的 Snapshot 14 分开记录。四个 Run 的最新事件再次核验为 1 incomplete / 3 not_run。

证据：[原归档](../../.artifacts/phase3-2026-11-12/real-round-2-runtime5/runtime-loss-archive.json)、[真实归档只读核验](../../.artifacts/phase3-2026-11-12/runtime5-loss-independent-verification-20261113.json)、[核验说明](../../.artifacts/phase3-2026-11-12/runtime5-loss-independent-verification-20261113.md)。

## Runtime 6 当前状态

原申请流程于 22:28:32 取得 `tpu-v6e1-s-euw4a0-1q01sb2h5jte9`。固定隔离环境已实测：Python 3.11.17、JAX/jaxlib 0.8.1、libtpu 0.0.30，单 TPU v6 lite／单进程；同步 JAX 探针返回 **140.0**，原 parser 可导入。

四项重新准入为 attention 错误负例、cache 错误负例、A 修复基线和 B delayed-store-wait，使用新环境的 Context / Run、独立 nonce 与 remote root。原准入驱动正在推进。本地 [runtime6-progress.jsonl](../../.artifacts/phase3-2026-11-12/runtime6-progress.jsonl) 显示首项 attention 的 generate 于 **23:00:20** 完成；compile 于 **23:00:49 running**，随后 **23:01:40 completed**。截至该 checkpoint，尚无首项最终正确性结论或四项全部通过的核验报告，不能宣布准入完成或切换 active binding。

证据：[环境](../../knowledge-site/manifests/phase3-2026-11-12/environment-isolated-runtime6.json)、[同步探针](../../knowledge-site/manifests/phase3-2026-11-12/isolated-probe-runtime6.json)、[准入兼容本地验证](../../.artifacts/phase3-2026-11-12/p35-runtime6-admission-local-validation-20261113.json)、[独立审查](../../.artifacts/phase3-2026-11-12/p35-runtime6-admission-independent-review-20261113.md)。这些软件验证不代替四项真实准入结果。

## 先使用现有预算完成两项 A 候选与两项诊断

四项准入全部完成并核验后，读取包含 Runtime 3/4/5 归档和 Runtime 6 准入结果的新 Snapshot。原 Analyzer 必须实际读取历史、源码、Profile 和当前 Context 的父计时，再发布本批两个 A Plan；旧 Runtime 3 ABBA 只作历史经验，不作新环境性能分母。

| 本批内容 | 目的 | 探索预算 |
| --- | --- | ---: |
| A `length-row` | 将 lengths 中间表示缩为 `[B,1,T]`，在 attention 内广播 | 1 次 / 1800 秒 |
| A `length-smem` | 去除 prepare 的 lengths 中间输出，从原始 `[B]` SMEM 长度读取并广播 | 1 次 / 1800 秒 |
| 独立诊断 baseline | 固定单 tile workload 的正确性对照 | 1 次 / 1800 秒 |
| 独立诊断 single-tile | 核验原 single-tile 分支的零长度、全无效 attention、完整 cache 与 opaque NaN 契约 | 1 次 / 1800 秒 |

本批共 **4 次 / 7200 声明秒**。如果没有额外 repair / retry 预留，累计预算由 **11/16、19800/28800** 到 **15/16、27000/28800**，保留 **1 次 / 1800 秒**。该余量没有被退回、提前支出或默认为额外性能复测。准入成本仍单独记账。

两项 A 候选必须保留所有真实成功、失败、更慢和非晋升结果。诊断使用独立 workload / Context 与固定 Oracle，不改旧结果、容差、输出契约或计时政策；至少一个新 A 候选完整通过真实 compile、correctness、benchmark 和 profile，并形成完整五阶段证据，才可作为后续成功里程碑。

## 两项 B 候选的条件与明确预算批次

`query-in-attention` 与 `b1-fused-prepare` 仍属于原四候选目标，当前只是推迟，未被删除、替换为完成或宣告无须执行。

只有以下事实全部成立后，才准备本批 B 的真实 grant：新 A 恢复轮至少一项 **correctness passed、timing valid、confirmed=true、Profile 及完整绑定回执通过**；两项独立诊断已实际完成并核验；本批全部结果已回写原 Memory，并在新 Snapshot 中核对 Best、Frontier、Current Parent、Latest Attempt、Last Correct 和预算。第一轮旧成功、旧 Runtime 的 ABBA、纯 generate 结果或取消归档都不能代替这个新里程碑。

届时通过原显式接口记录一个有界批次：**2 executions / 3600 声明秒 / 0 tokens**，唯一 next_batch 标识明确绑定两项 B 候选、当前 revision、state digest、完整旧/新 limits、用户继续授权和刚完成的里程碑 Snapshot。若此前无其他 grant，限额由 **16 / 28800** 增至 **18 / 32400**，累计 spent 原样保留；两项 B 正常执行后预计累计 **17 / 30600**，仍保留原一项余量。实际数字必须再次读取 Journal，不能以本计划的估算覆盖真实预留。

**目前没有提交这笔 grant。已有 grant 接口的本地实现不等于持续执行流程已经交付；新的 continuation 辅助尚未完成交付与独立审查，不能宣称自动循环就绪。** 后续必须先完成入口与授权额度链的兼容、验证和审查，再创建具体批次；不能修改旧冻结限额、放宽旧 Gate 或借用旧轮成功绕过条件。

## 恢复与后续迭代规则

1. 每次先查询原 operation：completed 只回收，running 继续 poll，unknown 保留核查，仅 definitely_not_started 才提交原请求。不得通过新建身份重复派发。
2. Runtime 消失时先保存原请求、回执、运行状态和预算，再区分零提交与已提交未知结果的归档路径；新设备必须重新准入。
3. 没有可用 TPU v6e 时，沿原唯一申请循环持续申请，保留全部尝试与分配证据；实际 allocated 后转入原环境与准入流程。
4. 第二轮成功后按用户授权继续自主迭代；每个后续批次仍由新的真实反馈和可审计预算驱动，完整记录“上一轮依据、修改、TPU 结果、Memory 变化与下一步判断”。

当前仍未完成第二轮硬件闭环、两项单 tile 诊断、原四候选总目标及 P3.5 最终验收；不宣称稳定端到端性能收益。
