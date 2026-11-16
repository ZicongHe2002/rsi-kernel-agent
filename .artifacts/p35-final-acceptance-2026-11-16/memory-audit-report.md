# P3.5 Memory、Journal 与恢复证据独立审计

2026-11-16，审计者 `/root/accept_memory_recovery`。结论：**本审计范围 verified；没有发现阻止现有两轮系统闭环验收的 Memory、预算或恢复元数据不一致。** 当前远端状态没有查询，Runtime 4 的历史 correctness 结果继续明确保留为 unknown。

审计只通过 SQLite `mode=ro`、`PRAGMA query_only=ON` 与只读事务访问原库；没有构造可写 ExperimentStore、获取执行锁、申请设备、创建 Run 或更改预算。复核开始与结束的另一连接均为 **1505 records / high-watermark 1530、Snapshot 26、Journal revision 247**。原 Store、schema、回执和历史资料没有修改。

## 验证方法与原始输出

- [memory-audit.py](memory-audit.py)：逐条重算 1505 个原始记录 SHA256，通过原 schema 解码、规范 JSON 完整往返；精确重建 2925 条关系，与 `links` 表逐条比较，并核对同 Task、因果 seq 和策略归属。逐一校验 247 次 Journal 修订的 ID、previous_id、state_digest、预算限额转换及预留累计。
- [memory-audit-result.json](memory-audit-result.json)：Snapshot 原始字节、原 Store payload 重建摘要、所有 15 个 Context 的 Memory 状态卡、逐次预留和 grant。对应 [完整日志](memory-audit.log)，最后运行 54.16 秒，exit 0；包含引用目标类型、跨 Run/Context 证据关联及 grant 对精确前一 Journal 状态的追加检查。
- [memory-audit-recovery.py](memory-audit-recovery.py)：核对原 schema v1 定义与只读 `foreign_key_check`，重放 84 个 RunEvent 转换，检查全部 170 个请求与 169 个已保存回执，以及 Runtime 3/4/5/6 归档。
- [memory-audit-recovery.json](memory-audit-recovery.json)：操作闭包、历史 unknown、归档与带候选名称的全部 Context 表。对应 [日志](memory-audit-recovery.log)，exit 0。

为避免重复展开 689 MB Snapshot，审计用只读 SQLite blob 流式计算原始 SHA256，并以每条原始规范 payload、原 seq、冻结 metadata/projections 重建完整快照的字节摘要。两者逐一相等，所以不是只相信保存的摘要字段。Memory 状态投影仍调用原 `_Index` 和 `MemoryView.status_cards`；完成全部字节验证后，仅在内存投影视图中省略不参与关系、状态卡或 typed association 的大 note 正文，保留类型及原正文摘要。没有把这个内存视图写回 Store。

| 冻结 Snapshot | records / high-watermark | 实际 UTF-8 bytes | 实测原始与重建 SHA256 |
| --- | --- | ---: | --- |
| 4 | 198 / 223 | 2,571,220 | `cf0d42b54c1098ffce83004aeab9017af06aff5908395625a2be24d2b766568d` |
| 10 | 776 / 801 | 221,958,760 | `737cdcd9750c67a8e0c3724b784016179ba7efea903efa2f5cb9ab1a1f227a29` |
| 19 | 1199 / 1224 | 266,634,051 | `67658bd13462e8b12df8b2f2a73b609f5584fc2175ad3b8bb830a180ad259b77` |
| 23 | 1396 / 1421 | 500,376,926 | `a17b228ea2c89250d2758f037042789df7c996efe83d8faedd6faf7495346532` |
| 25 | 1400 / 1425 | 504,968,369 | `e7a8d236627c04f77cbc052c897ebba26c3d713fdfa6361b8e6ed77a6c50a0da` |
| 26 | 1505 / 1530 | 689,053,158 | `25c8c6475b595fbd471b15077625a36e1e3a7dbeec04415ac9253c6586cfd372` |

Snapshot 26 SQL `length(body)` 为 689,050,048 **字符**；它与 689,053,158 UTF-8 **bytes** 的差异来自编码，不能将二者混写。

## 最新投影与顺序语义

所有 19 条 Best、15 条 Frontier 均同时满足：冻结 Snapshot 26 与当前表相等、原 Promotion/Frontier Decision 按顺序重放相等、对应 Evaluation/Run/Candidate/Context 一致。严格更低且有资格才发生原晋升；更慢与未晋升结果仍在历史内。全部 15 个 Context × 3 条策略卡的 paused 均为 false、deferred 均为空；完整 ID、显式 Parent Decision 和历史状态见 JSON。

| Runtime 7 范围 | Branch Best | Task Best | Latest Attempt | Last Correct | Current Parent | Frontier |
| --- | --- | --- | --- | --- | --- | --- |
| 正式 Context / A | length-row | length-row | length-smem | length-smem | A 修复基线 | length-row、length-smem |
| 正式 Context / B | b1-fused-prepare | length-row | query-in-attention | b1-fused-prepare | delayed-store-wait | query-in-attention、b1-fused-prepare |
| 独立诊断 Context / A | diagnostic-baseline | diagnostic-baseline | diagnostic-baseline | diagnostic-single-tile | 原第一轮 single-tile | 两个诊断候选 |

**Latest Attempt 按原 Candidate/Run 创建记录顺序选择，Last Correct 按正确 Evaluation 的记录顺序选择。** 这两个时间顺序不同，B 的 Latest Attempt 为 query、Last Correct 为 fusion，以及诊断的 Latest Attempt 为 baseline、Last Correct 为 single-tile，均符合原 Memory 代码。Current Parent 是显式 `parent_selection.v1` 选择，完成后不会自动替换成新的 Best，也不是 Last Correct 的别名。

各 Runtime 的正式 Context Best 继续独立：Runtime 1 为 A 修复基线；Runtime 2 A 为修复基线、B/Task 为 delayed-store-wait；Runtime 3 A 为修复基线、B/Task 为本环境 delayed-store-wait；Runtime 4 没有正式 Best；Runtime 5 A/Task 为本环境修复基线、B 为 delayed-store-wait；Runtime 6 A 为修复基线、B/Task 为 delayed-store-wait；Runtime 7 如表。七个负例 Context 均没有 Branch/Task Best。独立诊断 Context 不覆盖正式 Context。以上比较仅重放各自原决策，未跨 Runtime 排名。

## 预算、终态和历史未执行

Controller 当前 **23 jobs：15 completed、8 cancelled、0 非终态**；没有本地 Run 保持 running。Task 没有取消。

| 预留来源 | executions | 声明秒 |
| --- | ---: | ---: |
| 第一轮五项 | 5 | 9000 |
| Runtime 3 ABBA | 4 | 7200 |
| Runtime 3 中断首项 | 1 | 1800 |
| Runtime 5 中断首项 | 1 | 1800 |
| Runtime 7 A2 | 2 | 3600 |
| Runtime 7 独立诊断 | 2 | 3600 |
| Runtime 7 B2 | 2 | 3600 |
| **累计** | **17** | **30600** |

总限额为 **18 / 32400**，剩余 **1 / 1800**。所有 247 次 journal 修订都满足 spent 等于 reserved jobs 成本之和，旧预留不减少、成本不改写，无重复 job 计费。探索 Journal 内 retry jobs=0、repair jobs=0；这不否认早期基线有单独的 `baseline_repair_intent.v1`，它未冒充 Controller repair。

唯一预算 grant 在 revision **210**，key `p35-runtime7-b2-after-a2-diagnostic-v1`：限额 16→18、28800→32400；spent 保持 15 / 27000。授权引用的原字节工件与原文 quote 已核对，grant 只增加既定 B2 两项成本；冻结 Snapshot 23、四个 A2/诊断完成 Run 和原回执闭包均绑定。本审计没有申请或增加 grant。授权记录是原用户声明的归档，不是密码学身份认证。

准入预算与探索预算分开：原库保留 baseline reservation 2 条（2 / 3600）、negative reservation 1 条（1 / 1800）、prerequisite reservation 22 条（22 / 39600）。这些是不同命名记账域中的声明预留，不改写成真实 TPU 账单或 wall-clock，也不混入探索 17 次。

全部 52 个历史 Run 当前为 succeeded 27、failed 11、incomplete 4、not_run 10。failed 包括故意错误负例和原基线失败；这些仍保留。8 个 cancelled Controller jobs 中，只有 2 个启动过本地预留，对应 Run 为 incomplete；其余 6 个 Run 仍为 not_run。不能把 Controller cancelled、Run not_run 和真实 Kernel 执行失败混为一谈。

## 恢复证据与未知边界

全部 **170 个 durable request** 的 operation ID 唯一，且都等于原 `stable_id(operation, run_id, stage)`；完整 request digest、Candidate source/config/build、Parent、原 Context、Runtime 身份都重新校验。**169 个回执工件**重新读取并散列，与原请求 envelope 绑定，没有同 operation 的第二个不同请求或回执。

唯一缺最终回执的是 Runtime 4 correctness：

```text
operation:079d2d129595c886b74f35bdc44630a39e7ab03cc9817e67719659c733f64c3a
runtime-run:c9e383063a3da8b8500b2a36596049ea28b0920595d66d4c229133f5626699a1
```

它保留 generate/compile/correctness 原请求以及 generate/compile 成功回执；本地最终 Run 为 incomplete，没有伪造 Evaluation，`remote_correctness_outcome=unknown`、`remote_cancelled=false`。**0 个本地非终态作业不代表这次历史远端操作已被证明成功、失败或未执行。** 本次无远端查询，不推断当前设备仍在线或仍运行。

| Runtime | 本次离线核验 |
| --- | --- |
| 2 | 原第一轮 5 项均有终态；保留当时 Runtime 丢失观察。完成回执及固定请求仍在，不能把旧计时标签换成 Runtime 7 |
| 3 | 原第二轮 4 个 Run 均为零 durable request/receipt；首项 cancelled/incomplete，另外 3 个 not_run；预留 1 / 1800 保留 |
| 4 | 上述历史 unknown 明确保留；没有把 unknown 当 definitely_not_started 或正确性失败 |
| 5 | 4 个恢复 Run 同样零 durable request/receipt；首项 cancelled/incomplete，另外 3 个 not_run；预留 1 / 1800 保留 |
| 6 | 原两 Context 只有已完成的 4 个准入 Run；探索 Run/Job/request/receipt 为零，未创建或取消不存在的候选 Run |
| 7 | A2、B2、诊断 6 个 Controller jobs 均 completed、没有活动 operation；固定结果与 Memory 投影一致 |

Runtime 3/5 各有一个 cancelled job 留着原始 `operation` intent，这属于归档证据。Controller 只推进 active/evaluating/blocked，不能根据这些非空 intent 就声称正在执行。归档文件摘要与原 Store Decision 完整 summary 对比一致。

## 代码审查与信任边界

审阅了原 Phase 2A/2B/2D+ 报告的 schema、只追加、上下文隔离和幂等约定，并核对当前 `store.py`、`memory.py`、`workflow/journal.py`、`budget.py`、`controller.py`、`colab.py`、`colab_worker.py`、`real_protocol.py` 与 worker recovery 指南。

- Controller 与 Backend 只有在 definitely_not_started 时才提交；running 继续 poll；unknown 留在 blocked 或明确失联归档，不自动重跑；completed 只取回原回执。本次验证的是保留证据和当前分支逻辑，未重造硬件失联。
- 同一 Runtime 的合作任务使用固定注册域、Runtime 锁与 operation 锁；更换 Task/spool 不能绕过既有 unknown。可见 Linux 进程设备句柄用于发现干扰；它不能证明隐藏进程、其他命名空间或整个采样间隔绝对独占。
- 原代码不强杀用户 Notebook。这个结论来自源代码分支，离线材料不能证明所有历史外部活动。
- request/intent 持久化、原子完成文件、本地幂等和稳定 operation ID 可避免协议内盲目重派，**不构成任意分布式故障下的 exactly-once 保证**。掉电或 Runtime 丢失而没有完整完成回执时保持 unknown。
- 回执可信依赖原 worker/runner、桥接配置、隔离路径和本地工件根可信。摘要保证保留字节身份，不是对恶意 producer 的硬件执行证明。
- `loaded_files` 约束 bundle 字节及已载入 module 路径，不能证明每一个 bundle 文件都执行过；NPZ、设备运行后输入 cache 以及 XPlane 内容的独立数值结论由另一个原始证据审计负责。

本审计没有新增软件修复或改变既有 Decision，也不把晋升与稳定性能收益混为一谈。建议最终系统验收继续携带上述有限范围和历史 unknown 限制；单独的性能结论需要采用原始计时审计结果。
