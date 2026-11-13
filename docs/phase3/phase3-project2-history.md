# Phase 3：project2 历史恢复

2026-11-12。来源类别：**Source audit / 对历史 Real TPU artifacts 的离线核验**。本次未执行 project2 中的脚本、Notebook 或 Kernel；未建立历史 Run，也未写入当前 Best。

`C:\Users\fengluo\Desktop\project2` 是历史实验来源；当前权威源码仍为 `C:\Users\fengluo\Desktop\training\PallasKernels`。project2 根 AGENTS 已读取，其知识库已迁移至本 RSI 项目。未读取认证文件或凭据。所有新增文档和核验脚本均在 RSI 工作区。

## 索引范围与可复查输出

[文件索引](../../knowledge-site/manifests/phase3-2026-11-12/project2-inventory.json) 收录 74,382 个相关路径：15,764 源码、98 Notebook、1,681 文档、47,912 数据/日志、8,141 Profile/IR、375 XPlane、411 ZIP。它是路径/大小索引，不代表逐个完整阅读。排除了依赖环境、缓存、Git 对象、凭据名称和无关整仓；完整排除规则见索引。初次普通 Windows 路径枚举出现长路径误报，改为长路径读取后本次索引范围内 unreadable 为 0；没有修改权限。可疑 pytest 临时目录仍按规则排除。

[结构化实验清单](../../knowledge-site/manifests/phase3-2026-11-12/history-experiments.json) 恢复 **136 条**：Forward result14 的 27 个阶段记录、Decode 第二轮 68 个候选、第三轮 39 个候选、2026-11-11 默认配置 2 个记录。结果为 98 MEASURED、4 REJECT_LSE_COMPILE、24 COMPILE_FAIL、8 CORRECTNESS_FAIL、2 passed。每条包含来源、源码 SHA-256、明确配方/比较对照、shape、环境、正确性、原始 latency、Profile 路径、限制；未知源码父关系保留 `unknown`。

另保存 209 个关键证据文件的 SHA-256；校验 Forward result14 的 40 个源码文件、Decode 9 组各 23 个来源文件、最新 payload 的 688 个源码文件均匹配原始清单。最新下载包中 **1,044 个文件**的长度与摘要全部匹配。源文件出现于多个组是可追溯复核，并非不同实验的独立来源数。本次未重新解析全部 XPlane protobuf，也未复跑历史正确性；“通过”来自保存的原始结果和此次一致性检查。

## 时间线和关系

| 时间/阶段 | 有依据的关系与方法 | 证据等级与边界 |
|---|---|---|
| 2026-08-31 | expanded 与 absorbed MLA A/B；公式重结合、tile sweep | trajectory 明确记录部分早期原始 ZIP 已丢失；缺失条目只能保留文字历史 |
| 09-01–09-03，result1–5 | QK reduction 192→256、可见块 mask bypass、q-subtile、对角 panel512 | 报告指向原始结果；本轮优先恢复方法和限制，未把早期全表升级为逐文件核验 |
| result6–12 | N14 paired-Q、projection head grouping、B04 首块初始化、D01 Q-RoPE 布局、未归一化 online softmax；组合形成 BDU01 | 关联依据为报告/配方，不根据文件时间推定 Git 父链；H16/H32、JAX0.7.2/0.8.1 分开 |
| 09-14 result14 | BDU01 派生配方；R20 是布局对照，R25 改为 score[K,Q] / accumulator[Dv,Q] | 27 阶段原始 manifest、源码、CSV、trace 已定位并哈希核验；26 初筛候选中 22 measured、4 compile rejected；R25 另有 final |
| 09-16–09-18 Decode round1–3 | D04 dense 对照、Google/SGLang resident cache 适配、FP32 score、DMA 宽度对齐、A 系列准备路径 | 第二/三轮 107 候选完整恢复；dense/native 不共用计时边界 |
| 11-04 main check | main `d336a54…` 默认 Decode B8 prepare 编译失败；整批补零后切行修复 | 保存原版失败、新增回归失败、补丁通过、CPU/TPU 分开记录 |
| 11-04 fix commit | `d336a54…` → `0f552ae…` | `result/mla_decode_fix_20261104/commit.json` 明确 base/commit，附 patch 和源码包；本轮未遍历 Git commit DAG |
| 11-11 rebase/VMEM | 上述修复分支 rebase 到 `9979d49…` 后 head `1912b994…` | README 和 payload manifest 明确；“rebase base”不声称是直接 parent；两默认配置真实运行原始证据齐全 |

以上是有证据的研究演进，不是宣称每个相邻阶段都有精确源码父子关系。Forward 各 recipe 有 `parent` 标签和独立 `control` 标签，两者已分别保留；没有候选独立 commit 时，正式 `parent` 仍为 unknown。

本机无可用 Git CLI。只解析 `.git/HEAD`、worktree 指针和 refs/packed-refs：历史主 checkout 为 `fef5eab…`，旧 FWD clean 为 `4a091d…`，Decode full PR 为 `0a954b…`，Decode fix 为 `1912b994…`。这些是本机元数据，不证明工作树干净或源码未修改。未创建提交。

## 与当前权威源码的联系

| Kernel | 2026-11-11 实际导入摘要 | 当前训练仓库比较 |
|---|---|---|
| Forward | `f9b07f0c62d14d4ecf4b17242b3ee913db4c0039e20786830688c8ec38fff13d` | 当前 `mla_fwd.py` 原始字节完全相同 |
| Decode | `2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae` | 当前为 `e53b0e5ccff3ecf18192f30ef179cdb207c177f2f2dac48b2a08f40acc5000a6`，归一化换行后也不同 |

单一 Forward 文件相同不能替代依赖、YAML、运行环境和实际加载身份复核；Decode 历史 pass 更不能自动迁移到当前版本。历史 20 次 Profile 样本只是已有 Profile 证据，不是 Phase 3 新建的独立正式 Benchmark。

## Knowledge 接入边界

高价值资料按 [Forward](mla-forward-history.md)、[Decode](mla-decode-history.md)、[方法](optimization-methods.md)、[失败](failed-experiments.md) 提炼，已通过原 KnowledgeClient/MCP 保存为 `RSI/History/phase3-project2-2026-11-12.md` 和 10 篇 `source_supported` MethodRecord，并全部回读核验。来源笔记保留原始路径、SHA 和引文；10 处相对链接已通过 `update_note` revision CAS 改为明确本地绝对路径文本。各方法通过 `save_method(expected_revision)` 同步 SourceRef/source_revision，块外说明与摘录保持不变；来源状态全部为 available。

首次导入与本次维护的完整 revision 回执见 [history-knowledge-import.json](../../knowledge-site/manifests/phase3-2026-11-12/history-knowledge-import.json)，当前来源 revision 为 `691a21c5eac34ad28638ae7c24b487b3280dad71230e8a5362c970e74dd2e6f5`。笔记中的原文 SHA 指首次导入时的文档字节，不指随后更新接入状态的本地文档或调整链接展示后的笔记。`local_experiment_refs` 均为空；方法历史收益保留 workload/环境/计时边界，没有创建历史 Run 或写入当前 Best。

本轮优先恢复足以支持首个 Forward baseline/后续 Plan 的历史，未穷尽所有 DOCX、ZIP、Notebook 输出或 Git 对象。其他历史可以在 P3.1–P3.3 进行期间继续梳理，不阻塞真实连接。
