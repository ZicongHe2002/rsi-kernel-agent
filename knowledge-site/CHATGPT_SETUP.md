# 让网页版 ChatGPT 读写知序知识库

网页与 ChatGPT 均可编辑、分类和保存 Markdown。ChatGPT 通过私有 MCP 连接搜索、读取、新建和更新文档。浏览器中尚未保存的草稿不会被读取。连接使用本目录的 `content/`，不扫描其他项目。

本机已准备 `mcp_server.py`、独立 `.venv-mcp` 环境、官方 Windows `tunnel-client` 和 `连接ChatGPT.cmd`。以下是首次连接步骤；已有连接在修改工具后刷新即可。

## 网站账号与 MCP 连接

网站的 `gpt` 账号是独立的浏览器账号，获授「全部文档／可阅读」，包含以后新增的已保存文档。使用可操作浏览器的 GPT 时，可打开网站公网链接、填写 `gpt`，再由 owner 批准该浏览器的访问申请。这个账号不能编辑文档、管理账号或查看回收站。

普通 ChatGPT 对话不会因为获得网站 ID 就自动完成网页登录。直接搜索、读取知识库时，请在对话中选择已安装的「我的知识库」MCP 插件。已有 MCP 连接仍有此前授权的全库读写工具，它独立于网站的 `gpt` 只读账号；网站账号的权限不会限制该 MCP 连接。私有隧道需持续运行，不能把普通网站首页当作 MCP Server URL。

## 已有连接升级到可编辑版本

重启 MCP 服务后，在 ChatGPT 的个人插件中打开 `zhixu-knowledge`，使用 **Refresh / 刷新** 更新工具列表。应能看到 `search`、`fetch`、`create_note`、`update_note`。若新增工具默认关闭，在详情页启用，然后新建对话选择该插件。

写入工具向 ChatGPT 声明为写操作，ChatGPT 会按当前确认设置处理。新建不会覆盖同名文档；更新必须携带读取时的 revision，过期版本会拒绝保存。GPT 与网页统一通过本地网站保存接口写入。长文档先读取所有分页再编辑，update_note 的 content 是完整的新正文。

例如：

> 请在我的知识库里新建“学习计划”，分类为“学习方法”，正文写下我接下来要学习的三个主题并保存。

> 请读取刚才的学习计划，增加一个“每周复盘”小节并保存。

保存后刷新知识库网页即可看到；网页中仍在编辑的旧版本会提示冲突。

## 1. 确认 ChatGPT 有接入入口

打开 <https://chatgpt.com/plugins>，找 **＋ → Add custom MCP server**，确认 **Connection** 下有 **Tunnel**。

账号或工作区权限会影响入口。若没有该入口或 Tunnel 选项，先确认账号支持情况；不要把 `127.0.0.1:8765` 当成公网 Server URL 填入。

## 2. 创建私有隧道

打开 <https://platform.openai.com/settings/organization/tunnels>，选择对应的个人 Platform organization，创建一个隧道，例如命名 `zhixu-knowledge`。

复制完整的 `tunnel_...` ID，并确认隧道关联你将使用的 ChatGPT workspace。只关联 Platform organization 可能导致 ChatGPT 下拉列表中看不到它。

创建隧道需要 Tunnels **Read + Manage**；运行与选择隧道需要 **Read + Use**。若页面提示 “Tunnels access required”，需要先由该组织管理员授予相应组织级权限。

## 3. 创建运行用 API key

打开 <https://platform.openai.com/settings/organization/api-keys>，在对应组织创建 runtime API key。它所属主体需要目标隧道的 Tunnels **Read + Use**。不要使用 Admin API key。

密钥可以保存在本机 `%USERPROFILE%\.config\ai-secrets\.env` 的 `CONTROL_PLANE_API_KEY=` 中，以后复用；也可以在下一步的隐藏输入框中临时输入。不要发到聊天或知识库文档。

## 4. 启动本机连接

在这个文件所在目录双击 **连接ChatGPT.cmd**：

1. 输入上面的完整 Tunnel ID。
2. 脚本优先使用进程已有的 `CONTROL_PLANE_API_KEY`，其次按需从共享配置加载这一项；未配置时才提示隐藏输入 runtime API key。
3. 程序启动本地网站、检查配置并启动隧道，自动打开本机状态页。
4. 确认状态为 ready、正常连接后，保持命令窗口打开。

也可以在 PowerShell 粘贴：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\fengluo\Desktop\rsi-kernel-agent\knowledge-site\connect-chatgpt.ps1"
```

脚本仅在当前进程中使用 key，隧道 profile 保存的是环境变量引用。Tunnel ID 和本地路径保存在 `.tunnel/`；再次运行可按回车沿用 ID。共享配置已填写 key 时无需重输；退出后恢复原进程环境。普通 OpenAI API 的 `OPENAI_API_KEY` 不会自动当作隧道凭据。停止用 Ctrl+C。仅仅看到 `doctor` 通过不代表 ChatGPT 已连通，仍需完成下面的真实读取验证。

网页服务和 MCP 都读取相同的已保存文件。GPT 读写时需要保持此连接程序运行；写入还要求本地网站运行，连接启动器会自动启动网站。若网站被停止，GPT 写入会报错，重新运行 `启动知识库.cmd` 即可。

## 5. 在 ChatGPT 添加知识库

回到 <https://chatgpt.com/plugins>，选择 **＋ → Add custom MCP server**，填写：

| 字段 | 内容 |
| --- | --- |
| Name | 我的知识库 |
| Description | 搜索、读取、新建和编辑我的 Markdown 知识笔记 |
| Connection | Tunnel |
| Tunnel | 选择或粘贴第 2 步的完整 ID |
| Authentication | No authentication |

这里的 No authentication 指本机 MCP 无额外 OAuth 登录；隧道本身仍受 OpenAI 账号、组织和工作区权限控制。确认页面提示后选择 **Create as a plugin**，按页面要求安装。

工具列表应该有 `search`、`fetch`、`create_note` 和 `update_note`；前两者只读，后两者写入。若工具发现失败，检查隧道程序仍在运行、状态页就绪、工作区关联和 Read + Use 权限。

## 6. 验证实际读取

在知识库网页保存一篇测试笔记，例如正文包含你新写的一句独特文字。新建 ChatGPT 对话，输入 `@` 选择「我的知识库」，然后发：

> 请使用“我的知识库”搜索我刚才保存的测试笔记，读取正文，给出文档标题、分类和那句独特文字。

展开工具调用，确认先 `search` 再 `fetch`，返回的内容与已保存文件一致。这一步成功才表示接入完成。以后网页保存修改后，ChatGPT 下一次工具读取会拿到最新文件；旧回答不会自动更新。

## 本机维护

网站本身仍不需要安装依赖。可选 MCP 环境依赖在 `requirements-mcp.txt`，重建时使用 Python 3.10+ 创建独立环境后安装该文件。

当前使用官方 MCP Python SDK 1.30.0。隧道客户端从 OpenAI 官方发布下载，已核对发布的 SHA256；版本和校验记录位于 `tools/tunnel-client/release.json`。更新从官方 latest 页面或 Platform 页面下载：<https://github.com/openai/tunnel-client/releases/latest>。

本机协议、读写与版本冲突测试（使用临时文档）：

```powershell
.\.venv-mcp\Scripts\python.exe -m unittest -v test_mcp.py
```

官方步骤参考（核对日期：2026-10-31）：

- <https://developers.openai.com/api/docs/guides/secure-mcp-tunnels>
- <https://developers.openai.com/apps-sdk/deploy/connect-chatgpt/>
