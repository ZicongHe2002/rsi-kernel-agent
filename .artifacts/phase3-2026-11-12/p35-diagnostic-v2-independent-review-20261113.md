# Runtime 4 single-tile 诊断 v2 独立审查

2026-11-13 18:50 Asia/Shanghai，Reviewer `/root/p35_recovery_review`。未修改实现代码、未调用桥接、未执行 TPU、未写真 Store。

已审范围内未发现未关闭 Critical / Important：

- selection schema v1 继续固定 Runtime 3，原 scope 文件不覆盖；v2 仅接受 `runtime_generation=runtime4`，单独冻结 scope，并由整个 selection 的摘要决定目标目录。优化轮 ID 不能被用作诊断轮 ID。
- v2 调用原 Runtime 4 四项 gate 和归档/历史 ABBA 校验，ABBA 保持旧 Context。`allowed_round_id` 仅放行调用者正在恢复的诊断轮，其余活动作业仍被拒绝；调用者先严格校验 selection。
- `completed_round` 对第一轮及 `real-runtime4-recovery-round:2` 核验原 intake、全部终态结果和 workflow completion marker。原 Runtime 3 四项取消归档不能冒充这一新优化轮完成。这里 completed 指原工作流的终态闭包，失败尝试仍可被保存；它不单独宣称优化收益或全部候选成功。
- 两个诊断角色仍由原未改 `prepare_diagnostic` 固定基线/原 single-tile 源码 SHA、small/boundary shapes、zero-length/opaque NaN 数据及块尺寸。创建独立 Workload/Context，LSE 仍标未暴露/未测，不修改旧 correctness 契约。
- 原派发器保持恰好两个 role、无 parent benchmark、两次固定 cost、预算 checkpoint 与原 Journal revision 对齐，以及恢复期间对其他 job 的完整一致性检查。新路径只选择 Runtime 4 backend config。
- prepared history 在 prepare 与 run 前均核对先前 A 路线完整结果；v2 Snapshot 还要求包含归档 intent/result、ABBA policy/result/association 与四个原 ABBA Run。只读 verifier 增加了 v2 Runtime/context 绑定核对，实际逐 job 校验仍交给未修改的 `verify_job`。

独立运行 `test_p35_diagnostic_dispatch`（包括 v1 与 v2 继承回归）、`test_verify_p35_diagnostic`、`test_p35_recovery_round`：**39 tests，147.742 秒，exit 0，OK**。测试 stdout 保存在 `p35-diagnostic-v2-independent-tests-20261113.txt`；asyncio 慢回调诊断出现于工具 stderr，不是失败。全部使用临时 CPU fixture/mock，不代替真实诊断结果。

限制沿用原核验器：NPZ 校验不证明运行时 input cache 没被修改（该项来自 producer check），未重算 opaque cache finite metadata，未独立解码 XPlane protobuf。其他旧 driver 不使用附加诊断 OS 锁，运行须继续保持单驱动约定。

审查时源码 SHA256：

```text
p35_prepare_diagnostic_round.py d49728a42980805e87e42ec361c9d0cdef7973a8b4d0e14354d889d776f5dc70
p35_run_diagnostic_round.py 53f611df3f4b738b1b1c7d96a93797b7165ae05c04af9741021b47d54004d53c
verify_p35_diagnostic.py 320e5ab0b9ae7af350d31fece099ad714990c68ab7077bcc969d48b8b5e906ff
p35_single_tile_diagnostic.py 722d0f3ef1b1db3d0f4159076674f782bb6aa6f78bbc12875536135547776041
p35_recovery_round.py c176ef53d10bd5c70e3faf45e51ec9c3988f4a7da92ff5713d1e645f4337ffcb
```
