# Round 1 调用链、历史和方向核对

2026-11-12。**Source audit 与已固定实验历史读取；不是正式 PlanSet，不生成 Candidate，不是新实测或收益结论。** 本次只读当前会话 packet / session、完整源码、A/B完整历史及既有 runner/registry。后续正式提案由根会话通过原 Analyzer 生成；Candidate diff 尚待产生后逐项检查。

## 固定输入与调用链

会话 `real-round-1/session.json` 固定 manifest `5aff21e4175670eae1546c56e4897f12dfb3c289d129ab8db0cd8381453e8bec` / 7,947,365 bytes。首响应12个工具调用包含A/B各自完整source、history、profile和6个方法。

A/B source tool结果已逐字节比较，完全相同，各113,009 bytes，SHA256 `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`。config均为`{"case_overrides":{},"kernel":"mla_decode","schema_version":1}`。

实际调用链为：隔离 `mla_runner.run_mla_stage` → 冻结registry的 `get_kernel('mla_decode', cfg)` → `MlaDecodeKernel.__call__` contiguous分支 → `native_function` → 当前模块的 `build_mla_decode_contiguous` → 内部native → prepare / attention两个Pallas custom-call。修改dense/paged函数不会改变这条调用链。runner要求fresh process，核对bundle、entry、运行环境；Oracle在独立冻结工作区及CPU子进程执行。

全文件AST核对的导入期动作包括库import、`TRANS_B_DIM_NUMBERS`常量、`threading.RLock()` / `ContextVar`创建、jit/lru_cache/contextmanager装饰器包装、`__all__`；没有在模块顶层调用dense/paged Kernel或CPU monkeypatch。`_ModuleProxy` / `_CpuDma`类仅定义方法。CPU interpret对JAX discharge的临时替换在`_mla_decode_cpu_interpret_context()`实际进入时才发生；当前contiguous且interpret=false不调用该函数。

contiguous引用的外部名字仅为`TRANS_B_DIM_NUMBERS`、JAX/Pallas模块、标准builtins、`functools`和`warn_if_interpret`。它不调用文件前部dense/paged helper，后者也未改写这些全局名字。此结论是静态调用链核对，不能扩张为任意Candidate导入代码均无副作用的保证。

## 完整历史与精确父

| 路线 | 完整历史 | 当前精确父 | 当前真实基线 |
|---|---|---|---|
| A `mla-strategy:deee0989…` | 97条：87 owned、10 dependencies | `wf-parent:a37a1b7abd28b0681f9fd736f13eb97e37f15612` → `baseline-repair-candidate:f3a6d055…` | Run `runtime-run:4686bd6e…`，median 0.19319499995162914 ms |
| B `mla-strategy:ad341685…` | 130条：34 owned、96 dependencies | `wf-parent:2ceae14208b284845423989d29e280295b3cdd5e` → `strategy-seed-candidate:c642e60a…` | Run `strategy-seed-run:c339eda9…`，median 0.21536499980356894 ms |

两份history均`complete=true`，offset0 / next_offset null，固定snapshot `snapshot:2cae3f03945f43678028757740c89558`。当前Context均为`runtime-context:89820a7b421896bf463bb70f671ed7ce4c827086a4e37227ed7c1fc34d34d8b8`，各自Run、EvaluationBundle和ProfileBundle独立。prepared卡片包括A、B和Oracle验证策略，后者不参与优化。

A历史保留原始`e53b0e5c…`Candidate的compile_failed、失败Run/receipt、整批pad后取行的repair意图/新Candidate、旧Runtime成功，以及新Runtime readmission和独立新Run。旧context `mla-context:c86eb2d3…`没有被改标为runtime2。B的父系依赖引用A修复Candidate，但B建立了自己Plan/Candidate/Run与基线测量；同源码并不意味着复用A的测量或父ID。当前显式parent selection与这两条各自branch best一致。二者时差是同源码两次测量，不能解释为代码优化收益。

## 四方向合法性与风险核对

| 待判断方向 | 源码上可表达的范围 | 必须保留的条件与未证事项 |
|---|---|---|
| A：small tile512 / tile256 | 原config支持`case_overrides.decode-small.tiling.block_size`；N1024可整除512/256 | boundary N256保持T128、default保持T1024，不能全局改512。改变tile会增加循环/online归约/DMA次数；没有spill或收益保证。compile/correctness仍覆盖三case。 |
| A：first-tile / single-tile状态专门化 | 可只对静态`capacity == block_size`走单块state路径；若剥离所有场景首块，后续循环从1开始 | 首块cache仅load/append/store一次；保留全false valid时p/total/acc为0与分母保护。保留P→BF16、dot FP32累加、NaN masking。不用Python `if i == 0`判断JAX tracer。compiler可能已优化原零state，显式专门化不预证收益。 |
| B：延迟同tile store.wait | 将原store.wait移至当前body计算完成、return之前；load.start/load.wait和store.start保持 | 同一个semaphore先完成load再发store，每轮返回及下一轮tile复用前无条件wait。期间不得再写tile；完整cache存raw append后值，不能存masked值。SSA独立性支持源码依赖分析，不证明物理重叠或DMA瓶颈。 |
| B：compact new row，length布局暂留 | prepare只把new输出改为`[B,1,Dpad]`，对应attention in_spec改为`(1,1,Dpad)`；attention中广播row进行append选择 | lengths仍`[B,H,T]`，q路径不变。必须保留整批new pad后再取batch行，避免复发B3 concatenate offset错误。窄VMEM布局、切片/广播lowering是否可编译需真实验证。保留动态length、全cache输出、NaN tail/padding。 |

所有方向均必须保持原输入/输出语义、固定Oracle与容差、未捐赠/未改写input cache、完整output cache按位一致，以及prepare/attention/必要工作在原public-call计时范围内。源码变更只能发生在独立Candidate，不能更改trusted registry、runner、parser、benchmark或Reference。

对于compact方向，已归档small HLO的prepare `used_scoped_memory_configs.size=24064`与源码new广播逻辑1,310,720 bytes具有不同口径。不能把差值称为可省物理VMEM/流量，也不能据字段较小断言广播已消除。具体证据及引用限制见[analyzer-code-context.md](analyzer-code-context.md)的runtime2 HLO段。

本次未修改源码、配置、session响应、Store或远端；未替当前会话选定最终Plan。下一步可对实际生成的diff逐项核对上述边界。
