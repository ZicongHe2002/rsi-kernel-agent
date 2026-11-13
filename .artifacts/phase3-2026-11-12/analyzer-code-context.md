# MLA Decode：供后续 Analyzer 使用的源码上下文

2026-11-12。此文件是 Source audit、已归档 Real correctness / Real benchmark / Real profile 的读取结果，以及明确标记的数学假设。**不是正式 PlanSet，不生成 Candidate，不申请晋升。** 开头的计时/Profile段保留首次 Runtime 的历史证据；新增 runtime2 HLO 段只描述其对应新 Run 的编译器元数据。当前 Gate 结论以主执行流程最新验收为准，旧 Runtime 数值不能直接作为新 Runtime 的可晋升对照。

## 固定输入与实际证据

当前分析源码为 `candidates/baseline-repair-1/mla_decode.py`，SHA256 `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae`，113009 bytes。它只在原始权威源码 `e53b0e5c…` 的 contiguous prepare 中，把 new-KV 整批补零后再取行，修复真实 B3 编译错误。它不是已经证明更快的优化版本。冻结 Oracle 仍来自原 `e53b0e5c…` 包。

读取的证据汇总是 `baseline-evidence-summary.json`，Run `baseline-repair-run:22398773cfc39fbfd571714a9578835e2add586ab10b873cf017fa9ee750a8f9`。其 correctness 的 small、boundary、default 三例都通过；完整 cache 按字节相等，boundary 的 cache 中允许的 NaN 不构成 attention 非有限值。结果分类 `correct_inconclusive`：失败父版本没有可比较 timing，因此没有相对父版本性能提升结论。

| 证据 | 实际值 | 可支持的结论 |
|---|---:|---|
| host synchronized public-call benchmark | median 0.2207150005233416 ms，20 样本，5 warmup；P25/P75 0.21346500011532044 / 0.2250975001061306 ms | small workload 的完整 public-call 协议计时，含 prepare、attention 和完整 cache 输出；不是纯设备时间 |
| XPlane program | median 6.60375 µs，20 样本 | 同一逻辑 Run 的独立 Profile operation 中，目标 program 的 device-duration |
| prepare custom-call | median 0.726875 µs，20 样本 | prepare 的 trace-derived device-duration |
| attention custom-call | median 5.03875 µs，20 样本 | attention 的 trace-derived device-duration |

Profile 原始 XPlane digest `88fef4fdab3a900adf7d3d1a72f97105c5a6a1c9d563b02445d3b8cd676ee45d`；原 parser JSON digest `90d0cb64a2116c735c37499e9e7b8e05a831f1732da2833bfda80c106abc9001`；parser source `eb718d742010fbeccf9c45eb8eaf35300d2d23814be7222a7499cb739b9bf561`。profile / benchmark operation 不同。不得将上述中位数相减后称为实测 host overhead、DMA、等待或编译器开销。program 包含 custom-call，不能三项相加。现有解析器没有独立 DMA、wait、MXU 利用率或实测 VMEM 计数。

原 Runtime 为 `tpu-v6e1-s-euw4a0-xyzyx7kofigt`：Python3.11.17、JAX/JAXlib0.8.1、libtpu0.0.30、TF2.20.0、XProf2.22.1、单 TPU v6 lite。文档生成时主执行流程报告该 Runtime 已丢失，后续 Runtime 连接和基线重建由主执行流程负责。

## runtime2 已保存 HLO：编译器字段与其边界

本次新增核对来自 [baseline-runtime2-hlo-metadata.json](baseline-runtime2-hlo-metadata.json) 及其三份原始 HLO，而不是上段旧 Runtime 的 Profile。对应 Runtime `tpu-v6e1-s-euw4a1-344ww12q09usn`，Run `runtime-run:4686bd6eb923d2ce96be095360d34f0c3f533a30c4cf64c8e0bf70c3d275f223`，Context `runtime-context:89820a7b421896bf463bb70f671ed7ce4c827086a4e37227ed7c1fc34d34d8b8`。源码仍为修复后的 `2fc23aa9…`；config 为 `078d6739…`，build 为 `f224e0ed…`。runner 在 compile 阶段使用 `compiled.as_text()` 归档输出；没有执行新编译或新硬件测量。

| 固定 case | prepare `used_scoped_memory_configs.size` | attention 同字段 | 原始 HLO SHA256 / bytes |
|---|---:|---:|---|
| decode-small | `24064` | `3944448` | `59d500c778bb97c81a8b574aa215e88c462addafbf4dfc1cab2b8eed70d5793e` / 20169 |
| decode-boundary | `26624` | `212992` | `baeb876c85a39ee41a02253a2d40cb74fa30df8b5296737dd2aa553841d72c13` / 21274 |
| decode-default | `175104` | `6180864` | `223a142674f390e0c6abdad93cfb1292440ee2e47d7c00b58c7af66df351d508` / 23487 |

这里逐字保留 backend_config 中 `size` 字段的字符串数值。全部条目的 `memory_space="1"`、`offset="0"`；声明的 `scoped_memory_configs.size="120795955"`，`retry_config.retry_count="0"`。这些是编译产物中的空间/资源配置报告，分类为 **compiler-estimated metadata**；不是采样得到的活跃 VMEM、物理带宽、spill 次数或设备内存峰值。`retry_count=0` 也不能解释为“零 spill”或“没有编译资源压力”。当前核验没有解码 serialized custom-call body 为 LLO，也没有确认该字段是否覆盖参数/输出缓冲区、不同作用域或全部活跃临时量，因而不能与逻辑张量总量直接作差、作比或相加后报告占用。

small 的 HLO prepare 签名仍明确含 `bf16[1,16,640]`、`bf16[1,1024,640]`、`s32[1,16,1024]` 三个输出及 `S(1)` 标记。后两者对应源码的 new-KV 和 lengths 广播；attention 将它们作为独立 operand 接收。其逻辑形状算术仍分别是 1,310,720 bytes 与 65,536 bytes，加 q 的 20,480 bytes 合计 1,396,736 bytes。**逻辑 new-KV 约 1.31 MB 与 compiler scoped 字段 24,064 的差异，说明两者的计数口径不能等同。** 它既不证明“广播已经完全消除”，也不证明“prepare 实际占用/传输 1.31 MB”。HLO保留形状不自动证明物理物化；scoped字段较小不自动证明所有输出都使用紧凑表示。

这使 compact prepare 的依据更保守：可以把它描述为改变跨 custom-call 的显式布局/接口表达并验证 compiler 行为的研究假说，不能以逻辑广播尺寸直接宣称已测得内存瓶颈、预测可节省1.31 MB物理流量或承诺收益。attention字段较大同样不是spill证据。原正式Benchmark/Profile仍须独立比较，编译器字段只能辅助解释。

可复核链：元数据 report `fd5fd4a45c77afbfb7e6eb204a4afc7ec4d7de1a41fd6ebe2b278f2130219b86` / 4279 bytes → compile receipt `660a75377556300df6ab171825e832999e13df2366868d56676ddf25ec447d9e` / 53452 bytes → 上表三份 HLO。此轮以 SQLite `mode=ro`核对数据库 receipt marker、原有 compile Evidence 和 EvaluationBundle；五个工件长度/SHA均通过，结果见 [hlo-context-check.json](hlo-context-check.json)。元数据报告是原始HLO的派生本地审计工件，当前未另挂到实验 Evidence；没有新增 Profile、Run 或数据库记录。

### 在原 Analyzer 中引用这些证据

原 compile Evidence 已存在：`stage-evidence:dc8c407acf89de8b45b5fb735cff98ec9f6e44bdd728b3bc5276fad7e19a1d08`，kind为`code_fact`，其 artifacts 已含 compile receipt 和三份 HLO；原 `evaluation:3bf65cc359132ac5af087bc4f7147a524add9b09551d8d525a41110db8608abb` 已引用它。因此选此精确 baseline Run 的新 manifest 可以通过 `evidence:<上述ID>` 和 `history:<本策略ID>`保存编译证据来源，capture会核验这些原始工件。无需修改2C，也无需把编译器数据塞入实测Profile。

但当前 Evidence.value.outputs 只有 `compile_time_ms/environment/executed/identity`，不包含上表 `used_scoped_memory_configs` 数值。`history`返回完整记录及工件引用，`source`只返回固定源码/config；现有工具没有通用 raw HLO 读取入口。因而当前 manifest 引用可以证明“该Run保存了这个HLO”及其摘要，不能以一个并不存在的 JSON path 让exact predicate自动证明`24064`。HLO已绑定也不代表其全文已进入模型packet。

安全的两种表达方式：

1. **保持现有记录。** 当前会话明确说明已在宿主读取并核对原HLO，引用本策略 compile Evidence / history 与 raw digest；数值解释保持 compiler-metadata 限定及显式不确定性，不把它写为可由当前predicate验证的实测观察。若提交`compiler_estimate`而predicate无法验证，原review会降级为hypothesis，应保留该降级。`source:<策略>`只支持源码广播形状等事实，不能单独证明编译器数值。
2. **若希望packet直接包含数值，在新round快照之前由宿主追加一条有来源的Run-target `Decision(kind="note")`。** note可记录`type="compiler_metadata_audit.v1"`、精确Run/Candidate/Context、source/config/build、compile receipt、三份HLO ref、派生report ref、逐case原字段及上述限制。它进入该策略完整history后，可由`history:<策略>`的实际record index/path引用数值；原review仍将其分类为`historical_claim`，解释文本须保留“编译器元数据，非物理计数”。这是对已验证工件的审计摘要，不是新增Profile或实测Evidence。创建前须重验原工件；history对note中任意artifact_ref并不自动递归验真。此处只提出兼容引用方式，**未追加该Decision**。

已冻结的manifest不能就地补充新note，需新snapshot/manifest。旧 `baseline-analysis-inspection` 会话固定在首次 Runtime 的输入，不含这份runtime2编译证据，不能借其history/source引用假装是当前Run。策略B即使使用相同源码，也应引用自己Run的compile证据；A的HLO不能重标为B当前编译结果。

## 调用链与源码事实

以下行号都针对上述修复文件，不针对历史 dense/paged 实现。文件前 2857 行虽然包含其他路径，却不是当前入口；不能把那里的 pipeline 参数当作 contiguous 已支持的开关。

| 位置 | 已读源码事实 | 不应扩大的推论 |
|---|---|---|
| 外部 `pallas_kernels/kernels/registry/mla_decode.py:23`、`:150` | public runner 进入 `native_function`；contiguous 选择 `build_mla_decode_contiguous`，从 config 读 block_size、vmem_limit_bytes | 修改另一个同名 dense 函数不会改变当前调用链 |
| `mla_decode.py:2858`、`:2875`、`:2881` | builder 参数、合法性检查、内部 `native`；BF16、H 是16倍数、L是128倍数、block是128倍数、capacity整除block | 不是任意形状/精度都可直接代入 |
| `:2902` | prepare 全 batch pad new-KV；q 逐 batch pad；new-KV广播到 `[B,T,Dpad]`；lengths广播到 `[B,H,T]` | 逻辑广播大小不等于真实物理分配或实测流量 |
| `:2917`、`:2921` | prepare 的三个输出形状是 `[B,H,Dpad]` BF16、`[B,T,Dpad]` BF16、`[B,H,T]` int32，均VMEM | 这些尺寸可以作逻辑模型，不能声称 compiler 一定全部物化 |
| `:2931`、`:2940` | attention grid按batch；每个循环tile执行 load.start→load.wait，更新append值，store.start→store.wait，再做masked score及online softmax | async API名称本身不证明 DMA/compute 已重叠；独立 DMA 时间未知 |
| `:2948`、`:2951`、`:2967` | append只覆盖有效特征列；cache其余字节原样输出；compute前把无效tokens和padding置0，防止NaN传播 | 不能删除完整cache输出、忽略未使用tail或只写append一行来冒充同契约加速 |
| `:2969`、`:2982` | QK与PV都请求FP32累加；softmax的P再cast至BF16后PV；同一masked `kv` 被QK与PV使用 | 不宜把“改为共享KV”作为已经实现之外的新机制；改精度需完整边界验证 |
| `:2988` | 循环次数固定 `capacity // block_size`，遍历所有cache tile，初始m=minfloat、total=0、acc=0 | 即使length=0也必须完成完整cache复制；可以研究跳过无效attention计算，但不能跳过输出保留 |
| `:3004`、`:3008`、`:3018` | attention in_specs 与prepare输出匹配；一个BF16 `[T,Dpad]` scratch tile、一个DMA semaphore；VMEM limit是编译参数 | 没有双buffer开关；增加buffer必须改源码，不是传不存在的配置字段 |

当前固定矩阵：small B1/H16/N1024/L512/R64/T1024；boundary B3/H16/N256/L128/R64/T128、lengths0/1/129、opaque NaN tail/padding；default B8/H16/N16384/L512/R64/T1024。D=L+R，Dpad=L+ceil(R/128)×128。当前正式 timing/profile 只针对 small；另外两例是 correctness 覆盖，不是对应性能结果。

## 变体可以怎样表达

现有 `workflow/mla_runner.py:201` 的 config schema 只允许：

```json
{"schema_version":1,"kernel":"mla_decode","case_overrides":{"decode-small":{"tiling":{"block_size":512}}}}
```

这是语法示例，不是已选择的实验。case_overrides可以分别指定已有case，字段仅 `tiling.block_size` 和 `params.vmem_limit_bytes`，都为正整数，block须128对齐。实际native还要求N整除block。small合法tile集合128/256/512/1024；boundary仅128/256；default为128的、能整除16384的倍数，即128/256/512/1024/2048/4096/8192/16384，是否能编译和满足资源仍需验证。不能全局设置512而保留N256边界用例不变，否则构造本身非法；应使用逐case配置并明确区别。

纯配置试验可复用相同source digest，但必须有新config/build身份和Candidate。prepare布局、DMA流水、attention递推必须生成独立新source bundle和diff。现有context把所有非入口文件列为trusted_files，因此本轮不改registry、helper、benchmark或配置YAML；入口文件修改也不能改变冻结Oracle。`vmem_limit_bytes=120795955`是编译许可声明，不是可从设备推断出的实测容量/占用。降低该数值不等于减少live memory。

## 四个可隔离研究方向，尚未形成正式 Plan

### 1. 只改变block_size，保持源码与数值机制

**具体范围：** 使用上述case_overrides；源码生效位置`:2862`、`:2893`、`:2913`–`:2924`、`:2935`–`:2942`、`:2988`–`:2990`、`:3009`–`:3019`。small可以单独比较T512/T256与原T1024；boundary保持T128、default保持T1024。这样无需一次改动整个矩阵或掩盖不合法tile。

**源码依据：** small在T1024时只执行1轮，T512时2轮、T256时4轮。小tile降低每轮score与new-KV广播的逻辑尺寸，并增加循环/softmax归约和DMA启动次数。完整cache总读写量不会因此消失。

**假说：** 较小live state或不同编译映射可能补偿额外循环；也可能更慢。当前Profile没有spill信息，不可声称已发现spill。历史 `hist-decode-dma2` 的K2048/K4096结果来自不同native/dense边界，不能直接预测当前T选择。

**合法性与验证：** 逐case保持shape、scale、cache语义、dtype和tolerance；重新跑全矩阵。不能将T改变带来的Profile变动直接归因于某一种资源，需原始program样本与同协议benchmark。

### 2. compact prepare：保留pad，推迟new-KV/length广播

**具体范围：** `prepare` 的`:2902`–`:2928`只输出pad后的new row，例如`[B,1,Dpad]`，长度保留最小可编译形状；同步修改attention读取`:2932`–`:2938`及in_specs`:3008`–`:3010`。在attention内按需要广播new row和length，再复用到所有tile。q padding和当前“整批pad再取行”规则保持。

**源码依据：** 目前new是单token却在prepare逻辑上复制到`[B,T,Dpad]`；length也是单值却扩为`[B,H,T]`。small的三份prepare输出逻辑总量为1,396,736 bytes，其中new广播1,310,720 bytes、length广播65,536 bytes、q20,480 bytes。这里是shape算术，不是实测物理流量。

**假说：** compact跨custom-call表示可能减少物化/传递成本，或仅改变已被compiler优化的广播。首次Runtime实测prepare约0.727µs仅为历史背景，不能承诺大幅端到端收益，也不能假设其耗时全部来自广播。runtime2已归档HLO的prepare scoped字段为24,064，与逻辑广播的计数口径不同，进一步限制了“可直接节省1.31 MB物理内存/流量”的说法，详见新增编译证据段。

**合法性与风险：** 属于代码可表达的同输入/输出契约实现变换，不是现有config参数。必须保留VMEM合法布局、lengths动态值、非零batch行和NaN padding规则。历史 `hist-decode-prepare-padding` 已证明slice/pad顺序会触发Mosaic布局失败，因此不能回退到先切new行再pad；窄广播/SMEM替代是否支持必须编译验证。不允许把prepare搬到benchmark外。

### 3. attention循环的双buffer预取，只针对多tile情形

**具体范围：** `attention` 参数`:2931`，`body`的`:2940`–`:2957`，循环`:2988`–`:2998`，scratch`:3018`–`:3021`。构造两个`[T,Dpad]` BF16 tile和独立读/写完成约束；prime首tile，下一tile load提前启动，当前tile在append/writeback/compute结束后才允许复用，最后drain全部未完成DMA。

**源码依据：** 当前只有一个tile且每次load和store均立即wait；没有明确跨tile重叠。当前small原T1024只有1tile，**不具备跨tileprefetch重叠机会**。default有16tile但未做该case的正式timing；small换T512/256才是多tile，这会引入tile改变的混杂因素，需要对应的同tile单buffer对照。

**假说：** 对足够多tile的工作负载，允许独立DMA/compute推进可能降低串行等待。现有Profile无独立transfer/compute分量，不能把attention总时长直接填作compute_s，也不能称DMA瓶颈已测得。

**合法性与风险：** 可表达但改动大，不能把它当成纯配置试验。缓存input不能donate或原地写；每块仍应精确写入完整output。输出DMA进行中不得复写同一个buffer；只增加load semaphore而遗漏write drain会产生竞态。额外scratch、同步开销及VMEM预算可能抵消收益。历史 `hist-decode-dma2` 提供机制支持，不提供当前性能保证。

### 4. first-tile / single-tile attention状态专门化，保持cache路径

**具体范围：** `body`中online recurrence`:2977`–`:2985`与初始state/循环`:2988`–`:2998`。把第0块计算为`m=max(scores)`、`p=where(valid,exp(scores-m),0)`、`total=sum(p)`、`acc=dot(BF16(p),kv_latent)`；之后的tile继续原在线递推。静态`capacity == block_size`时可直接使用该单块状态，仍按原`:2998`分母保护输出。仅改变attention状态计算；原load、append、完整cache store、mask和BF16/FP32策略不变。

**源码依据：** 现有首轮从minfloat/0/0开始，对旧total和acc执行乘alpha再相加。当前small恰好只有这一轮；在数学理想模型里，旧state为0，更新项可省去。现有compiler是否已常量折叠/消除这些操作未知，源码存在它们不证明设备实际付出了可省成本。

**假说：** 显式首块路径可能减少归约外的vector操作或live state，也可能毫无收益甚至增加代码/分支负担。历史 `hist-fwd-state-lifetime` 仅支持研究这种递推重写，不是Decode实测收益依据。

**合法性与风险：** 这是需实测数值验证的代数等价尝试。必须保留valid全false时p=0、total=0、输出为0；不能删除mask让NaN tail进入matmul，不能改变P的BF16 cast或dot accumulation。首块特殊处理不得导致其cache被重复/遗漏写回。small、boundary的0/1/129长度及default多tile必须同时通过；完整cache必须按位相同。

**为何不另列“KV复用”为独立方向：** 当前同一个masked `kv` 已供QK和PV使用；删除重复读取并无明确源码对象。可以研究缩短其生命周期或重排store，但这属于上述流水/状态变换，不能凭名字宣称新复用。无效tail跳过attention计算但仍copy cache在语义上可研究，然而small/default当前length都满，不构成当前benchmark的有根据收益方向。

### 更小的DMA研究替代：同一tile延迟writeback wait

此项可以替代大改双buffer方向参与后续判断，不是额外已批准实验。**精确变换范围：** 保持`:2943`–`:2945`的load.start/load.wait及`:2951`–`:2956`的append、`tile[...] = kv`、store.start不变；把`:2957`的`store.wait()`移动到`:2985`的`return m_next,total,acc`之前，在acc更新之后无条件执行。不改tile、block、semaphore数量或实际attention计算。

依赖顺序可以写为：`load.start → load.wait → raw kv = where(append,new,tile) → tile=raw kv → store.start → masked kv = where(valid,raw kv,0) → QK/softmax/PV → store.wait → return/next iteration`。源码中`raw kv`是JAX SSA值，重新赋值`kv = where(...)`产生新值，不是对tile的原地mask。attention计算不写tile，也不读updated_ref；因此从源码数据依赖看，写回原始完整cache与对独立SSA值的计算可以重叠。该假说即使small只有1tile也有意义，不依赖下一tile预取。

必须保持的条件及风险：

- **同一DMA semaphore的生命周期：** load已wait后才开始store；每一轮必须在return前wait store，下一轮才可用同一个sem开始load。最后一轮同样要drain，不能依赖函数结束隐式完成。任何早退/分支都不得绕过wait。
- **DMA源存活与tile复用：** store完成前不得修改tile。当前mask只写SSA值，若编译重构引入`tile[...] = masked_kv`、或下一轮load提前覆盖tile，都会破坏未使用tail/padding的按位保留。
- **完整cache与input不变：** 输出仍由未mask的append后tile写回独立updated_ref。compute用masked值只是阻止NaN传播；不能将其当作存储值。新旧candidate比较必须包含完整cache和input cache不变验证。
- **SSA不等于物理寄存器证明：** lowering可能把读取tile的值spill、重载或插入依赖；源级合法顺序不能证明设备重叠。需要检查编译成功、真实oracle、whole program时间以及可用的trace事件；现parser不提供独立DMA时间。
- **资源争用和调度：** cache write DMA可能与MXU/XLU或VMEM访问争用，DMA descriptor/kv live range可能变长，编译器也可能保守串行化。即使正确，仍可能持平或退化。

这是“先提出源码可支持的重排、再测量”的方向；不能把当前5.03875µs attention分解成一个假造的store分量或承诺其全部可隐藏。

## 现有数学工具的精确输入要求

实现入口为`src/rsi_kernel_agent/profiler/models.py::calculate`；会话allowlist为`profiler/tools.py::AnalysisTools.call`。工具请求必须含四个字段`model, inputs, evidence_refs, assumptions`。evidence_refs必须来自当前固定manifest，且不能混用不同strategy的证据；此文中的文件路径本身不是合法manifest引用ID。所有结果类别固定为`model_predicted`，未知字段、bool尺寸、非有限值拒绝。

| model | inputs必填字段 | 适用映射与限制 |
|---|---|---|
| compute_memory | m,n,k,dtype_bytes | 单tile QK为m=H,n=T,k=Dpad；PV为m=H,n=L,k=T。模型所有operand统一dtype_bytes，不能准确表达BF16输入+FP32累加；它是单dense matmul近似，不是完整decode流量/时间 |
| tile | m,n,k,tile_m,tile_n,tile_k,dtype_bytes | 例如QK的m=16,n=N,k=Dpad，tile_m=16,tile_n=T,tile_k=Dpad。工具会假设每个reduction tile重读矩阵，忽略现有融合KV复用/streaming softmax，不应将其字节预测冒充HBM实测 |
| live_memory | buffers数组，每项name,elements,dtype_bytes,copies,start,end | start/end为非负逻辑schedule step，半开区间；不是时间戳。按实际可能共存的buffer建模，不能无依据把全部prepare、全部batch、scratch、编译暂存相加 |
| pipeline | stages,transfer_s,compute_s | stages是重复tile数，秒为单位；要求具体非负数，不接受null。缺乏独立DMA证据时只能明确假设做敏感性计算，不能声称参数测得；stages=1没有跨tile重叠收益 |

compute_memory的可选`compute_rate_flops_s`、`bandwidth_bytes_s`及live_memory的`capacity_bytes`，一旦填写，必须同时给`hardware_basis`和至少一个有效evidence_ref或明确assumption。当前不能把源码VMEM limit当实测capacity；也不能将文档峰值当已测持续带宽。

可供后续会话绑定当前source引用的QK语法示例（非当前session响应）：

```json
{"model":"compute_memory","inputs":{"m":16,"n":1024,"k":640,"dtype_bytes":2},"evidence_refs":[],"assumptions":["Single small-case QK matmul only; excludes softmax, PV, full cache writeback, prepare, mixed-precision accumulator and synchronization; no hardware rate assumed"]}
```

手算可核对的纯shape事实：small完整cache读+写下界为`2×B×N×Dpad×2=2,621,440` bytes；default为335,544,320 bytes；boundary为786,432 bytes。该下界只描述当前算法必须保留的独立cache输出边界，不含其他输入、layout、compiler或cache hierarchy成本。QK+PV浮点乘加计数按存储维度为`2×B×H×N×(Dpad+L)`：small37,748,736、default4,831,838,208、boundary9,437,184 FLOP；不含softmax/掩码/拷贝，也不声称MXU实际指令量。

## 正式 Analyzer 必须遵守的交接条件

1. 以最新Gate验收确认两个真实known-wrong Candidate的固定Oracle拒绝证明，保留其新Candidate/Run和失败工件。
2. 以新Runtime独立context及基线/Profile为准；先取全部策略卡片，再完整读取每条参与路线历史，不能复用旧Runtime结果假装新计时。
3. 显式选择精确父Candidate；固定source/config/build、同Run的Benchmark/Profile及Knowledge来源revision到AnalysisInputManifest。
4. 用原packet/response/session机制调用工具，并从当前证据选择多个有区别的正式Plan。此文件不预先选赢家，不声称四方向都应执行，也不将历史收益或理论值提升为当前实测。
