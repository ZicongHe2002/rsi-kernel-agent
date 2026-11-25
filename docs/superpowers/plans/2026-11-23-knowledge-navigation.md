# Knowledge Navigation Implementation Plan

> For agentic workers: execute the three scoped tasks below with isolated file ownership and a final independent review. The user has requested implementation.

**Goal:** 删除知识库 RSI 资料分类，让整体流程与模块分工作为唯一主入口，五组件及详细专题可直接展开访问。
**Architecture:** 保持文档 ID 稳定；用站点 navigation.json 声明展示层级，后端按实际文档与权限过滤，前端呈现可折叠文档树。资料删除走带 revision 的回收站 API。
**Tech Stack:** Python HTTP server、原生 JavaScript/CSS、unittest、jsdom。
**Spec:** 用户 2026-11-23 本次请求。

## Constraints and review focus
- 仅删除 content/RSI 内资料；可从回收站恢复。禁止误删 rsi-kernel-agent 项目。
- 主入口 → 五个组件 → 详细专题；参考进展和历史资料折叠，搜索与待办继续可用。
- 不泄露未授权父文档名称；允许已授权子文档独立显示。
- 新增但不在目录配置内的文档仍可找到；显式链接/历史导航继续工作。
- 保存或删除使用最新 revision；用户新增内容不得被覆盖。

## Task 1 — Backend
- [x] 为真实 HTTP listing 写树排序、受限用户、父级不可见、文档删除、无/坏配置行为测试，先确认失败。
- [x] 新增可选目录加载器；GET /api/notes 返回 navigation 数组，节点为 id(可选)、label、children。
- [x] 跑新增测试和知识库后端相关回归。

## Task 2 — Frontend
- [x] 用 DOM 测试证明当前缺少主入口选择、组件折叠/跳转与未配置文档回退。
- [x] 渲染有层级的目录、友好标题和明确选中态；空地址及学习方法分类默认打开主文；显式链接保留。
- [x] 验证搜索、回收站、未授权部分目录及新增普通笔记行为。

## Task 3 — Content and release
- [x] 备份资料与文档；11 篇 content/RSI 文档通过 API 移入回收站，仅清理确认空目录。
- [x] 写入目录配置，更新主文/辅助索引入口，历史路线图标为历史；保留现有正文与链接。
- [x] 独立代码审查，运行知识库验证；重载网站，核对真实 API；权限目录、主页和组件跳转由 DOM/HTTP 自动化验证，实际浏览器视觉检查未完成。
- [x] 将已确认结果写入本周完成记录。
