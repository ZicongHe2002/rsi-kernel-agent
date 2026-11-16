# P3.5 辅助测试异步夹具清理修复

2026-11-16；仅本地临时 fixture 测试，无网络、TPU、正式 Store 写入或预算消耗。

独立运行恢复组 16 项通过；在其前运行 `DiagnosticRuntime6FlowTests` 后，17 项重现原辅助全套中的 10 个失败、2 个错误。根因在测试生命周期：手动创建的 `IsolatedAsyncioTestCase` 只调用 `asyncSetUp()`，没有启动自己的 unittest asyncio runner，却把其 `doCleanups()` 注册到外层测试。CPython 3.13 的异步 `_callCleanup` 要求 runner 已初始化；嵌套 `doCleanups` 把该异常计入自己的临时 outcome，外层测试没有接收到错误。结果 `ExitStack.close` 没有执行，`require_recovery_context` 的 mock 泄漏到后续恢复测试。

修改仅涉及下面五个辅助测试文件、六处嵌套 fixture。每处在 `await fixture.asyncSetUp()` **之前**设置 `fixture.addCleanup = self.addCleanup`，并移除对未运行 fixture 的 `doCleanups` 注册。清理直接由正在运行的 owning TestCase 执行；setup 中途失败时也已注册清理。未修改任何断言、生产函数、恢复防线、真实执行历史或配置。

| 文件 | 修改位置数 |
| --- | ---: |
| `test_p35_diagnostic_dispatch.py` | 1 |
| `test_resume_p35_diagnostic.py` | 1 |
| `test_resume_p35_diagnostic_independent.py` | 2 |
| `test_verify_p35_continuation.py` | 1 |
| `test_verify_p35_continuation_independent.py` | 1 |

上述文件均位于 `.artifacts/phase3-2026-11-12/`。完整原文件字节保存于 [修复前副本](traceability-cleanup-before/)，精确差异见 [diff](traceability-cleanup-fix.diff)，修改前后 SHA256 与仅预期改动断言见 [变更事实](traceability-cleanup-changes.json)。同步 `TestCase` 的嵌套 fixture 不存在本次缺失 asyncio runner 的问题，保持原样。

| 验证 | 结果 | 清理与 mock 状态 |
| --- | --- | --- |
| 原顺序 RED：诊断 flow + 恢复 16 项 | 17 项，10 failures / 2 errors，29.367 秒，exit 1 | 嵌套 `DiagnosticRuntime4Tests.runTest` 有 2 个清理回调、无 runner、无自己的 outcome，`doCleanups` 返回 false；恢复 gate 未恢复 |
| 完全相同顺序 GREEN | 17 项全通过，29.060 秒，exit 0 | 17 次清理调用，无 unsuccessful return；恢复 gate 与测试前对象身份一致 |
| 其余嵌套路径 GREEN | 3 项全通过，67.891 秒，exit 0 | 覆盖两项诊断 independent consumer 与一项 continuation independent consumer，4 次清理调用，无 unsuccessful return；恢复 gate 已恢复 |

RED 的 `cleanup_unsuccessful_returns` 还记录了后续失败测试继承的失败 outcome；不能把这些零回调记录误称为十次独立清理异常。直接证实本次清理遗漏的是无 own outcome 的嵌套 fixture 那一条，以及未恢复的 gate 对象身份。GREEN 的全部清理返回成功。

证据：[验证驱动](traceability-cleanup-check.py)、[RED 日志](traceability-cleanup-red.log)、[RED 事实](traceability-cleanup-red.json)、[GREEN 日志](traceability-cleanup-green.log)、[GREEN 事实](traceability-cleanup-green.json)、[嵌套路径日志](traceability-cleanup-nested-green.log)、[嵌套路径事实](traceability-cleanup-nested-green.json)。驱动只观察 `unittest.TestCase.doCleanups` 的实际返回值并检查测试前后函数身份，没有改变 fixture 被测行为或生产 gate。

本记录只确认上述局部根因与修复。此前全套辅助测试的另外 21 个错误属于独立 budget fixture 问题，由另一审计任务处理。主任务负责对最终文件状态重新运行完整根测试与完整辅助 discovery；这 20 项局部验证不能代替全量回归，也不产生新的硬件验收结论。
