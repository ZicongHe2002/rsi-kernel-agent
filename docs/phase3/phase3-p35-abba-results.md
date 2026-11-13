# P3.5 ABBA 独立确认结果

2026-11-13。四个真实 Run 已完成，原发布器核验原始 NPZ、完整回执、身份、计时样本与 Evidence 后写回唯一 Store。独立 reviewer 对保存证据和统计再次只读复核。

**结论：host public-call latency 为 `inconclusive_drift`；program duration 为 `replicated_observed_improvement`；联合判定为 `inconclusive`。** 本次两对 program median 分别降低 6.49584% 与 5.92180%，不构成稳定端到端加速或更广 workload 的证明。

| 顺序 / 角色 | host median (ms) | program median (μs) | prepare median (μs) | attention median (μs) |
|---|---:|---:|---:|---:|
| C1 control-before | 0.222100 | 6.610000 | 0.693750 | 5.043750 |
| T1 treatment-first | 0.223945 | 6.180625 | 0.696250 | 4.643125 |
| T2 treatment-second | 0.271240 | 6.225625 | 0.695625 | 4.633125 |
| C2 control-after | 0.242520 | 6.617500 | 0.693750 | 5.065000 |

所有 Run 保留 20 个 host 样本和独立 Profile 的 program/prepare/attention 各 20 个样本，未剔除异常点。原五阶段均 succeeded，三组正确性通过，timing valid、confirmed=true；原 Evaluator 对当前选定父版本均归类 correct_slower，这与独立确认判定属于不同契约。

预先冻结的 `p35-abba-v1` 按 Run 为统计单位：两对 median reduction 均至少 2%、所有 treatment medians 严格低于所有 control medians、每个角色的 `(max-min)/median` 不超过 5%。host 两对 reduction 为 −0.8307% 和 −11.8423%，control/treatment 角色 spread 为 8.7900% / 19.1020%。program 两对 reduction 为 6.49584% / 5.92180%，角色 spread 为 0.1134% / 0.7254%。prepare 基本相同，attention 下降对应 program 变化，但不能据此证明独立 DMA 时长、物理资源利用率或因果机制。

Runtime 为 `tpu-v6e1-s-euw4a1-39sjw4nm2h4b9`，Context 为 `runtime-context:559e042239cb327b2d894d6535b09b9a7b406dbfff2dc55e5852311c430ccab3`。control 源 SHA256 为 `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`；treatment 源为 `0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34`。源、config、build、协议、输入及 operation 身份均核验；C–T–T–C 顺序及 benchmark→profile→下一 Run 的时间不重叠。桥接临时 unknown 由原 Controller 查询原 operation 恢复，未创建替代 Run、重置预算或重复扣费。

完成快照为 version 8 / 770 records / high-watermark 795，原探索预算累计 9 / 16 executions、16200 / 28800 声明秒。确认结论随后写入 Task Decision 及 A/B 两条策略关联记录，供第二轮新的 Snapshot 读取；本轮确认不是第二轮优化。

完整原始样本、描述统计和逐项验证在 [发布工件](../../.artifacts/phase3-2026-11-12/artifacts/sha256/bd/bd4abd9b250ba3a76f333f004c2d2429ce02d0c1a3470b39a431f3a782e5e6e7)，其 [Store 指针](../../.artifacts/phase3-2026-11-12/p35-confirmation-result.json)、[完成快照](../../.artifacts/phase3-2026-11-12/confirmation-f686274a57bb7ebb/completed.json)、[只读复核](../../.artifacts/phase3-2026-11-12/p35-abba-independent-review-20261113T092452Z.json) 和 [恢复范围审查](../../.artifacts/phase3-2026-11-12/p35-confirmation-recovery-review-20261113.md) 保留原始证据。

限制：每角色只有两个独立 Run；Run 内 20 样本不等于 20 次独立实验，无置信区间、显著性或普遍加速主张。发布器逐值重算原始 NPZ，但未再次设备执行验证 input cache 不变，也未独立重解码 XPlane protobuf。独立 reviewer 核对 SHA/闭包/保存 parser 和统计，未重复发布器 NPZ 比较。历史 Best 按原 consider 保留，确认统计不直接修改晋升指针。

最新根级回归已于 17:26:44（Asia/Shanghai）结束：321 项、318 通过、3 跳过、0 失败/错误，567.523 秒，exit 0；见 [日志](../../.artifacts/phase3-2026-11-12/p35-root-tests-final-2026-11-13.txt) 与 [命令和退出码](../../.artifacts/phase3-2026-11-12/p35-root-tests-final-2026-11-13-result.json)。第二轮及单 tile 硬件诊断仍在继续，P3.5 尚未最终验收。
