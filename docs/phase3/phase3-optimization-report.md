# Phase 3 真实优化实验（进行中）

2026-11-12。P3.2/P3.3 已在 Runtime 2 的 TPU v6e、MLA Decode contiguous BF16 范围内通过；本页记录随后真实优化，不把提案、静态检查或原 Store Best 晋升当成已验证性能收益。第一轮完成后该Runtime已释放；新v6e于11月12日22:54分配成功，申请循环随即退出，但11月13日10:04查询已无Runtime，第二轮未执行。最终闭环仍要求完成两轮并读取第一轮的新证据。

## 固定对照与范围

唯一数据库为 `.artifacts/phase3-2026-11-12/experiments.sqlite3`，工件仍由原 ArtifactStore 管理。本轮测量 Runtime 为 `tpu-v6e1-s-euw4a1-344ww12q09usn`，Context 为 `runtime-context:89820a7b421896bf463bb70f671ed7ce4c827086a4e37227ed7c1fc34d34d8b8`。A/B 拥有各自真实基线 Run，源码均为兼容修复 `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`。

基线同步主机计时 median 为 A 0.193195 ms、B 0.215365 ms；同字节的约11%差异提示跨次主机波动。默认 Profile program median 分别6.624375/6.615 us。这两类量具有不同计时边界，不能相互替代，不能把基线间差异写成优化。

正确性冻结为 small、boundary、default 三组；计时/Profile 仅 small。attention BF16 容差0.01/0.01、完整 cache 位等、输入 cache 不变。原 Oracle/runner/worker/Benchmark/parser 均保持固定。参见 [Baseline](phase3-baseline-report.md)、[Profiler](phase3-profiler-report.md)。

## 第一轮：四 Plan、五 Candidate

原 `prepare_round → Analyzer session → publish_result → intake → Controller` 执行。模型为当前对话，无独立 API。manifest SHA `5aff21e4175670eae1546c56e4897f12dfb3c289d129ab8db0cd8381453e8bec`，输入含全部3条策略卡片，参与A/B完整历史97/130条；方法和来源revision冻结。session先读取源码/完整历史/Profile/6方法，再运行8项数学计算，最后提交四Plan；校验review无降级项、winner_selected=false。

| Plan / Variant | 唯一变化 | 理论依据与待验证风险 |
|---|---|---|
| A tile / tile512、tile256 | 源码不变，只覆盖small block_size | tile数1→2→4，可能缩小工作集，也增加query读取、DMA和softmax次数 |
| A single / single-tile | capacity==block_size静态分支去掉旧零状态更新 | QK/PV算术量不变；编译器可能已折叠原表达式；须保留全无效行语义 |
| B store / delayed-store-wait | store.wait移到body return前 | 尝试同tile写回与计算重叠；无独立DMA实测，必须保证tile/信号量生命周期 |
| B compact / compact-new-row | prepared new由[B,T,Dpad]改[B,1,Dpad]，attention内broadcast | small逻辑payload从1,310,720降到1,280bytes；不能认定为物理VMEM节省，窄布局可能编译失败 |

源程序的masked KV本来同时用于QK/PV，没有将既有复用包装成新优化。一个tile的small不具备跨tile double buffering空间；pipeline计算明确使用假设时间，结果不是DMA测量。

候选位于主项目独立目录；三个源码只改 `build_mla_decode_contiguous`。原执行source bundle包含375文件（权威源码审计清单390文件是不同范围），其余依赖保持。第一次打包辅助脚本误将390当执行包数量，断言在发布前拒绝；据原包实际数量修正为375后打包通过，没有更改包内容或契约。正式intake validator五项通过。

第一轮会话、具体修改及工件见 [result.json](../../.artifacts/phase3-2026-11-12/real-round-1/result.json)、[candidate-artifacts.json](../../.artifacts/phase3-2026-11-12/real-round-1/candidate-artifacts.json)、[variants.json](../../.artifacts/phase3-2026-11-12/real-round-1/variants.json)。数学结果是model_predicted，没有输入假定硬件峰值。三个变更源码的静态AST/语法及独立diff检查不代表真实正确性。

Controller已于本地16:34启动，按原公平调度和单并发逐个运行。当前证据以 [controller-status.json](../../.artifacts/phase3-2026-11-12/real-round-1/controller-status.json) 和原Store为准，完整结束后再归档结果。开始前只读观察见 [runtime2-idle-before-round1.json](../../.artifacts/phase3-2026-11-12/runtime2-idle-before-round1.json)；该时点可见进程无设备占用，不保证物理全局绝对独占。

## 已返回的第一轮结果

`delayed-store-wait` 已完成五阶段并由原 Evaluator/consider 归档，Run `wf-r:9693f8c2c46382039b4386db7c6ddbe1`。三组 correctness 通过，原始 NPZ 已独立重算 attention 数值及全部 cache 原始位；回执、完整 Evidence 闭包、实际加载身份及20样本统计复核通过。

| 指标 | 该次候选测量 | B父基线 |
|---|---:|---:|
| 同步主机 median | 0.183795 ms | 0.215365 ms |
| 设备 program median | 6.190 us | 6.615 us |
| prepare median | 0.695625 us | 0.693750 us |
| attention median | 4.616875 us | 5.070000 us |

原分类为 `correct_faster`、confirmed=true。这是单次Run低于父的结果；没有把它扩称可重复收益或证明全部差值来自DMA重叠。读回核验见 [版本化核验报告](../../.artifacts/phase3-2026-11-12/real-round-1/local-verifications/20261112T084653210802Z-r17.json)。

随后 tile512 的 generate 在桥接中断后被Controller保留为blocked。原operation `operation:ec298b55466a600e177cb939cb69a7de32f576bcf7f3d53269ee38fb5a8cde94` 的独立查询返回completed/succeeded，request digest一致；继续原队列仅回收原回执，不新建Run或重新执行该操作。查询记录见 [blocked-lookup-1.txt](../../.artifacts/phase3-2026-11-12/real-round-1/blocked-lookup-1.txt)。这次中断是恢复案例，不是候选计算失败。

用户随后要求暂时交还TPU，本地循环停止且策略持久暂停；查询确认tile512 compile已完成、运行锁释放、可见设备无占用。用户明确要求继续后重新只读检查空闲，恢复原journal和operation。完整检查见 [暂停记录](../../.artifacts/phase3-2026-11-12/user-pause-20261112.json)、[交还观察](../../.artifacts/phase3-2026-11-12/runtime2-idle-user-handoff.json)、[恢复观察](../../.artifacts/phase3-2026-11-12/runtime2-idle-user-resume.json)。没有终止用户进程、重建Runtime或重执行已完成compile。

随后完成的两个候选：

| 候选 | 原分类 | Host median | Program / prepare / attention median | 核验 |
|---|---|---:|---|---|
| tile512 | correct_slower | 0.225485 ms | 8.090 / 0.691875 / 6.541250 us | [三case原NPZ及完整证据](../../.artifacts/phase3-2026-11-12/real-round-1/local-verifications/20261112T091839031473Z-r34.json) |
| compact-new-row | correct_slower | 0.221875 ms | 6.588750 / 0.650000 / 5.090000 us | [三case原NPZ及完整证据](../../.artifacts/phase3-2026-11-12/real-round-1/local-verifications/20261112T093258885137Z-r56.json) |

tile512的循环代价在本次设备测量中超过可能的工作集收益；compact的program差异很小，且主机计时稍慢，没有明确收益。两者结果、非晋升Decision和Frontier均由原Controller/Store处理，未删除退化尝试。

## 第一轮完成与 Runtime 更换

2026-11-12 22:01，原 Store 中五个候选均已完成，正确性全部通过。最终本地复核覆盖原始 NPZ、完整 cache 位、加载身份、回执与证据闭包、计时统计及已解析 Profile；未重新解析原始 protobuf。见 [最终五项核验](../../.artifacts/phase3-2026-11-12/real-round-1/local-verifications/20261112T140159968103Z-r78.json) 和 [完成快照](../../.artifacts/phase3-2026-11-12/real-round-1/completed.json)。

| 候选 | 原分类 | 同步主机 median ms | Profile program median us |
|---|---|---:|---:|
| tile512 | correct_slower | 0.225485 | 8.090000 |
| tile256 | correct_slower | 0.202570 | 11.127500 |
| delayed-store-wait | correct_faster | 0.183795 | 6.190000 |
| compact-new-row | correct_slower | 0.221875 | 6.588750 |
| single-tile | correct_slower | 0.193550 | 6.580625 |

A 的 Branch Best 保持修复基线；B 与该 Context 的 Task Best 为 delayed-store-wait。这是原晋升规则下的单次测量结果，尚无跨独立 Run 的重复性确认。小 tile 的设备时长明显回退；single-tile 和 compact 的 program 时长没有明确收益。第二轮应据此调整提案，不能重复第一轮假设并声称已利用新证据。

随后桥接查询确认原 Runtime 已不在账号 assignments 中。五个实验均已有本地完成回执，不存在需要盲目重跑的未知实验。原 Runtime/Context 与全部结果永久保留；随后于11月12日22:54申请到新v6e，循环退出；11月13日10:04再次查询列表为空，尚无新环境准入或第二轮结果。第二轮必须先取得新环境指纹，在新 Context 重测 A/B 当前父版本并重新验证已知错误负例；不跨 Runtime 直接计算加速比。

原 Controller 探索预算已使用5次、剩余11次；不会因换 Runtime 重置。环境准入的独立预算记录与探索预算分开报告。第二轮、重复性确认及 single-tile 零长度补充验证尚未执行。

## 尚未关闭的验收事项

- 新 Runtime 的真实环境、父版本及 Oracle 重新准入。
- 基于新快照/完整历史改变第二轮计划，完成第二轮真实实验。
- 若候选出现较低计时，以原协议追加受控复测后再判断可重复收益；不得重跑成功operation或直接修改Best。
- single-tile分支的零长度尚未被当前boundary case直接覆盖（boundary为多tile）；应追加独立验证，不改已冻结的正式workload。
- 第二轮完成前不申请P3.5验收，尚无经独立Run复测确认的优化收益。
