# Phase 2B：既有 Knowledge MCP 接口审计

2026-11-11。只读审查既有 `knowledge-site/mcp_server.py`、`server.py`、`auth.py`、`local_access.py`、`test_mcp.py`及脱敏连接文档；没有读取认证文件、修改账号权限、启动隧道、访问Colab或改动网站实现。生产写入能力依据源码及隔离测试，不把只读smoke称为生产写入验收。

## 真实工具 schema

运行已有解释器，通过 `build_server().list_tools()` 核对运行时生成schema，与源码一致：

| 工具 | 输入 | 返回 |
|---|---|---|
| `search` | query默认空、最多1000字符；offset≥0默认0；limit 1–50默认20 | results、total、next_offset；note项含id/title/category/updated/revision/excerpt |
| `fetch` | id必填；offset≥0默认0；limit 1–24000默认24000 | id/title/category/updated/revision/content/offset/total_chars/next_offset |
| `create_note` | title 1–150字符；content≤1,000,000字符；category默认「未分类」 | 已保存id/title/category/updated/revision；无content |
| `update_note` | id、revision、完整content必填；revision为小写64位SHA-256；title/category可省略 | 同上；省略title/category保留原值 |

search/fetch标注readOnly和idempotent；create_note是非幂等写；update_note还标注destructive。工具参数不暴露root/site_port。服务默认 `BASE_DIR/content` 和网站端口8765；根目录必须已存在。请求序列化后另受4MB HTTP上限约束。

search是标题/分类/正文的casefold子串匹配，最新修改在前；摘录仅180字符左右，不能替代fetch全文。fetch以字符分页，revision来自原始字节SHA-256，包括可能的BOM；正文用utf-8-sig解码。跨页需校验ID、revision、total_chars和连续offset；改版后重读。原服务没有语料快照token，不能保证跨页search在同一时刻完整。

stdio `ClientSession.call_tool` 返回CallToolResult；先检查isError，再解析结果。本机实际响应的structuredContent为None，dict JSON在content[0].text。直接FastMCP调用失败抛ToolError，stdio失败返回isError及文本；fetch缺失来源的错误没有稳定机器错误码。

## 权限、写入与冲突

search/fetch直接使用本地NoteStore，而非网站成员ACL。这是现有私有owner stdio边界；网页账户只读不能证明私有MCP只读。内容是参考数据，不是可以执行的指令或连接配置。

create/update通过既有 `local_headers(root)` 和loopback HTTP访问网站，先GET `/api/health`确认app=zhixu、返回data_dir且与服务根路径一致，再POST `/api/notes`或PUT `/api/note`。认证方式不由新集成层替换。未通过本地service身份的health只返回app，MCP拒绝无法核验数据目录的写入。

网站local service要求正确Bearer token、loopback Host且没有代理转发头；普通网页session另有CSRF及can_read/can_edit/can_create规则。源码中权限与原路径/symlink/junction检查继续生效，2B没有扩大任何账户授权。

update要求完整替换内容和原revision，在网站同一NoteStore锁内检查。HTTP409表示旧revision或目标同名冲突；必须重新读取、合并再提交。create同名拒绝覆盖。网络超时不代表未保存，应用应fetch核对结果；新薄层用确定性方法路径和精确内容对比恢复重复请求，绝不盲目覆盖不同版本。

## 已验证连接与环境

现有环境：`mcp==1.30.0`、`pydantic==2.14.2`。版本来自现有解释器包元数据，未安装或升级。

真实只读stdio探针使用现有 `mcp_server.py`，执行initialize、list_tools、search(query='',limit=1)、fetch首个返回文档的100字符，结果：

```json
{
  "server": "Zhixu Knowledge",
  "tools": ["search", "fetch", "create_note", "update_note"],
  "search_total": 16,
  "search_returned": 1,
  "search_structured_content_type": "NoneType",
  "fetch_id_matches": true,
  "fetch_revision_matches": true,
  "fetch_chars": 100,
  "fetch_has_next": true
}
```

16是探针当时文档总数，不是固定测试期望。没有输出正文或凭据，未调用生产create/update。此结果证明本地stdio读取可用，不证明ChatGPT远端隧道或工作区授权当前可用。

现有连接脚本使用 `.venv-mcp/Scripts/python.exe` 和 `mcp_server.py`，隧道profile为zhixu-knowledge，运行密钥经命名环境变量CONTROL_PLANE_API_KEY提供。脚本还会启动网站并初始化/更新隧道配置，本轮没有执行它。脱敏 `connections/mcp-servers.example.toml` 全部是注释示例，不是有效连接配置；没有导出用户级配置或认证信息。

## 隔离测试方式与限制

既有 `make_server()` 即使传临时库也会生成`.auth`数据库和service key。为满足本轮不修改`.auth`，新测试直接组合原KnowledgeServer / Handler / NoteStore，服务token只在测试内存中，临时stdio入口仅替换测试用local_headers供给。真实MCP解析、HTTP本地身份判定、路径检查、revision锁和文件保存逻辑全部复用原实现；无效测试token必须被拒绝。该测试入口不是生产认证方案，未修改原认证代码。

真实stdio方法保存、人类冲突和多策略SQLite集成结果以[最终实现报告](implementation-report.md)及其测试日志为准。手工全局配置、生产凭据状态、远端隧道重连、生产知识写入、账户变更都不属于本次审计的验证结论。
