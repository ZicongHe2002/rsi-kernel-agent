# Phase 1 验证报告

> 2026-11-11｜文档与静态集成审计。明细：[phase1-validation.json](phase1-validation.json)。不表示网站测试、Colab连通或TPU基线通过。

## 已执行检查

| 检查 | 证据与范围 |
|---|---|
| 原文保留 | 修改前16份文档/配置原始字节快照；逐份SHA-256核对，位置见[before.json](phase1-2026-11-11/before.json) |
| 合并语义 | 独立审查逐段比对原方法页与canonical第14–24节；独有搜索、源码阅读、适用性、方法卡、3案例、2图和4来源保留；旧路径为迁移说明 |
| 来源保留 | 修改前正文外部Markdown URL在当前文档中均可找到；缺失数0。改编号保留MS1–MS4和原核查日期 |
| 相对链接 | 扫描当前正文、维护说明、现有归档/回收站、bootstrap的Markdown链接，排除代码示例；回收站以metadata.id解析；最终明细中broken_links为空 |
| 图表 | 23个当前Mermaid源码块均由既有Mermaid11.17.2在隔离headless Chrome解析并生成有效SVG，23通过/0失败；没有打开真实网站或认证状态 |
| 图片与依赖 | Phase0清单中的20张QA PNG字节/哈希均不变；104个Mermaid依赖文件大小/哈希与既有manifest一致 |
| 公式 | 资源单位换算、4种流水线边界的独立逐块排程、物理分配去别名、重叠区间并集、配对几何speedup共8项核对通过；是算术自洽性，不是TPU模型校准 |
| 原仓库只读 | MLA清单597个文件、Colab清单25个文件与额外Windows指南再次逐文件哈希相同；未导入Kernel或执行CLI |
| 语法 | 网站17个顶层Python文件AST解析通过；MLA审计453个Python文件仅AST解析通过，无导入/执行 |
| 忽略规则提案 | 36个路径样例覆盖秘密/私钥、认证DB/WAL/SHM、环境、raw trace、IR和保留文档/图片/示例配置，均符合预期；提案没有激活 |
| Git跟踪 | 只读解析本仓库index仅LICENSE，未见.auth；Git命令不可用，不能声称完整git status/历史验证 |

## 检查方式与限制

文档修改先保存原文，整批检查SHA-256后写入并复核；API未认证读取返回401，遵守“不读取.auth”要求，没有取token、改权限或绕过认证。后续审查的小修使用精确补丁。原文快照可恢复，未删除原归档。

Mermaid最初在无DOM的Node环境调用parse，22个flowchart因依赖DOMPurify的`addHook`失败，1个sequence通过；这是浏览器库环境依赖，不是文档语法结论。随后在现有Chrome隔离临时profile、阻断网络并加载网站已有classic fallback库，23图全部parse/render成功。没有修改库、降低网站安全级别或安装依赖；此验证不等于真实网站完整交互/截图视觉验收。

相对链接检查覆盖有效Markdown链接语法和目标文件存在，不把代码围栏内说明路径当死链。回收站旧Analyzer说明的出链可按原ID解析；不声称旧在线URL有重定向。外部URL只清点及保留，未逐一联网重验。图像验证证明已存20PNG未改，不证明缺失的原始参考图片已找回。

.gitignore只提供待采用文件 [gitignore.proposed](../access/gitignore.proposed)，未写入根.gitignore；36例为针对实际使用模式的离线匹配验证，**不是native `git check-ignore`**。后续有Git时还应在隔离索引验证ignore/negation及实际tracked状态；不自动untrack、删除或掩盖已有文件。通配`.pem`可能覆盖公开证书，未来确需跟踪的非秘密证书或trace/IR测试夹具应另加明确例外，不把真实私钥加入例外。

网站原有77个测试方法未执行；没有修改网站运行逻辑，本轮检查不需要启动测试服务或创建认证状态。Kernel/CLI仓库测试、实时连接、TPU、A09运行前提、timing统计/晋升和环境兼容性均未验证。Phase2计划中的默认参数和路径仍是待审批/待确认值。

秘密模式检查仅针对生成文档/清单与自有文本，以路径/类别报告；`.auth`、Colab认证/会话/settings/history/log、虚拟环境和运行日志不读取。不能给出“全仓库无秘密”或“凭据已泄露”的断言。

## 可追溯产物

- [文档/图片/快照清单](site-inventory.json)
- [来源清单](source-manifest.json)
- [前后哈希](phase1-changes.json)
- [MLA原仓库清单](mla-source-manifest.json)
- [Colab原仓库清单](colab-source-manifest.json)

一次性编辑/验证脚本及详细浏览器结果位于现有忽略目录 `.artifacts/phase1-2026-11-11/`，属于文档维护工具，不是RSI实验实现。

## 命令与退出码

以下工作目录为 `C:/Users/fengluo/Desktop/rsi-kernel-agent`。只使用已有解释器/浏览器/依赖，没有安装或升级。

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B '.\knowledge-site\.artifacts\phase1-2026-11-11\validate_docs.py'
& 'C:\Users\fengluo\AppData\Local\OpenAI\Codex\runtimes\cua_node\76f5b5935a63c7ba\bin\node.exe' '.\knowledge-site\.artifacts\phase1-2026-11-11\check_mermaid_browser.cjs'
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B '.\knowledge-site\.artifacts\phase1-2026-11-11\finalize.py' secrets
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B '.\knowledge-site\.artifacts\phase1-2026-11-11\finalize.py' manifest
```

最终上述验证均退出0。首次文档检查在维护报告尚未创建时准确报告6个缺失目标、退出1；补齐报告/清单后完整重跑通过。无DOM的初次Mermaid检查退出1，原因和隔离浏览器验证结果已在上文说明。不将失败尝试作为通过记录。`reconcile.py`、`edit_docs.py`、`edit_contracts.py`与`finalize.py weekly`为本轮带快照/哈希检查的维护操作，执行退出0；不要把这些编辑命令当成可反复执行的运行时功能。
