import http.client
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode

from server import make_server
from local_access import local_headers


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<h1>Knowledge</h1>", encoding="utf-8")
        (self.web / "note-links.js").write_bytes(b"window.noteLinks = {};\n")
        (self.web / "vendor").mkdir()
        (self.web / "vendor" / "mermaid.js").write_bytes(b"window.mermaid = {};\n")
        self.start()

    def start(self):
        self.server = make_server(self.root / "content", 0, self.web)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.temporary.cleanup()

    def request(self, method, path, data=None, headers=None, raw=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"Content-Type": "application/json", **local_headers(self.root / "content")}
        request_headers.update(headers or {})
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        connection.request(method, path, body, request_headers)
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        connection.close()
        return status, payload if raw else json.loads(payload), response_headers

    def create(self, title="测试", category="未分类", content="# 标题\n\n正文\n"):
        status, note, _ = self.request("POST", "/api/notes", {"title": title, "category": category, "content": content})
        self.assertEqual(status, 201, note)
        return note

    def ensure_task_lists(self):
        status, lists, _ = self.request("POST", "/api/task-lists/ensure", {})
        self.assertEqual(status, 200, lists)
        return lists

    def test_task_lists_are_created_only_by_explicit_post(self):
        self.assertEqual(self.request("GET", "/api/notes")[1]["notes"], [])
        self.assertEqual(self.request("GET", "/api/task-lists/ensure")[0], 404)
        self.assertEqual(self.request("POST", "/api/task-lists/ensure", {}, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(list((self.root / "content").iterdir()), [])
        with mock.patch("server.current_week_start", return_value=date(2026, 11, 2)):
            lists = self.ensure_task_lists()
        self.assertEqual(lists["week_start"], "2026-11-02")
        self.assertEqual(lists["weekly"]["id"], "待办清单/一周待办 · 2026-11-02.md")
        self.assertEqual(lists["general"]["id"], "待办清单/长期待办.md")
        self.assertEqual(set(lists["created"]), {lists["weekly"]["id"], lists["general"]["id"]})
        weekly = lists["weekly"]["content"]
        headings = [line for line in weekly.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, [
            "## 本周待办", "## 完成记录",
        ])
        self.assertEqual(lists["general"]["content"], "# 长期待办\n\n## 待办事项\n\n## 完成记录\n")

    def test_task_lists_are_idempotent_concurrent_and_preserve_original_bytes(self):
        with mock.patch("server.current_week_start", return_value=date(2026, 11, 2)):
            with ThreadPoolExecutor(max_workers=4) as pool:
                initial = list(pool.map(lambda _: self.ensure_task_lists(), range(8)))
            self.assertEqual(sum(len(response["created"]) for response in initial), 2)
            lists = initial[0]
            self.assertTrue(all(response["weekly"] == lists["weekly"] and response["general"] == lists["general"] for response in initial))
            originals = {}
            for kind in ("weekly", "general"):
                note_id = lists[kind]["id"]
                target = self.root / "content" / note_id
                raw = b"\xef\xbb\xbf" + (f"# {kind} 用户原文\r\n\r\n- [x] 已完成  \r\n").encode("utf-8")
                target.write_bytes(raw)
                os.utime(target, (1700000000, 1700000000))
                originals[kind] = (raw, target.stat().st_mtime_ns)
            current = self.ensure_task_lists()
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(lambda _: self.ensure_task_lists(), range(8)))
            self.assertTrue(all(response == current for response in responses))
            self.assertEqual(current["created"], [])
            self.stop()
            self.start()
            self.assertEqual(self.ensure_task_lists(), current)
        self.assertEqual(len(self.request("GET", "/api/notes")[1]["notes"]), 2)
        for kind, (raw, mtime) in originals.items():
            target = self.root / "content" / lists[kind]["id"]
            self.assertEqual(target.read_bytes(), raw)
            self.assertEqual(target.stat().st_mtime_ns, mtime)

    def test_task_lists_roll_over_on_shanghai_monday_and_keep_history(self):
        with mock.patch("server.datetime", wraps=datetime) as clock:
            clock.now.side_effect = lambda tz: datetime(2026, 11, 1, 15, 59, tzinfo=timezone.utc).astimezone(tz)
            previous = self.ensure_task_lists()
            self.assertEqual(previous["week_start"], "2026-10-26")
            saved = self.request("PUT", "/api/note", dict(previous["weekly"], content="- [x] 上周安排与完成记录\n"))[1]
            clock.now.side_effect = lambda tz: datetime(2026, 11, 1, 16, 0, tzinfo=timezone.utc).astimezone(tz)
            current = self.ensure_task_lists()
            self.assertEqual(current["week_start"], "2026-11-02")
            self.assertEqual(current["general"], previous["general"])
            self.assertEqual(current["created"], [current["weekly"]["id"]])
            self.assertEqual(self.ensure_task_lists()["created"], [])
        self.assertEqual(self.request("GET", "/api/note?" + urlencode({"id": saved["id"]}))[1], saved)
        self.assertEqual(len(self.request("GET", "/api/notes")[1]["notes"]), 3)

    def test_deleted_task_lists_stay_deleted_without_blocking_new_weeks(self):
        with mock.patch("server.current_week_start", return_value=date(2026, 11, 2)):
            previous = self.ensure_task_lists()
        deleted_general = self.request("DELETE", "/api/note", previous["general"])[1]
        with mock.patch("server.current_week_start", return_value=date(2026, 11, 9)):
            current = self.ensure_task_lists()
            self.assertEqual(current["general"], deleted_general)
            self.assertEqual(current["created"], [current["weekly"]["id"]])
            deleted_weekly = self.request("DELETE", "/api/note", current["weekly"])[1]
            self.stop()
            self.start()
            deleted = self.ensure_task_lists()
            self.assertEqual(deleted["created"], [])
            self.assertEqual(deleted["weekly"], deleted_weekly)
            self.assertEqual(deleted["general"], deleted_general)
            self.assertFalse((self.root / "content" / current["weekly"]["id"]).exists())
            self.assertFalse((self.root / "content" / current["general"]["id"]).exists())
            status, restored, _ = self.request("POST", "/api/trash/restore", {"trash_id": deleted_general["trash_id"]})
            self.assertEqual(status, 200)
            self.assertEqual(self.ensure_task_lists()["general"], restored)
        self.assertEqual(self.request("GET", "/api/note?" + urlencode({"id": previous["weekly"]["id"]}))[1], previous["weekly"])

    def test_task_lists_return_latest_deleted_version_and_keep_snapshots(self):
        with mock.patch("server.current_week_start", return_value=date(2026, 11, 2)):
            lists = self.ensure_task_lists()
            self.request("DELETE", "/api/note", lists["general"])
            replacement = self.create(title="长期待办", category="待办清单", content="新版本长期任务\n")
            latest = self.request("DELETE", "/api/note", replacement)[1]
            self.assertEqual(self.ensure_task_lists()["general"], latest)
            self.assertEqual(len(self.request("GET", "/api/trash")[1]["notes"]), 2)
            self.assertFalse((self.root / "content" / replacement["id"]).exists())

    def test_damaged_deleted_task_list_is_not_silently_recreated(self):
        with mock.patch("server.current_week_start", return_value=date(2026, 11, 2)):
            lists = self.ensure_task_lists()
            deleted = self.request("DELETE", "/api/note", lists["general"])[1]
            archived = self.root / "content" / ".trash" / deleted["trash_id"] / "note.md"
            archived.write_bytes(b"externally modified snapshot")
            self.assertEqual(self.request("POST", "/api/task-lists/ensure", {})[0], 409)
            self.assertFalse((self.root / "content" / deleted["id"]).exists())
            self.assertEqual(archived.read_bytes(), b"externally modified snapshot")

    def test_exact_markdown_persists_after_restart(self):
        body = "---\r\ntags: [TPU]\r\n---\r\n# 中文\n\n```python\nx = 1\n```\n\n最后一行  "
        note = self.create(content=body)
        self.assertEqual(note["content"], body)
        self.assertEqual((self.root / "content" / note["id"]).read_bytes(), body.encode("utf-8"))
        self.stop()
        self.start()
        status, loaded, _ = self.request("GET", "/api/note?" + urlencode({"id": note["id"]}))
        self.assertEqual(status, 200)
        self.assertEqual(loaded, note)
        status, raw, headers = self.request("GET", "/api/export?" + urlencode({"id": note["id"]}), raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(raw, body.encode("utf-8"))
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])

    def test_nested_category_move_and_search(self):
        self.assertEqual(self.request("POST", "/api/categories", {"name": "计算/TPU"})[0], 201)
        note = self.create(content="HLO SyncFlag details")
        updated = dict(note, title="同步", category="计算/TPU", content="HLO SyncFlag details\n补充")
        status, moved, _ = self.request("PUT", "/api/note", updated)
        self.assertEqual(status, 200, moved)
        self.assertEqual(moved["id"], "计算/TPU/同步.md")
        self.assertFalse((self.root / "content" / note["id"]).exists())
        self.assertNotEqual(note["revision"], moved["revision"])
        listing = self.request("GET", "/api/notes?q=syncflag")[1]
        self.assertEqual([item["id"] for item in listing["notes"]], [moved["id"]])
        self.assertIn("计算/TPU", listing["categories"])
        self.assertEqual(self.request("GET", "/api/notes?q=notpresent")[1]["notes"], [])

    def test_stale_updates_and_name_collisions_preserve_data(self):
        first = self.create(title="first", content="before")
        second = self.create(title="second", content="other")
        status, current, _ = self.request("PUT", "/api/note", dict(first, content="after"))
        self.assertEqual(status, 200)
        self.assertEqual(self.request("PUT", "/api/note", dict(first, content="stale"))[0], 409)
        self.assertEqual(self.request("PUT", "/api/note", dict(current, title=second["title"]))[0], 409)
        self.assertEqual(self.request("POST", "/api/notes", dict(second, content="overwrite"))[0], 409)
        self.assertEqual((self.root / "content" / first["id"]).read_text(), "after")
        self.assertEqual((self.root / "content" / second["id"]).read_text(), "other")

    def test_traversal_hidden_and_windows_paths_rejected(self):
        for note_id in ["../secret.md", ".private/a.md", "C:/secret.md", "a\\b.md", "/a.md", "CON.md", "a/../b.md"]:
            with self.subTest(note_id=note_id):
                self.assertEqual(self.request("GET", "/api/note?" + urlencode({"id": note_id}))[0], 400)
        for category in ["../outside", "a//b", "a\\b", "LPT1", ".hidden", "ends."]:
            self.assertEqual(self.request("POST", "/api/categories", {"name": category})[0], 400)

    def test_static_allowlist_and_origins(self):
        self.assertEqual(self.request("GET", "/", raw=True)[0], 200)
        self.assertEqual(self.request("GET", "/note-links.js", raw=True)[1], b"window.noteLinks = {};\n")
        status, script, headers = self.request("GET", "/vendor/mermaid.js", raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(script, b"window.mermaid = {};\n")
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        for path in ["/server.py", "/content/private.md", "/../server.py", "/%2e%2e/server.py", "/web/index.html",
                     "/vendor/VERSIONS.json", "/vendor/../server.py"]:
            self.assertEqual(self.request("GET", path)[0], 404)
        payload = {"title": "denied", "category": "未分类", "content": ""}
        self.assertEqual(self.request("POST", "/api/notes", payload, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/api/notes", headers={"Host": "evil.test"})[0], 403)
        self.assertEqual(self.request("POST", "/api/notes", payload, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/notes", payload, {"Origin": f"http://127.0.0.1:{self.server.server_port}"})[0], 201)

    def test_shutdown_requires_launch_secret(self):
        self.assertEqual(self.request("GET", "/api/health")[1]["app"], "zhixu")
        self.assertEqual(self.request("POST", "/api/shutdown", {"token": "guessed"})[0], 403)
        self.server.shutdown_token = "test-only-secret"
        health = self.request("GET", "/api/health")[1]
        self.assertIsInstance(health["pid"], int)
        self.assertNotIn("test-only-secret", json.dumps(health))
        self.assertEqual(self.request("POST", "/api/shutdown", {"token": "guessed"})[0], 403)
        self.assertEqual(self.request("POST", "/api/shutdown", {"token": "test-only-secret"})[0], 200)

    def test_existing_bom_root_file_and_symlink(self):
        (self.root / "content" / "existing.md").write_bytes(b"\xef\xbb\xbf# Imported\n")
        status, note, _ = self.request("GET", "/api/note?id=existing.md")
        self.assertEqual(status, 200)
        self.assertEqual(note["content"], "# Imported\n")
        self.assertEqual(note["category"], "未分类")
        self.assertEqual(self.request("PUT", "/api/note", dict(note, content="edited"))[1]["id"], "existing.md")
        outside = self.root / "secret.md"
        outside.write_text("secret")
        try:
            (self.root / "content" / "linked.md").symlink_to(outside)
        except OSError:
            return  # Windows accounts may lack the symbolic-link privilege.
        self.assertEqual(self.request("GET", "/api/note?id=linked.md")[0], 400)
        self.assertNotIn("linked.md", [n["id"] for n in self.request("GET", "/api/notes")[1]["notes"]])

    def test_deleted_note_persists_and_restores_exact_original(self):
        note = self.create(title="恢复中文", category="项目/TPU", content="initial")
        source = self.root / "content" / note["id"]
        raw = b"\xef\xbb\xbf" + "# 原始文档\r\n\r\nSyncFlag 内容  \r\n".encode("utf-8")
        source.write_bytes(raw)
        note = self.request("GET", "/api/note?" + urlencode({"id": note["id"]}))[1]
        status, deleted, _ = self.request("DELETE", "/api/note", {"id": note["id"], "revision": note["revision"]})
        self.assertEqual(status, 200, deleted)
        self.assertEqual(deleted["id"], note["id"])
        self.assertEqual(deleted["content"], note["content"])
        self.assertEqual(deleted["revision"], note["revision"])
        self.assertRegex(deleted["trash_id"], r"^[0-9a-f]{32}$")
        self.assertFalse(source.exists())
        self.assertEqual(self.request("GET", "/api/note?" + urlencode({"id": note["id"]}))[0], 404)
        active = self.request("GET", "/api/notes?q=syncflag")[1]
        self.assertEqual(active["notes"], [])
        self.assertFalse(any(category.startswith(".") for category in active["categories"]))
        listing = self.request("GET", "/api/trash?q=syncflag")[1]["notes"]
        self.assertEqual([item["trash_id"] for item in listing], [deleted["trash_id"]])
        self.assertIn("SyncFlag", listing[0]["excerpt"])
        self.assertNotIn("content", listing[0])
        self.assertEqual(self.request("GET", "/api/trash?q=notpresent")[1]["notes"], [])
        self.stop()
        self.start()
        status, archived, _ = self.request("GET", "/api/trash/note?" + urlencode({"id": deleted["trash_id"]}))
        self.assertEqual(status, 200)
        self.assertEqual(archived, deleted)
        source.parent.rmdir()
        status, restored, _ = self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})
        self.assertEqual(status, 200, restored)
        self.assertEqual(source.read_bytes(), raw)
        self.assertEqual(restored, note)
        self.assertEqual(self.request("GET", "/api/trash")[1]["notes"], [])
        self.assertEqual(self.request("GET", "/api/trash/note?" + urlencode({"id": deleted["trash_id"]}))[0], 404)
        self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})[0], 404)

    def test_deleted_root_note_restores_without_default_category_move(self):
        source = self.root / "content" / "root.md"
        source.write_bytes(b"# Root\r\nkeep root position\r\n")
        note = self.request("GET", "/api/note?id=root.md")[1]
        deleted = self.request("DELETE", "/api/note", note)[1]
        status, restored, _ = self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored["id"], "root.md")
        self.assertEqual(source.read_bytes(), b"# Root\r\nkeep root position\r\n")
        self.assertFalse((self.root / "content" / "未分类" / "root.md").exists())

    def test_delete_requires_current_revision_and_same_origin(self):
        note = self.create(content="before")
        current = self.request("PUT", "/api/note", dict(note, content="latest"))[1]
        self.assertEqual(self.request("DELETE", "/api/note", note)[0], 409)
        self.assertEqual(self.request("DELETE", "/api/note", {"id": note["id"]})[0], 409)
        self.assertEqual(self.request("DELETE", "/api/note", current, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("DELETE", "/api/note", current, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("DELETE", "/api/note", {"id": "../outside.md", "revision": "anything"})[0], 400)
        self.assertEqual(self.request("GET", "/api/trash")[1]["notes"], [])
        self.assertEqual((self.root / "content" / note["id"]).read_bytes(), b"latest")
        deleted = self.request("DELETE", "/api/note", current)[1]
        self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]}, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(len(self.request("GET", "/api/trash")[1]["notes"]), 1)

    def test_restore_name_collision_keeps_both_versions(self):
        original = self.create(content="original")
        deleted = self.request("DELETE", "/api/note", original)[1]
        replacement = self.create(content="new content")
        status, _, _ = self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})
        self.assertEqual(status, 409)
        self.assertEqual((self.root / "content" / replacement["id"]).read_bytes(), b"new content")
        self.assertEqual(self.request("GET", "/api/trash/note?" + urlencode({"id": deleted["trash_id"]}))[1]["content"], "original")
        deleted_replacement = self.request("DELETE", "/api/note", replacement)[1]
        self.assertNotEqual(deleted["trash_id"], deleted_replacement["trash_id"])
        self.assertEqual(len(self.request("GET", "/api/trash")[1]["notes"]), 2)
        self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})[0], 200)
        self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted_replacement["trash_id"]})[0], 409)
        self.assertEqual((self.root / "content" / original["id"]).read_bytes(), b"original")

    def test_restore_collision_during_publish_does_not_overwrite(self):
        note = self.create(content="archived")
        deleted = self.request("DELETE", "/api/note", note)[1]
        target = self.root / "content" / note["id"]
        original_link = os.link

        def publish_collision(source, destination):
            Path(destination).write_bytes(b"external editor won")
            return original_link(source, destination)

        with mock.patch("server.os.link", side_effect=publish_collision):
            self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})[0], 409)
        self.assertEqual(target.read_bytes(), b"external editor won")
        self.assertEqual(self.request("GET", "/api/trash/note?" + urlencode({"id": deleted["trash_id"]}))[1]["content"], "archived")

    def test_failed_delete_keeps_active_file_and_cleans_snapshot(self):
        note = self.create(content="must remain")
        source = self.root / "content" / note["id"]
        original_unlink = Path.unlink

        def denied_unlink(path, *args, **kwargs):
            if path == source:
                raise PermissionError("simulated file in use")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", new=denied_unlink):
            self.assertEqual(self.request("DELETE", "/api/note", note)[0], 500)
        self.assertEqual(source.read_bytes(), b"must remain")
        self.assertEqual(self.request("GET", "/api/trash")[1]["notes"], [])

    def test_trash_ids_metadata_and_content_cannot_escape_or_silently_change(self):
        for trash_id in ["../outside", ".private", "a/b", "C:\\private", "../" + "a" * 32, "A" * 32, "a" * 31, None]:
            with self.subTest(trash_id=trash_id):
                query = urlencode({"id": trash_id}) if trash_id is not None else ""
                self.assertEqual(self.request("GET", "/api/trash/note?" + query)[0], 400)
                self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": trash_id})[0], 400)
        note = self.create(content="original")
        deleted = self.request("DELETE", "/api/note", note)[1]
        entry = self.root / "content" / ".trash" / deleted["trash_id"]
        metadata_path = entry / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata_path.write_text(json.dumps(dict(metadata, id="../outside.md")), encoding="utf-8")
        self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})[0], 400)
        self.assertFalse((self.root / "outside.md").exists())
        self.assertEqual((entry / "note.md").read_bytes(), b"original")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        (entry / "note.md").write_bytes(b"tampered snapshot")
        self.assertEqual(self.request("GET", "/api/trash/note?" + urlencode({"id": deleted["trash_id"]}))[0], 409)
        self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})[0], 409)
        self.assertFalse((self.root / "content" / note["id"]).exists())

    def test_trash_symlinks_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        trash = self.root / "content" / ".trash"
        try:
            trash.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("This Windows account cannot create symbolic links.")
        note = self.create(content="do not move")
        self.assertEqual(self.request("DELETE", "/api/note", note)[0], 400)
        self.assertEqual(self.request("GET", "/api/trash")[0], 400)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.root / "content" / note["id"]).read_bytes(), b"do not move")
        trash.unlink()
        deleted = self.request("DELETE", "/api/note", note)[1]
        archived = trash / deleted["trash_id"] / "note.md"
        secret = outside / "secret.md"
        secret.write_bytes(b"outside secret")
        archived.unlink()
        archived.symlink_to(secret)
        self.assertEqual(self.request("GET", "/api/trash/note?" + urlencode({"id": deleted["trash_id"]}))[0], 400)
        self.assertEqual(self.request("POST", "/api/trash/restore", {"trash_id": deleted["trash_id"]})[0], 400)
        self.assertEqual(self.request("GET", "/api/trash")[1]["notes"], [])
        self.assertEqual(secret.read_bytes(), b"outside secret")


if __name__ == "__main__":
    unittest.main()
