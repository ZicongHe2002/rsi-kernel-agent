# RSI 文档、源码与执行权限政策

> 2026-11-11｜政策记录，不修改系统 ACL、账号授权或连接状态。

| 资源 | 本轮允许 | 后续边界 |
|---|---|---|
| 本仓库 knowledge-site 文档 | 按用户批准整合、保留快照、生成索引/清单和周记录 | 不把设计写成已实现或 TPU 实测 |
| PallasKernels 原仓库 | 只读源码/配置/测试及 Git 元数据 | 不写回；候选必须使用隔离副本 |
| 候选 Kernel | 本轮不修改 | 仅 `pallas_kernels/kernels/mla_decode.py`、`mla_fwd.py` 的实现部分可优化；Decode 内嵌 reference 也受保护 |
| 共享依赖、reference、测试、注册、配置、容差和环境 | 只读 | 候选包内保持基线字节，具体清单见 MLA 审计 |
| 既有 Colab CLI | 只读 README/源码/测试 | 复用，不替换连接系统；实际执行需批准其正常状态/日志副作用 |
| TPU／远端会话 | 不连接、不执行、不重置 | 正式任务仅 MLA Decode 和 Forward；身份、设备、预算、版本、上传范围先明确 |
| `.auth`、凭据、浏览器状态 | 不读取秘密、不复制、不移动、不删除 | 迁移另行审批；不输出认证链接、Cookie 或令牌 |
| ChatGPT/Codex账号／ACL | 仅记录文档与配置结构证据 | 不授权连接器、不改变角色或账号权限 |
| 原始 traces、私有源码、输入和结果 | 本轮只制定传输方案 | 仅进入批准环境；不能自动上传外部模型、网站或公共仓库 |

网站代码支持 owner 和按分类/单篇授权的成员；这些是代码能力，当前数据库授权没有读取。私有 stdio MCP 的 owner 能力与浏览器只读账户独立，不得把浏览器限制推断为 MCP 只读。Git 不保存完整 Windows ACL，仓库可写也不等于 TPU 或外发授权。

安全凭据使用已有批准的本机存储或交互流程；需要某项操作时仅加载它明确需要的命名变量，不读取整个秘密文件。知识库只保存角色、环境变量名称、权限和连接要求，不保存值。

当前实现把认证状态置于 `knowledge-site/.auth`，与文档区边界不一致，但没有据此认定泄露。本轮只查看代码引用和 Git 跟踪元数据，未检查秘密内容。忽略规则不能改变物理位置，也不能取消既有跟踪；参见[迁移提案](AUTH_STATE_MIGRATION_PLAN.md)与[忽略规则提案](gitignore.proposed)。

来源：[用户审批副本](../manifests/phase1-2026-11-11/sources/phase1-approval.txt)、[MLA审计](../manifests/mla-integration-audit.md)、[Colab审计](../manifests/colab-cli-audit.md)、[源码与验证记录](../manifests/phase1-validation.md)。
