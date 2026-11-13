# 单 tile 诊断归档核验（2026-11-13）

`verify_p35_diagnostic.py` 仅封装原 `verify_real_round.verify_job`，不改原 verifier。原 `verify_npz_cases` 从 `job.contract.workload.spec.cases` 得到 case 数，并以 `len(cases) * 6 + 1` 验证归档结构，没有写死三组，因此可用于新的 small/boundary 两组独立诊断。parent latency 也从原 job 读取，允许无 parent Evaluation 的诊断 Context。

调用示例：

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts\phase3-2026-11-12\verify_p35_diagnostic.py' '.artifacts\phase3-2026-11-12\p35-diagnostic-selection.json'
```

封装使用原 selection 校验和 target 计算；将 `controller-status.json` 逐项绑定到原 journal revision/digest，限制到 selection 的 task/strategy/round 与独立 diagnostic Context，然后调用原 `verify_job`。SQLite 连接使用 `mode=ro` 与 `PRAGMA query_only=ON`。不创建 Controller、不调用 bridge/TPU、不调用 append/consider。唯一写入是 target 的 `local-verifications/<UTC时间戳>-r<revision>.json`，以独占创建模式保留历史。

没有 status、未准入全部角色或尚在执行时报告 pending，CLI 退出码 0。验证不一致时报告 verification_failed，CLI 退出码 1。运行中 status 变化会记录 `status_file_changed_during_verification`，结果仍明确绑定所读 revision。`--no-npz` 显式关闭独立 NPZ 比较，并在报告标注限制。

新封装六项回归与原 verifier 十二项回归共 18/18 通过（4.657 秒），日志为 `p35-diagnostic-verifier-tests-20261113.txt`。覆盖 missing status 不打开 Store、pending 非失败、status 篡改不能到达 job 验证、原失败判定保留、CLI 连续调用生成不同报告且不创建 DB，以及数据库无法打开时仍保存失败报告。测试只操作临时 fixture；本交付没有调用真实诊断执行器。

## 保存证据能证明的范围

`mla_runner.py` 每 case 保存六件 artifact：执行前 `inputs.npz`、Oracle outputs、Oracle metadata、specialization、HLO、candidate outputs，末尾再保存 input manifest。输入 NPZ 的 `array_2` 是执行前 cache；返回 NPZ 的 `array_1` 是新的 cache 输出。原 verifier 会按 contract 重新核对 attention 数值、完整 opaque BF16 cache 字节与所有 artifact 的哈希/大小。

输入 cache 不变性不能从当前归档独立复验：运行器在调用后用 `inputs[2]` 与原 host cache 比较，但没有归档调用后的 device input 内容或摘要。失败时原 receipt 保存 `metric_reason="Candidate mutated the input cache"`。输出与 Oracle 相等，甚至零长度输出与输入字节相等，都不能证明原 device input 未被修改。封装保留 producer 判定，明确写出此限制，不能将成功 case 描述为独立重验了输入不变性。

Cache `finite` metadata 可以从归档独立重算，但本封装按“只复用、不新增验证语义”的范围没有实施：读取 actual 与 Oracle 的 cache BF16 words，用 `(word & 0x7f80) == 0x7f80` 识别 NaN/Inf，分别检查全有限后取逻辑 AND，即是原 receipt 的 finite 定义；同时须检查 NPY shape/dtype/长度与内部 SHA。原 exact cache 判定仍是字节相等，`finite=False` 不能改成 correctness 失败。

独立只读抽查 Runtime 3 baseline small/boundary 已验证该方法：small 双方均无 NaN/Inf，finite=true；boundary 双方各有 171648 个 NaN、无 Inf，finite=false，与 receipt 一致且 cache passed=true。这只证明两组样本的可复算性，不代表全部历史或未来诊断已完成 finite 复核。原 verifier 的 exact-cache 路径目前仍标 `finite_recomputed=False`。
