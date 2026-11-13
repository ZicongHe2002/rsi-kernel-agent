# Phase 1 变更报告

> 2026-11-11（Asia/Shanghai）。用户已批准文档整合与两个资源的只读审计；Phase2尚未开始。完整前后字节数/哈希见[变更清单](phase1-changes.json)。

## 修改的原有文件

| 路径（相对knowledge-site） | 变更 |
|---|---|
| `content/学习方法/rsi-kernel-agent.md` | 补Profiler→Analyzer、完整多策略Memory流程与模块/实践导航；更新真实资产和两任务范围 |
| `content/学习方法/rsi-optimization-workflow.md` | 去掉旧单策略限制，合并重复2.3/2.4及状态段，保留独有解释与来源；明确deferred和条件补采 |
| `content/学习方法/RSI实践/rsi-multi-plan-exploration.md` | 迁入原方法搜索全部实质正文为14–24节；保留2张图、3案例、方法卡、适用性和4来源 |
| `content/学习方法/RSI实践/rsi-method-search-and-selection.md` | 保留原路径的迁移说明；原始字节进入快照，不删除档案 |
| `content/学习方法/RSI实践/rsi-profiler-analyzer.md` | 修复4处公式，补变量/单位/假设/遗漏成本；区分证据类型、claim、Plan、availability |
| `content/学习方法/rsi-memory.md` | 明确七个独立状态及版本化完整历史；重写状态卡示例，保留旧模板快照 |
| `content/学习方法/rsi-profiler.md` | 链接共用证据契约，记录新发现的可复用capture/parser |
| `content/学习方法/rsi-benchmark.md` | 统一证据字段；拆request_status/availability/claim_status；旧扩展任务表标为未授权历史设想 |
| `content/学习方法/rsi-knowledge-search.md` | 指向合并后的canonical实践及证据契约 |
| `README.md` | 更新真实目录/周清单结转/完整测试命令/MCP读写及认证迁移边界 |
| `requirements-mcp.txt` | 仅把过期read-only注释改为read/write，依赖版本未变 |
| `content/待办清单/一周待办 · 2026-11-09.md` | 仅追加本轮已核实结果；原安排/复选框不变 |

## 新增维护材料

新增工程index、网站文档index、共用证据契约、Phase2路线图、ACCESS_POLICY、认证状态迁移提案、未激活的.gitignore提案、脱敏连接说明与全注释MCP示例。manifests内新增MLA/Colab审计及源码指纹、文档/来源/链接/图片/验证/变更清单、原文/审批快照。精确所有新文件路径由变更JSON列出，包括一次性维护脚本与验证结果；没有新增运行框架。

## 静态集成结论

精确未来优化allowlist为 `PallasKernels/pallas_kernels/kernels/mla_decode.py` 与 `mla_fwd.py`，原仓库保持只读。Decode文件中 `mla_decode_paged_reference` 仍受保护；后续oracle须由冻结受信基线独立执行以防候选模块全局副作用。Forward默认A09前提需独立检查；不能通过改YAML/容差使其通过。

已找到并计划复用 `tools/get_tpu_duration_info.py`、`tpu_duration_runtime.py`、`tpu_duration_profile.py`；内部编译入口时长与public wrapper墙钟范围不同。已有Colab CLI只做薄适配：exit0不能证明任务成功，status本地IDLE不能证明远端停止，传输须独立哈希和完成标记。`run`会创建VM，test_all会删TPU锁，都不作为本计划入口。

## 验证和剩余风险

验证详情见[验证报告](phase1-validation.md)及JSON。独立文档审查发现的状态字段混用、历史任务范围、表格断行、补采图示和哈希路径表述已修正。集成审查以报告及实际源码交叉核对，任何未解运行条件均保留在路线图。

未完成项是执行前置条件，不是本轮偷偷实施的工程：目标session/设备/版本/预算未知；CLI普通执行可能写持久settings，与只读约束需协调；Forward A09域未在TPU核验；无原样基线、无优化性能证据；Git命令不可用；认证状态仍位于原.auth，迁移未实施；部分原始图片/报告/来源版本仍缺失。

没有安装Skill/依赖、改账号ACL或ChatGPT授权、触碰.auth、改Kernel/CLI原仓库、创建候选工作区、上传源码、重置session、升级runtime或执行TPU。

## 哈希清单边界

`phase1-changes.json`为自描述索引，其自身不递归记录最终SHA；其他新增/修改/保留原文件均记录before/after哈希。原文快照为`.original`扩展名，网站不把快照当current文档读取。原本被忽略的维护工具仍留在`.artifacts/`并明确标为辅助产物；不因此隐藏它们的文件变更记录。
