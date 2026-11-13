# 知识库、Codex 与 Colab 连接边界

> 2026-11-11｜脱敏连接说明；本轮未登录、重授权、重置会话或验证远端连通。

## 三条不同连接

| 连接 | 现有实现 | 权限与状态 |
|---|---|---|
| 知识库网站 | server.py，默认 `http://127.0.0.1:8765` | owner/成员授权由网站管理；本轮未读实时账户数据库 |
| ChatGPT 知识库 MCP | mcp_server.py + connect-chatgpt.ps1 + 既有私有隧道 | search、fetch、create_note、update_note；不是只读 MCP；连接端实际授权未验证 |
| Colab 执行 | Desktop/colab/google-colab-cli | 复用既有 exec/upload/download；未调用 CLI、未确认当前 session/TPU；细节见审计 |

网站 HTTPS 分享和 MCP 隧道彼此独立；普通网页账户只读不意味着私有 MCP 也只读。MCP写入会把 title/category/content/revision 交给网站保存接口；409必须重新读取并合并，不能盲重试覆盖。

本轮未认证 GET `/api/note` 返回401，故文档维护使用现有文件的 SHA-256 前置检查、原文快照及写后复核，没有读取 `.auth` 或绕过认证。用户如果同时更新文件，检查失败就重新合并；API访问恢复后仍优先使用 revision 接口。

## 配置和授权

项目 `.codex/config.toml` 当前不存在。用户级配置与本会话工具不能导出成通用凭据；ChatGPT账号插件/连接授权也不是可复制的普通文件。需要连接时由用户在相应产品的交互界面完成授权，并核对该连接实际提供的读写工具和作用范围。

`connect-chatgpt.ps1` 引用命名变量 `CONTROL_PLANE_API_KEY`，不在仓库存储其值。不要复制 `.codex/auth.json`、provider认证配置、Cookie、浏览器数据库或service-account内容。现有 Colab认证模式、超时和持久状态副作用以[CLI审计](../manifests/colab-cli-audit.md)及源码为准，不能照抄过期README默认值。

示例见[mcp-servers.example.toml](mcp-servers.example.toml)，全部注释且不可直接连接。它只展示配置结构，不创建ACL或授予访问。

## 后续连接验收

批准具体连接后再验证：会话身份与范围、非秘密健康检查、实际search/fetch、限定文档的revision写入、拒绝越权。TPU执行必须另行指定会话/设备/预算/上传文件清单，且只运行MLA Decode、MLA Forward。不要为了健康检查自动分配/停止/重置Colab VM，不自动安装依赖或升级runtime。

参考：现有[CHATGPT_SETUP](../CHATGPT_SETUP.md)、[权限政策](../access/ACCESS_POLICY.md)、[认证状态迁移提案](../access/AUTH_STATE_MIGRATION_PLAN.md)。产品机制来源沿用2026-11-11已获取的[官方Skills文档](https://learn.chatgpt.com/docs/build-skills)与[官方MCP文档入口](https://developers.openai.com/codex/mcp/)；这些页面不能证明本机账号已连接。
