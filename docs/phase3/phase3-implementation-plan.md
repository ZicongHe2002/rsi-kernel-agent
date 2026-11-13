# Phase 3 实施计划

2026-11-12。执行依据为用户归档的 [原始请求](../../knowledge-site/manifests/phase3-2026-11-12/sources/phase3-request.txt)。本计划在运行时代码修改前保存；各 Gate 通过需要新证据，计划本身不代表验收。推理由当前会话模型通过既有 Analyzer session 完成。

## 权威来源和复用

主项目为 `C:\Users\fengluo\Desktop\rsi-kernel-agent`；当前 Kernel 为 `C:\Users\fengluo\Desktop\training\PallasKernels`；历史来源为 `C:\Users\fengluo\Desktop\project2`；桥接实现为 `C:\Users\fengluo\Desktop\colab\google-colab-cli`。外部 Kernel 和历史只读，新 Candidate 使用主项目内独立工作区。保留 schema v1、原 Store/ArtifactStore/Knowledge/Memory/Profiler/Analyzer/Controller/consider。不得增加模型 API。

## 分工与文件边界

- `docs/phase3/` 保存四目录审计、历史恢复、工作负载/正确性、连接与各 Gate 报告。
- `workflow/execution.py`、`intake.py`、`controller.py`、`evaluation.py`、`__main__.py` 做必要兼容扩展。synthetic 原契约和确定性 ID 不变。
- `workflow/real_protocol.py` 集中真实源码包、固定 runner/Oracle/Benchmark 协议和回执验证，作为原 Evaluator 的验证辅助；不新增晋升算法。
- `workflow/colab.py` 通过已安装 Colab 桥接提交、查询、回传和发布原 Store Decision；`workflow/colab_worker.py` 是远端独占队列与持久操作记录；固定 MLA runner 负责已有 Kernel/reference/benchmark/parser 的调用。
- `tests/test_phase3_*.py` 分别验证包身份、真实回执拒绝、远端恢复及 CPU harness。网络/真实 TPU 验证单独归档，不伪装为本地测试。
- `knowledge-site/manifests/phase3-2026-11-12/` 保存请求、源文件起始摘要、测试日志、实验索引、变更清单与实现报告；工件依旧使用原 ArtifactStore。

## 任务 1：P3.0 审计与历史恢复

1. 阅读 AGENTS、实际 2A–2D+ 调用链和四目录开发约定，记录可复用接口与 synthetic 硬编码位置。
2. 分别重建 Forward/Decode 输入、真实入口、Reference、Benchmark 和 XPlane parser 能力，保存相关源码摘要。
3. 索引 project2 原始文件，通过 Git/Diff/日志重建已知谱系，不能用 mtime 推断父子。保留缺失证据和失败。
4. 将高价值历史整理为有来源的 Knowledge，缺少正式 Run 证据不得伪造历史 Run 或当前 Best。
5. 审计现有 Colab 协议。控制面成功不代表 TPU 已分配；超时分配先查询，不盲目重建。

## 任务 2：P3.1 兼容协议、桥接与恢复

先写会失败的测试，再实施：真实 context 分发、完整 bundle 摘要、被替换 helper/错误导入路径、远端 completed 只取回、unknown 不提交、两 Task 设备互斥。

保留 synthetic 四阶段。real 阶段为 generate → compile → correctness → benchmark → profile。variant 移除 synthetic scenario/latency，增加 `source_bundle_ref`；入口 `source_ref` 继续可供 Analyzer 读取。bundle 为 `{schema_version:1, entry:{path,symbol}, files:[{path,artifact_ref,role}]}`，路径拒绝逃逸、重复和链接。build JSON 必须绑定完整 bundle digest，固定 runner/Oracle/Benchmark 摘要来自不可变 context.protocol。具体严格 schema 在 `real_protocol.py` 和其测试定义，实施前由协作者共享。

后端复用原 request/receipt envelope、Decision 和幂等键。上传包不等于真实加载证明：独立进程记录 module path、入口、实际加载依赖哈希和 specialization。远端 operation 先持久 claim，再执行，receipt 原子发布；本地网络超时不释放远端锁。Runtime 丢失、执行后未落盘等不可判断情况保留 unknown。

原桥接内部管理认证，不读取或输出秘密。使用其 API 避免 CLI 的自动升级检查与含敏感字段调试输出。验证真实 Runtime/TPU v6e、版本、同步最小 JAX 结果，再记录 P3.1 Gate。

## 任务 3：P3.2 固定 MLA 基线

优先 Decode 连续 Cache，最终根据审计完整性和真实环境决定。先冻结 Workload Matrix（合法小/中/默认/边界 Shape）、Oracle、容差、必要计时工作与输入生成规则。FWD 独立 Task/contract，不与 Decode 混算。

上传哈希清单覆盖 eager import 的完整依赖。真实环境不自动升级。正确性使用同一输入比较原独立参考，检查 output/cache、dtype、shape、有限性、误差；增加已知错误候选拒绝。计时复用原 measure，同步全部 pytree，分别报告 compile/warmup/host-synchronized samples，重算统计。无证据不得称纯设备或物理绝对独占。Baseline 未过不得开始优化。

## 任务 4：P3.3 真实 Profile

正式计时结束后独立 operation 捕获 XPlane；确认当前 API/trace mode，保存原始字节及 parser 源码/版本。原 `read_profile` 的 program/custom-call 时长可归一化，不能扩展成 DMA/MXU/VMEM 实测。Profile 与 Benchmark 属于事先声明的同一逻辑 Run，分别保留 operation 和时间边界。缺少 Profile 保存 unavailable；P3.3 不通过且停止优化。

## 任务 5：P3.4/P3.5 两轮真实实验

1. Gate 通过后读取新 Memory snapshot、全部策略卡片及参与路线完整历史；检索原 Knowledge。
2. 当前会话生成并提交合法 Analyzer response，多条有依据 Plan，明确父、位置、模型、证据、风险和验证。
3. 独立 Candidate diff/source bundle；Controller 执行，固定 Evaluator 验证后调用原 consider。失败和有限 repair 保留。
4. 第一轮完成后新 snapshot，第二轮显式引用新结果并改变方案，不能重复旧方案充数。至少两轮真实硬件实验才申请最终闭环验收。

## 任务 6：验收和归档

运行新专项及既有根级完整套件、原 MCP 回归，按 CPU/synthetic/real 分开报告。独立 reviewer 检查实际实现和证据，修复重要缺陷并运行相关回归。保存命令、环境、原始结果、修复/失败、摘要、完整限制及 Gate 状态；维护路线图、索引、周完成记录。Git 不可用则不声称提交。外部 Gate 被阻塞时继续不依赖它的测试/审计，并明确所需用户操作，不把 Phase 3 标完成。
