# 第一轮 Candidate 独立静态审查

日期：2026-11-12（Asia/Shanghai）。结论：未发现阻塞真实验证的 Critical / Important 源码或打包缺陷。四个 Plan、五个 variant 与已保存 Analyzer PlanSet 对应；三份改动源码都只改变 `build_mla_decode_contiguous`，两个 tiling variant 保持源字节不变。这个结论允许进入原 Controller 的编译和固定 Oracle 验证，**不等于真实 correctness、DMA 重叠或性能收益已通过**。

本 reviewer 没有导入候选模块、调用 JAX/TPU、访问远端、打开 SQLite 或修改 Store。实际只读核对源码、diff、metadata、打包 JSON 和 ArtifactStore bytes；另外请第二位审查者只读检查了 delayed-store-wait 的 DMA 生命周期。审查结果绑定 [round1-candidate-static-verification.json](round1-candidate-static-verification.json)，可复核脚本为 [verify_round1_candidates_locally.py](verify_round1_candidates_locally.py)。

## Plan 与实际工件

基线为 `baseline-repair-1/mla_decode.py`，SHA256 `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`，113,009 bytes。

| Plan | Variant | 实际变化 | Source SHA256 |
|---|---|---|---|
| R1-A-tile | tile512 | 只覆盖 decode-small `tiling.block_size=512` | `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae` |
| R1-A-tile | tile256 | 只覆盖 decode-small `tiling.block_size=256` | 同上 |
| R1-A-single | single-tile | 静态单 tile 分支去掉零旧状态的 online recurrence | `325e0e9098633d625bcac832f9cb043935c7ce205df64a13b53fc84e22133e23` |
| R1-B-store | delayed-store-wait | 仅移动一次 `store.wait()` | `0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34` |
| R1-B-compact | compact-new-row | prepared new-KV 改为 `[B,1,Dpad]`，attention 内广播 | `c9e270a86764555f38cbbfe658857d9c48bf9d0e8163a1b7d4edc41959236c0d` |

`result.json` 四个 proposal ID、variant 标签顺序和 parent ID 与 `variants.json`、`candidate-artifacts.json`、每个目录的 `packaged.json` 一致。后三项 config 仍是空 `case_overrides`；前两项没有重复源码文件，只引用相同基线源、bundle 与 build。tile512/256 metadata 的 requested_config 与正式打包 config 精确相同，boundary 的 block128、default 的 block1024 保持不变。

## 变更边界与冻结身份

三份源码均通过 Python AST 解析及仅生成 Python code object 的 `compile(..., 'exec')` 检查，没有执行 code object。顶层 AST 唯一变化为 contiguous builder；其前后原始 bytes（包括原 CRLF）一致，所有其他函数、Oracle、decorator 和导入保持不变。每个 `change.diff` 均从实际父/候选 bytes 独立重建，并与本地文件、metadata SHA 和 ArtifactStore diff_ref 精确一致。

本次实际 execution bundle 是 **375 个文件**。390 是更早的 source-audit inventory 数量，不能当作本次 bundle 文件数。独立脚本验证基线包全部 375 个文件的 bytes SHA；候选包的文件集合、role、entry 和其他 bundle 字段保持一致。三个源码候选只有 `pallas_kernels/kernels/mla_decode.py` 的 artifact_ref 变化，其他 374 个条目及对应原 bytes 未变；tiling variant 整包不变。

只读审查 `package_round1_candidates.py`：它复制原 bundle，只替换 entry artifact_ref；复制原 build，只更新 `source_bundle_digest`；只对 tile512/256 设置 small 的 block_size。独立核对实际产物确认 `oracle_bundle_digest`、`oracle_digest`、`runner_digest` 三个固定摘要以及 schema_version 均与 baseline build 相同。固定 Oracle bundle 为 `5d15d9724a1dfe21a10e5f3cf770203521935189ddac4b53dc6f0b3d6275ff74`，源 SHA 为 `e53b0e5ccff3ecf18192f30ef179cdb207c177f2f2dac48b2a08f40acc5000a6`。未修改容差、workload shape、lengths、mask/window/cap 参数或输出契约。

审查版本：package 脚本 SHA `653ad5c6cc9a7f549c7dcb2e868c10b0a8058795bfde68be9aabac798f930356`；Analyzer `real-round-1/result.json` SHA `eb6bd22efc91995a2080fcd106079d92369041d04900b813c94e5716466b0fcb`；`variants.json` SHA `97a75ed0ff8a10f35d4de93e23e15a6e89e8f1d5a6a72119124aac2bc0342f70`。

## single-tile

实际候选 `mla_decode.py:2978` 判断 `capacity == block_size`。二者由静态 shape/config 得到，没有按 traced 循环 index 做 Python 分支。父 builder 已要求容量为 block_size 的正整数倍；该分支被选中时 `fori_loop` 只有一次迭代。新增 max/sum/PV 不再计算 `m`、`total`、`acc` 的初始零状态更新。BF16 的 P cast、FP32 累加、valid mask、soft_cap、DMA/cache 操作与末尾安全归一化保留。`:2985` 的多 tile else 内容与原 recurrence AST 精确一致。

静态边界推理：全无效行的 scores 被替换为有限 float32 最小值，P 经 `where(valid, ..., 0)` 为零；计算用 KV 在 matmul 前被清零，因此 total/acc 为零，`:3006` 的分母选择 1，attention 为零。append mask 和全容量块输出不变，非正长度不追加 cache。此推理不是 TPU lowering 的验证。

**尚未覆盖的分支组合**：固定 small 输入的 lengths 由原 registry 生成全容量 1024；显式零长度仅在 boundary `[0,1,129]`，该 case 容量256/block128，仍走原多 tile else。因此固定三 case 通过后也不能声称新单 tile 分支已经实测过零长度或全无效输入。建议后续另建诊断 case 验证 single-tile+zero/NaN tail；不应为补测而改变已冻结正式 workload 或把额外诊断混入正式计时。该覆盖限制已向主 agent 报告并被接纳。

## delayed-store-wait

候选 `:2952` 的 `tile[...] = kv` 是 body 中唯一 tile 写入，先于 `:2956 store.start()`。`load.start()/wait()` 在 `:2944–2945` 先完成。移位后的 `:2984 store.wait()` 无条件执行并紧邻 body return；下一次循环开始前已等待 store 完成，原 tile 与共享 DMA semaphore 不会从源码控制流上提前复用。

start 与 wait 之间的语句只有 mask、数值 `kv` 重绑定、dot 和 online recurrence，没有 Ref 赋值或第二个 DMA。尤其 `:2967 kv = jnp.where(...)` 是计算值重绑定，没有写回 tile，因此原始 NaN 尾/padding 仍经 store 输出，mask 只阻止它们进入 attention 运算。第二位审查者独立确认该源码整文件仅是同一 `store.wait()` 的移动，其他字节不变。

静态未见 DMA 源修改冲突或遗漏 wait，但不能证明编译器是否重用物理 buffer、串行化、接受此调度，或产生实际重叠和收益。保持真实编译与固定三 case 检查，之后根据独立 Profile 报告；不能用该源码变化自行推导 DMA 时长。

## compact-new-row

实际修改精确为四处：`:2913` 把 `np_ref[b]` 写为一行；`:2923` prepared shape 改为 `[B,1,Dpad]`；`:2933–2934` 在 attention 读入该行并广播到 `[T,Dpad]`；`:3010` 对应 new-KV BlockSpec 改为 `[1,1,Dpad]`。独立脚本用这四处替换完整重建候选 bytes，结果与工件相同。

`:2904–2912` 的整 batch concatenate/pad 仍发生在按行 slice 前，保留修复 B3 offset mismatch 的关键顺序。query、lengths 的布局和全部 attention body、cache append/mask/DMA 计算不变；prepared new 行在 attention 内广播后的逻辑值与原先提前广播一致。需要真实确认的风险是 `[B,1,Dpad]`／`[1,1,Dpad]` 的 TPU VMEM sublane、对齐和布局转换是否合法，编译器可能拒绝或引入成本。仅凭逻辑 shape 不能声明物理 VMEM 节省。

## 共同 cache 和数值风险

三个源码候选均保留 `capacity // block_size` 全容量循环、独立完整 `updated_ref` 输出以及原 attention 输出形状；未新增 input/output alias、donation、对 cache_ref 的写入或跳过无效尾块。原 append 条件只替换对应 token 的有效 feature，原始 cache tail/padding 字节先存入输出，计算用 KV 后置清零。零长度、B3 padding、opaque NaN 尾、input cache 未修改的真实约束仍应由原 runner 验证。

tile512/256 在 small 中把 online-softmax 更新从1次增加到2次/4次，仍是同一数学目标，但浮点舍入、循环/DMA 开销和编译器布局可能改变。所有候选都仍需原三组 Oracle 验证；benchmark 仅 small，不能从 default correctness 推出 default 性能。相同源码的新 Runtime A/B baseline 已显示 host timing 差异，因此单次 lower median 或 Best promotion 也不能独立建立可重复的优化收益。

## 验证记录

最终命令：

```powershell
.\knowledge-site\.venv-mcp\Scripts\python.exe -B -X utf8 knowledge-site/manifests/phase3-2026-11-12/verify_round1_candidates_locally.py
```

最终 exit 0，工具报告进程耗时1.340s，四 Plan／五 variant 全部静态身份检查通过。此前两次脚本运行分别因 reviewer 自身硬编码 `ast.dump` 文本与当前 Python 表示不一致、四处重建漏保留原 CRLF 而失败；改为结构/语义节点判断和保留实测 CRLF 后通过。过程中没有修改候选源、diff、metadata、packaged bytes 或正式 variants，失败未被记作通过，也不是候选编译失败。
