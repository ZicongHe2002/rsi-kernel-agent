# Phase 2C Profiler / Analyzer 实现与验收报告

2026-11-11。Phase 2C 已实现并通过本地验收，停在本阶段等待审查。按用户补充决定，**推理由当前对话模型承担，不使用独立模型 API**。2A/2B源码及schema v1保留；2D、Phase3未开始。

## 交付与复用

| 文件 | 实际职责 |
|---|---|
| [inputs.py](../../../src/rsi_kernel_agent/profiler/inputs.py) | AnalysisRequest、不可变AnalysisInputManifest、精确绑定、固定完整历史/知识版本、离线重放 |
| [profiles.py](../../../src/rsi_kernel_agent/profiler/profiles.py) | 保存的现有parser输出归一化、显式通用事件、样本统计和同钟域并集 |
| [models.py](../../../src/rsi_kernel_agent/profiler/models.py) | compute_memory、tile、live_memory、pipeline四类可复算模型 |
| [tools.py](../../../src/rsi_kernel_agent/profiler/tools.py) | 固定来源/历史/方法/Profile读取、数学计算与已有skill读取allowlist |
| [analyzer.py](../../../src/rsi_kernel_agent/profiler/analyzer.py) | 单一会话模型工具循环，完整代码理解/观察/多假设/多Plan协议 |
| [outputs.py](../../../src/rsi_kernel_agent/profiler/outputs.py) | 引用/父绑定/输出结构复核、未支持断言降级、兼容现有Bundle/Plan发布 |
| [session.py](../../../src/rsi_kernel_agent/profiler/session.py) | packet/response文件交换和持久重放；无需独立API |
| [evaluation.py](../../../src/rsi_kernel_agent/profiler/evaluation.py) | 固定案例rubric、引用/断言/多样性/绑定和工具复算指标 |
| [接口指南](../../../docs/phase2c-profiler-analyzer.md) · [实施计划](../../../docs/superpowers/plans/2026-11-11-phase2c-profiler-analyzer.md) | schema、调用示例、数学定义、边界与2D建议 |

全部位于Profiler子包，无新顶层系统模块、第二个数据库或Knowledge MCP替换。方法仍来自既有KnowledgeClient/原MCP，Memory仍是原ExperimentStore快照；完整原始请求见[sources/phase2c-request.txt](sources/phase2c-request.txt)。[protected-verification.json](protected-verification.json)确认10个原2A/2B及网站/MCP源码哈希全部未变。

精确变更文件、修改前原始字节快照及最终SHA256见[changes.json](changes.json)。当前路线图、Profiler/Analyzer说明、工程/知识索引和本周完成记录已同步；15份Markdown的123个相对链接有效，见[links.json](links.json)。周记录API返回401后采用原字节备份、重读及写前校验的原子追加，未读取认证文件、未勾选无关事项，见[weekly-update.json](weekly-update.json)。`.artifacts/phase2c-2026-11-11/`是本轮隔离合成store、会话交换和验证辅助脚本，不是新增运行后端。

现有离线入口为 `C:/Users/fengluo/Desktop/training/PallasKernels/tools/tpu_duration_profile.py::read_profile`。只读核对实际源码、调用和依赖，未导入执行外部代码。它读取唯一XPlane protobuf，输出目标program/custom-call时长样本、设备offset和统计，不提供独立DMA/waits/资源计数或LLO正文。当前环境缺少其TensorFlow/protobuf依赖，本轮复用保存输出与显式注入reader接口，**没有实跑原始XPlane解码**。路径、源码SHA256、输出schema和限制见[parser-audit.md](parser-audit.md)。

## 输入、证据和输出

[analysis-request.json](analysis-request.json)和[analysis-input-manifest.json](analysis-input-manifest.json)是实际运行的合成示例。每条策略绑定Task/Workload、精确ParentCandidate、source/config摘要、context、Run、独立Profile/Benchmark、Memory snapshot及工具版本；A/B完整历史各14条。当前证据及其所有上游必须同Run/候选/策略；跨策略Knowledge复用允许。

源码/配置工件必须存在且摘要一致。缺失Profile、失败/被反驳证据、冲突Benchmark值保留原记录并标不可用。当前有效计时需与原2A支持的measured/trace-derived/derived timing匹配，不能由Analyzer覆盖Benchmark。预测及来源主张不能经多层derived包装变为测量。

Knowledge note原始字节revision与解码正文工件摘要分开，保留BOM区别；来源已变则stale并保留原excerpt，不用新文档冒充旧版本。Memory有固定完整快照；Knowledge没有全库快照，因此不声称合并输入是原子一致快照。manifest回读重新校验记录、所有可用工件、来源捕获和绑定。

归一化Profile区分measured/trace-derived/compiler-estimated/inferred/unavailable。同设备同钟域取区间并集，嵌套program/kernel不直接相加；缺失数值是null，事件数/并集不等于critical path。

模型输出的代码理解是待验证解释；事实需精确predicate匹配引用原值，事实文本由程序渲染，模型因果解释另存hypothesis。来源/历史分别是source_claim/historical_claim。无法支持的断言降级，不删除仍合法的探索Plan。Plan有准确策略/父、具体修改、引用、观察与计算ID、权衡、风险、未知、候选变体和正确性/性能验证。

2A AnalysisBundle只能属于单Run，因此汇总保存在内容寻址工件，分别生成A/B的原AnalysisBundle，Plan使用原Plan和MemoryWriter.bind_plan。[publication-example.json](publication-example.json)记录实际两Bundle/四Plan及完成Decision。保存前重放完整模型选择和工具结果，拒绝caller篡改。相同key重试相同结果，无自动选winner、晋升、父选择或Frontier变更。

## 实际计算与会话模型示例

每次计算记录model revision、公式、输入、单位、假设、遗漏成本、结果、引用及model_predicted标签。未提供hardware capacity/throughput时保留null。

| 模型 | 已执行示例结果（全部是模型估计） |
|---|---|
| Compute/memory | M=N32、K64、float32：131072 FLOP，最低20480byte，6.4 FLOP/byte；硬件时间下界未知 |
| Tile | A16×16×16：36864byte；B8×8×8：69632byte；B8×16×8变体：53248byte；均假设无跨输出tile缓存 |
| Live memory | A两份left、两份right、一份accumulator：5120byte；不含隐藏临时变量，容量未知 |
| Pipeline | 假设2µs搬运+3µs计算，4阶段理想14µs/串行20µs；与合成10ms计时无拟合关系 |

真实会话推理读取固定源代码/任务、A/B完整Memory、各自Profile、Knowledge版本后选择并运行11次工具：2次Profile、2次history、1次knowledge、6次数学计算。完整调用参数/返回见[tool-calls.json](tool-calls.json)，实际AnalysisBundle汇总/PlanSet见[synthetic-example.json](synthetic-example.json)。这份例子由当前对话模型生成，测试fixture的脚本化提案另在测试中使用，未把后者冒充LLM效果。

四个实际Plan分别是A双buffer预取、A单独调整K阻塞、B矩形输出tile重用、B条件性right tile布局packing。每个Plan保留替代解释、具体控制实验与边界测试，没有提前选赢家。所有10ms/12ms及保存trace是**synthetic软件验证数据，没有真实Kernel/TPU性能结论**。

## 固定会话模型评估

[10个固定案例](../../../tests/fixtures/analyzer_cases.json)在模型运行前冻结；模型包不含gold。四组使用同一继承会话模型、每组最多6响应/12工具/60000字符，均独立无历史上下文。底层模型精确版本和provider token统计未暴露，因此只能声称相同会话配置及动作上限，不能声称严格token等预算实验。

| 配置 | 最终案例rubric | 工具调用/成功复算 | 整体输出验收 |
|---|---:|---:|---|
| 基础指令 | 10/10 | 0，复算指标N/A | 未通过：事实文本没有机器可核对的规范断言格式 |
| 指令+已有skills | 9/10 | 0，N/A | 未通过：一处结论类别与固定rubric不符，及断言格式 |
| skills+确定性工具 | 9/10 | 12次，成功11/11可复算 | 未通过：一处类别、断言格式和一次被拒绝的混合策略计算；错误保留 |
| 工具+证据复核 | **10/10** | **12次，12/12可复算** | **通过** |

初测案例为9/8/9/9；base/skills/review的跨策略引用经统一的**结构校验反馈**在原预算内修正，没有反馈gold或改动结论类别/数值。原始响应、初始答案、结构修正和分数全部保留于[evaluation](evaluation/)与[evaluation-initial-results.json](evaluation-initial-results.json)。最终逐项分母/指标见[evaluation-results.json](evaluation-results.json)，条件见[evaluation-protocol.json](evaluation-protocol.json)。最终模型响应事件数base3、skills3、tools4、review5（含被拒绝格式及wrapper修正）。

四组最终Plan父绑定4/4、两策略均有两份不同机制、规范化重复率0；引用检查与不确定性case rubric独立记录。base/skills/tools的“未支持断言”评分是**未满足精确可核对格式的代理**，不表示这些自然语言句子全部错误。review组有17条断言因predicate为空或路径多写一层value而降级成假设，另3条unknown；没有保留机器验证的事实断言，因此分母为0、未支持率N/A，不能报告为0%或零错误。这个样本暴露了机器可核对断言覆盖不足，保留原样不事后修饰。数值case回答和工具计算仍单独验证，前述独立会话示例包含实际通过的predicate。任意代码语义与自然语言同义Plan无法靠此评分器证明；独立review保留人工判断。

每组仅一个批量样本，公共系统指令仍共享；结果不是skills或tools普遍提升的统计证据，也没有对真实TPU优化能力作测量。最终默认review工作流通过固定验收；对比组失败如实保留。

## 测试与审查

环境：CPython3.13.14、SQLite3.53.1、MCP1.30.0、Pydantic2.14.2，无安装升级。最终完整输出：

```text
Ran 162 tests in 52.115s
OK (skipped=1)

Ran 5 tests in 0.733s
OK
```

即根级**161通过、1跳过、0失败/错误**，相对2B新增77项通过；既有MCP回归5/5。唯一跳过仍为Windows symlink权限不足，未放宽权限；junction/路径/并发等原验收保留。命令与版本见[test-environment.json](test-environment.json)，完整日志见[tests.txt](tests.txt)、[existing-mcp-tests.txt](existing-mcp-tests.txt)。asyncio同步fixture的slow-callback诊断原样保留。

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -m unittest discover -s tests -v
# knowledge-site目录：
& '.\.venv-mcp\Scripts\python.exe' -B -m unittest test_mcp.MCPTests -v
```

测试覆盖20项输入/重放、16项Profile、13项模型、11项Analyzer、11项评分器、5项集成、1项真实原MCP/HTTP临时库。后者验证真实revision读取、来源改版后旧manifest稳定且新capture显式stale，不创建.auth。外部解析依赖缺失和生产远端连接均未伪称验证。

各确定性组件先观察缺失实现RED；审查缺陷经失败复现后修复，并通过回归测试或独立专项脚本复核。原MCP/集成初次组合即通过的部分如实记录为组合验证，不虚称RED。独立reviewer无会话历史审查并复现，11项Important已关闭，包括分类误升、结果/manifest篡改、失败证据、冲突计时、Profile缺失与预测祖先、重试状态、长ID、兼容计时及评分验收门槛。具体复现和最终复核见[review.md](review.md)。

## 剩余边界与停止点

- 没有原始XPlane实际解码、真实MLA、TPU、loaded-code身份或硬件性能验证；外部parser只读，未访问Colab CLI。
- 会话宿主负责模型推理和选择已有工具；模块本身没有模型服务/自动网页浏览。新外部资料必须先通过已有工具检索并固定版本到新manifest。
- 源码是固定相关文件文本，不自动解析完整仓库语义；变换合法性和性能必须由后续实现与Benchmark验证。
- 继承2A全量快照内存成本；无大规模trace/历史压测。模型输入可能超过宿主上下文时应显式分轮读取，不能把被截断数据称完整。
- 文件会话是可信本地单写入者模式；多记录发布不是批量事务，以最终完成Decision为交付标记。失败前缀/orphan blobs保留；备份需同时保存一致DB、工件树和会话记录。
- Knowledge捕获信任原MCP返回revision，不声称解码正文可重建BOM原始字节；历史全文缺失时保留excerpt限制。
- Git CLI不可用，未创建提交、不声称工作树干净。原2A/2B历史报告未改写。

推荐2D先实现消费完成PlanSet的预算/队列/有限repair状态机，再用合成Runner验证失败、延期、幂等和恢复；复用2A晋升和精确父链，不由LLM修改最佳指针。真实执行及MLA/Colab集成保留Phase3。**这里只提出2D建议，当前停止在2C等待用户审查。**
