"""MCP read/write integration tests. All notes are synthetic temporary fixtures."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server import build_server
from server import make_server
from local_access import local_headers


class MCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "content"
        self.root.mkdir()
        self.long_id = "机器学习/TPU/执行笔记.md"
        self.long_text = "# 执行笔记\r\n\r\n" + "同步信号 αβ 🧠：等待 DMA 完成。\n" * 2200 + "最后一行  "
        self.fixtures = {
            self.long_id: self.long_text.encode("utf-8"),
            "编程/Python.md": "# Python\n在正文讨论 TPU，文档分类为编程。\n".encode("utf-8"),
            "欢迎.md": b"\xef\xbb\xbf# Welcome\nSynthetic fixture only.\n",
            "settings.json": b'{"not_a_note": true}',
            ".hidden.md": b"This hidden file must not be exposed.",
            ".private/secret.md": b"This hidden directory must not be exposed.",
        }
        for index, (relative, data) in enumerate(self.fixtures.items()):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
        (self.workspace / "outside.md").write_text("Outside the knowledge root", encoding="utf-8")
        self.before = self.snapshot()
        self.app = build_server(self.root)

    def snapshot(self):
        return {
            path.relative_to(self.root).as_posix():
                ("file", hashlib.sha256(path.read_bytes()).hexdigest()) if path.is_file() else ("directory", None)
            for path in self.root.rglob("*")
        }

    def tearDown(self):
        try:
            self.assertEqual(self.snapshot(), self.before, "MCP calls must not change files or directories")
        finally:
            self.temporary.cleanup()

    async def call(self, name, arguments):
        result = await self.app.call_tool(name, arguments)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "text")
        return json.loads(result[0].text)

    async def test_tool_table_distinguishes_read_and_write_permissions(self):
        tools = await self.app.list_tools()
        self.assertEqual({tool.name for tool in tools}, {"search", "fetch", "create_note", "update_note"})
        for tool in tools:
            self.assertEqual(tool.annotations.readOnlyHint, tool.name in {"search", "fetch"})
            self.assertEqual(tool.annotations.destructiveHint, tool.name == "update_note")
            self.assertEqual(tool.annotations.idempotentHint, tool.name in {"search", "fetch"})
            self.assertFalse(tool.annotations.openWorldHint)
            self.assertNotIn("root", tool.inputSchema["properties"])
            self.assertNotIn("site_port", tool.inputSchema["properties"])
        self.assertEqual({tool.name: set(tool.inputSchema["properties"]) for tool in tools}, {
            "search": {"query", "offset", "limit"}, "fetch": {"id", "offset", "limit"},
            "create_note": {"title", "content", "category"},
            "update_note": {"id", "revision", "content", "title", "category"},
        })

    async def test_chinese_body_and_category_search_with_pagination(self):
        body = await self.call("search", {"query": "同步信号"})
        self.assertEqual([note["id"] for note in body["results"]], [self.long_id])
        category = await self.call("search", {"query": "机器学习/TPU"})
        self.assertEqual([note["id"] for note in category["results"]], [self.long_id])
        first = await self.call("search", {"query": "tpu", "limit": 1})
        self.assertEqual(first["total"], 2)
        self.assertEqual(first["results"][0]["id"], "编程/Python.md")
        self.assertEqual(first["next_offset"], 1)
        second = await self.call("search", {"query": "tpu", "offset": first["next_offset"], "limit": 1})
        self.assertEqual(second["results"][0]["id"], self.long_id)
        self.assertIsNone(second["next_offset"])
        all_notes = await self.call("search", {})
        self.assertEqual(all_notes["total"], 3)
        self.assertEqual({n["id"] for n in all_notes["results"]}, {self.long_id, "编程/Python.md", "欢迎.md"})
        self.assertEqual((await self.call("search", {"query": "不存在的内容"}))["total"], 0)
        beyond = await self.call("search", {"offset": 999})
        self.assertEqual(beyond["results"], [])
        self.assertIsNone(beyond["next_offset"])

    async def test_fetch_pages_reconstruct_complete_unicode_and_exact_revision(self):
        parts, offset = [], 0
        expected_revision = hashlib.sha256(self.fixtures[self.long_id]).hexdigest()
        while True:
            page = await self.call("fetch", {"id": self.long_id, "offset": offset, "limit": 9999})
            self.assertEqual(page["id"], self.long_id)
            self.assertEqual(page["title"], "执行笔记")
            self.assertEqual(page["category"], "机器学习/TPU")
            self.assertEqual(page["offset"], offset)
            self.assertEqual(page["total_chars"], len(self.long_text))
            self.assertEqual(page["revision"], expected_revision)
            self.assertLessEqual(len(page["content"]), 9999)
            parts.append(page["content"])
            if page["next_offset"] is None:
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), self.long_text)
        bom = await self.call("fetch", {"id": "欢迎.md"})
        self.assertEqual(bom["content"], "# Welcome\nSynthetic fixture only.\n")
        self.assertEqual(bom["revision"], hashlib.sha256(self.fixtures["欢迎.md"]).hexdigest())
        beyond = await self.call("fetch", {"id": self.long_id, "offset": len(self.long_text) + 1})
        self.assertEqual(beyond["content"], "")
        self.assertIsNone(beyond["next_offset"])

    async def test_bad_paths_unknown_tools_and_invalid_pagination_are_rejected(self):
        for note_id in ["../outside.md", "/outside.md", "C:/outside.md", "a\\b.md", ".hidden.md",
                        ".private/secret.md", "settings.json", "missing.md", "CON.md"]:
            with self.subTest(note_id=note_id), self.assertRaises(ToolError):
                await self.app.call_tool("fetch", {"id": note_id})
        for name in ["save", "delete", "add_category", "unknown"]:
            with self.subTest(tool=name), self.assertRaises(ToolError):
                await self.app.call_tool(name, {})
        for name, arguments in [
            ("search", {"offset": -1}), ("search", {"limit": 0}), ("search", {"limit": 51}),
            ("search", {"query": "x" * 1001}), ("search", {"offset": "not-an-integer"}),
            ("fetch", {"id": self.long_id, "offset": -1}), ("fetch", {"id": self.long_id, "limit": 0}),
            ("fetch", {"id": self.long_id, "limit": 24001}), ("fetch", {}),
        ]:
            with self.subTest(tool=name, arguments=arguments), self.assertRaises(ToolError):
                await self.app.call_tool(name, arguments)
        missing = self.workspace / "does-not-exist"
        with self.assertRaises(RuntimeError):
            build_server(missing)
        self.assertFalse(missing.exists(), "A missing root must not be created")

    async def test_real_stdio_initialization_listing_and_error_protocol(self):
        entrypoint = self.workspace / "temporary_mcp_entry.py"
        entrypoint.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
            "from mcp_server import build_server\n"
            f"build_server({str(self.root)!r}).run(transport='stdio')\n",
            encoding="utf-8",
        )
        parameters = StdioServerParameters(
            command=sys.executable, args=["-u", str(entrypoint)],
            env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )

        async def probe():
            with (self.workspace / "stdio-stderr.log").open("w", encoding="utf-8") as errors:
                async with stdio_client(parameters, errlog=errors) as (read, write):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        self.assertEqual(initialized.serverInfo.name, "Zhixu Knowledge")
                        tools = await session.list_tools()
                        self.assertEqual({tool.name for tool in tools.tools}, {"search", "fetch", "create_note", "update_note"})
                        searched = await session.call_tool("search", {"query": "同步信号"})
                        self.assertFalse(searched.isError)
                        self.assertEqual(json.loads(searched.content[0].text)["results"][0]["id"], self.long_id)
                        for name, arguments in [("unknown", {}), ("fetch", {"id": "../outside.md"}),
                                                ("search", {"limit": 0})]:
                            result = await session.call_tool(name, arguments)
                            self.assertTrue(result.isError)
                            self.assertTrue(result.content)

        await asyncio.wait_for(probe(), timeout=20)


class MCPWriteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "content"
        self.root.mkdir()
        self.outside = self.workspace / "outside.md"
        self.outside.write_bytes(b"outside must remain unchanged")
        self.web = self.workspace / "web"
        self.web.mkdir()
        self.server = make_server(self.root, 0, self.web)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.running = True
        self.app = build_server(self.root, site_port=self.port)

    def stop_site(self):
        if self.running:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.running = False

    def tearDown(self):
        self.stop_site()
        try:
            self.assertEqual(self.outside.read_bytes(), b"outside must remain unchanged")
        finally:
            self.temporary.cleanup()

    def snapshot(self, root=None):
        root = root or self.root
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                for path in root.rglob("*")}

    def http(self, method, path, data=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json", **local_headers(self.root)})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    async def call(self, name, arguments):
        content = await self.app.call_tool(name, arguments)
        return json.loads(content[0].text)

    @asynccontextmanager
    async def stdio(self):
        entrypoint = self.workspace / "temporary_writable_mcp.py"
        entrypoint.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
            "from mcp_server import build_server\n"
            f"build_server({str(self.root)!r}, site_port={self.port}).run(transport='stdio')\n",
            encoding="utf-8",
        )
        parameters = StdioServerParameters(command=sys.executable, args=["-u", str(entrypoint)],
                                          env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        with (self.workspace / "write-stdio-stderr.log").open("w", encoding="utf-8") as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                    await session.initialize()
                    yield session

    async def test_real_stdio_writes_are_visible_in_website_and_reject_stale_revision(self):
        async with self.stdio() as session:
            body = "# MCP 新建\r\n\r\n中文正文 🧠\n尾部空格  "
            result = await session.call_tool("create_note", {"title": "协作笔记", "category": "测试/TPU", "content": body})
            self.assertFalse(result.isError, result)
            created = json.loads(result.content[0].text)
            self.assertNotIn("content", created)
            self.assertEqual(created["id"], "测试/TPU/协作笔记.md")
            self.assertEqual(created["revision"], hashlib.sha256(body.encode("utf-8")).hexdigest())
            self.assertEqual((self.root / created["id"]).read_bytes(), body.encode("utf-8"))
            status, web_note = self.http("GET", "/api/note?" + urlencode({"id": created["id"]}))
            self.assertEqual(status, 200)
            self.assertEqual(web_note["content"], body)
            self.assertIn(created["id"], [note["id"] for note in self.http("GET", "/api/notes")[1]["notes"]])
            updated_body = body + "\nMCP 增补"
            result = await session.call_tool("update_note", {"id": created["id"], "revision": created["revision"], "content": updated_body})
            self.assertFalse(result.isError, result)
            updated = json.loads(result.content[0].text)
            self.assertNotIn("content", updated)
            self.assertEqual(updated["id"], created["id"])
            fetched = await session.call_tool("fetch", {"id": updated["id"]})
            self.assertEqual(json.loads(fetched.content[0].text)["content"], updated_body)
            status, current = self.http("GET", "/api/note?" + urlencode({"id": updated["id"]}))
            self.assertEqual(status, 200)
            status, web_changed = self.http("PUT", "/api/note", dict(current, content="网页后来保存的版本"))
            self.assertEqual(status, 200)
            before_stale = self.snapshot()
            stale = await session.call_tool("update_note", {"id": updated["id"], "revision": updated["revision"], "content": "不能覆盖网页新版本"})
            self.assertTrue(stale.isError)
            self.assertEqual(self.snapshot(), before_stale)
            self.assertEqual((self.root / updated["id"]).read_text(encoding="utf-8"), web_changed["content"])

    async def test_update_rename_and_move_and_duplicate_rejection(self):
        first = await self.call("create_note", {"title": "第一篇", "content": "initial"})
        self.assertEqual(first["category"], "未分类")
        second = await self.call("create_note", {"title": "第二篇", "category": "归档", "content": "other"})
        moved = await self.call("update_note", {"id": first["id"], "revision": first["revision"], "content": "moved",
                                               "title": "新标题", "category": "归档/实验"})
        self.assertEqual(moved["id"], "归档/实验/新标题.md")
        self.assertFalse((self.root / first["id"]).exists())
        self.assertEqual(self.http("GET", "/api/note?" + urlencode({"id": moved["id"]}))[1]["content"], "moved")
        before = self.snapshot()
        for name, arguments in [
            ("create_note", {"title": "第二篇", "category": "归档", "content": "overwrite"}),
            ("update_note", {"id": moved["id"], "revision": moved["revision"], "content": "overwrite",
                             "title": second["title"], "category": second["category"]}),
        ]:
            with self.subTest(tool=name), self.assertRaises(ToolError):
                await self.app.call_tool(name, arguments)
            self.assertEqual(self.snapshot(), before)

    async def test_invalid_write_paths_revision_and_content_size_leave_files_unchanged(self):
        note = await self.call("create_note", {"title": "Protected", "content": "unchanged"})
        before = self.snapshot()
        invalid = [
            ("create_note", {"title": "../outside", "content": "escape"}),
            ("create_note", {"title": "escape", "category": "../outside", "content": "escape"}),
            ("create_note", {"title": "CON", "content": "reserved"}),
            ("create_note", {"title": "big", "content": "x" * 1_000_001}),
            ("create_note", {"title": "serialized-big", "content": "\x00" * 800_000}),
        ]
        for note_id in ["../outside.md", "settings.json", ".hidden.md", "C:/outside.md", "a\\b.md"]:
            invalid.append(("update_note", {"id": note_id, "revision": note["revision"], "content": "escape"}))
        for revision in ["", "a" * 63, "A" * 64, "g" * 64]:
            invalid.append(("update_note", {"id": note["id"], "revision": revision, "content": "invalid revision"}))
        invalid.append(("update_note", {"id": note["id"], "content": "missing revision"}))
        for name, arguments in invalid:
            with self.subTest(tool=name, title=arguments.get("title"), id=arguments.get("id")), self.assertRaises(ToolError):
                await self.app.call_tool(name, arguments)
            self.assertEqual(self.snapshot(), before)

    async def test_wrong_library_and_stopped_website_refuse_writes(self):
        different_root = self.workspace / "different-library"
        different_root.mkdir()
        wrong_site = build_server(different_root, site_port=self.port)
        before, different_before = self.snapshot(), self.snapshot(different_root)
        with self.assertRaisesRegex(ToolError, "The website is serving a different knowledge directory; no note was saved"):
            await wrong_site.call_tool("create_note", {"title": "wrong library", "content": "must not write"})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.snapshot(different_root), different_before)
        self.stop_site()
        with self.assertRaises(ToolError):
            await self.app.call_tool("create_note", {"title": "site offline", "content": "must not write"})
        self.assertEqual(self.snapshot(), before)

    async def test_missing_local_auth_cannot_verify_directory_and_refuses_write(self):
        before = self.snapshot()
        with patch("mcp_server.local_headers", return_value={}):
            with self.assertRaisesRegex(ToolError, "Could not verify the website's knowledge directory") as error:
                await self.app.call_tool("create_note", {"title": "unverified library", "content": "must not write"})
        self.assertIn("restart the website and MCP connection", str(error.exception))
        self.assertNotIn("different knowledge directory", str(error.exception))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
