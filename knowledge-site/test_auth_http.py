import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode

from server import make_server


class AuthHttpTests(unittest.TestCase):
    def setUp(self):
        logging = mock.patch("server.Handler.log_message")
        logging.start()
        self.addCleanup(logging.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<h1>Login shell</h1>", encoding="utf-8")
        self.server = make_server(self.root / "content", 0, self.web, public_origin="https://kb.example.test")
        self.server.shutdown_token = "test-shutdown-only"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.hidden = self.server.store.save({"title": "Confidential", "category": "Private", "content": "SECRET_BODY_7ea2"}, create=True)
        self.readable = self.server.store.save({"title": "Handbook", "category": "Team", "content": "Visible handbook"}, create=True)
        self.editable = self.server.store.save({"title": "Draft", "category": "Drafts", "content": "Editable text"}, create=True)
        self.owner = self.owner_login()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if self.server.auth is not None:
            self.server.auth.close()
        self.temp.cleanup()

    def request(self, method, path, data=None, identity=None, headers=None, raw=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"Content-Type": "application/json"}
        if identity:
            request_headers.update(identity)
        request_headers.update(headers or {})
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        try:
            connection.request(method, path, body, request_headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, payload if raw else json.loads(payload), dict(response.getheaders())
        finally:
            connection.close()

    def owner_login(self, headers=None):
        token = self.server.auth.create_owner_token()
        status, payload, reply_headers = self.request("POST", "/api/auth/bootstrap", {"token": token}, headers=headers)
        self.assertEqual(status, 200, payload)
        cookie = reply_headers["Set-Cookie"].split(";", 1)[0]
        identity = {"Cookie": cookie}
        status, payload, _ = self.request("GET", "/api/auth/session", identity=identity, headers=headers)
        self.assertEqual(status, 200)
        identity["X-CSRF-Token"] = payload["csrf_token"]
        return identity

    def member_login(self, name="alice", grants=None):
        account = {"id": name, "display_name": name.title(), "grants": grants or []}
        self.assertEqual(self.request("POST", "/api/admin/users", account, self.owner)[0], 201)
        status, payload, headers = self.request("POST", "/api/auth/request", {"id": name})
        self.assertEqual(status, 200, payload)
        identity = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        _, requests, _ = self.request("GET", "/api/admin/requests", identity=self.owner)
        request_id = next(item["id"] for item in requests["requests"] if item["user_id"] == name)
        status, payload, _ = self.request("POST", "/api/admin/requests/approve", {"id": request_id}, self.owner)
        self.assertEqual(status, 200, payload)
        _, payload, _ = self.request("GET", "/api/auth/session", identity=identity)
        identity["X-CSRF-Token"] = payload["csrf_token"]
        return identity

    def note_url(self, route, note):
        return route + "?" + urlencode({"id": note["id"]})

    def test_anonymous_endpoints_and_static_paths_do_not_leak_notes(self):
        paths = ["/api/notes", "/api/notes?q=SECRET_BODY_7ea2", "/api/trash", "/api/admin/users", "/api/admin/requests",
                 self.note_url("/api/note", self.hidden), self.note_url("/api/export", self.hidden), "/api/trash/note?id=anything"]
        for path in paths:
            with self.subTest(path=path):
                status, body, _ = self.request("GET", path, raw=True)
                self.assertEqual(status, 401)
                for secret in (b"SECRET_BODY_7ea2", b"Confidential", b"Private", b"data_dir"):
                    self.assertNotIn(secret, body)
        for path in ("/content/Private/Confidential.md", "/.auth/auth.sqlite3", "/.auth/local-service.key", "/../content/Private/Confidential.md"):
            status, body, _ = self.request("GET", path, raw=True)
            self.assertIn(status, (400, 404))
            self.assertNotIn(b"SECRET_BODY_7ea2", body)
        self.assertEqual(self.request("GET", "/api/health")[1], {"app": "zhixu"})
        session = self.request("GET", "/api/auth/session")[1]
        self.assertFalse(session["authenticated"])
        self.assertNotIn("library_id", session)

    def test_pending_unknown_id_is_not_authenticated_and_cannot_get_owner(self):
        status, payload, headers = self.request("POST", "/api/auth/request", {"id": "nobody"})
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"request_status": "pending"})
        pending = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        self.assertEqual(self.request("GET", "/api/notes", identity=pending)[0], 401)
        state = self.request("GET", "/api/auth/session", identity=pending)[1]
        self.assertEqual(state["request_status"], "pending")
        self.assertIsNone(state["user"])
        self.assertEqual(self.request("POST", "/api/auth/request", {"id": "fl"})[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/bootstrap", {"token": "fl"})[0], 403)

    def test_bootstrap_is_single_use_csrf_is_required_and_logout_revokes_cookie(self):
        token = self.server.auth.create_owner_token()
        self.assertEqual(self.request("POST", "/api/auth/bootstrap", {"token": token})[0], 200)
        self.assertEqual(self.request("POST", "/api/auth/bootstrap", {"token": token})[0], 403)
        cookie_only = {"Cookie": self.owner["Cookie"]}
        new_note = {"title": "No CSRF", "category": "Private", "content": "No"}
        self.assertEqual(self.request("POST", "/api/notes", new_note, cookie_only)[0], 403)
        self.assertEqual(self.request("POST", "/api/notes", new_note, self.owner, {"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("POST", "/api/notes", new_note, self.owner, {"Origin": "https://evil.test"})[0], 403)
        self.assertEqual(self.request("POST", "/api/notes", new_note, self.owner, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        status, _, headers = self.request("POST", "/api/auth/logout", {}, self.owner)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/api/notes", identity=self.owner)[0], 401)

    def test_member_listing_search_export_and_casefold_permissions(self):
        member = self.member_login(grants=[{"scope": "category", "path": "team", "access": "read"}])
        status, listing, _ = self.request("GET", "/api/notes", identity=member)
        self.assertEqual(status, 200)
        self.assertEqual([note["id"] for note in listing["notes"]], [self.readable["id"]])
        self.assertNotIn("Private", listing["categories"])
        self.assertFalse(listing["notes"][0]["permissions"]["edit"])
        self.assertEqual(self.request("GET", "/api/notes?q=SECRET_BODY_7ea2", identity=member)[1]["notes"], [])
        for route in ("/api/note", "/api/export"):
            self.assertEqual(self.request("GET", self.note_url(route, self.hidden), identity=member)[0], 404)
        status, raw, _ = self.request("GET", self.note_url("/api/export", self.readable), identity=member, raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"Visible handbook")
        for route in ("/api/admin/users", "/api/admin/requests", "/api/trash"):
            self.assertEqual(self.request("GET", route, identity=member)[0], 403)
        self.assertEqual(self.request("GET", "/api/health", identity=member)[1], {"app": "zhixu"})
        for note_id in ("Team/../Private/Confidential.md", "Team\\..\\Private\\Confidential.md"):
            status, _, _ = self.request("GET", "/api/note?" + urlencode({"id": note_id}), identity=member)
            self.assertEqual(status, 400)

    def test_edit_permission_controls_mutations_and_category_creation(self):
        member = self.member_login(grants=[
            {"scope": "category", "path": "Team", "access": "read"},
            {"scope": "category", "path": "Drafts", "access": "edit"},
        ])
        for note in (self.readable, self.hidden):
            self.assertEqual(self.request("PUT", "/api/note", dict(note, content="Denied"), member)[0], 403)
        status, edited, _ = self.request("PUT", "/api/note", dict(self.editable, content="Allowed update"), member)
        self.assertEqual(status, 200)
        self.assertTrue(edited["permissions"]["edit"])
        self.assertEqual(self.request("PUT", "/api/note", dict(edited, title="Moved"), member)[0], 403)
        self.assertEqual(self.request("PUT", "/api/note", dict(edited, category="Private"), member)[0], 403)
        self.assertEqual(self.request("DELETE", "/api/note", edited, member)[0], 403)
        self.assertEqual(self.request("POST", "/api/trash/restore", {"id": "anything"}, member)[0], 403)
        self.assertEqual(self.request("POST", "/api/categories", {"name": "Anywhere"}, member)[0], 403)
        self.assertEqual(self.request("POST", "/api/task-lists/ensure", {}, member)[0], 403)
        for category in ("Team", "Private", "Drafts2"):
            status, _, _ = self.request("POST", "/api/notes", {"title": "New", "category": category, "content": "No"}, member)
            self.assertEqual(status, 403)
        status, note, _ = self.request("POST", "/api/notes", {"title": "New", "category": "Drafts/Sub", "content": "Allowed"}, member)
        self.assertEqual(status, 201)
        self.assertEqual(note["id"], "Drafts/Sub/New.md")

    def test_single_document_edit_grant_does_not_allow_new_documents(self):
        member = self.member_login(grants=[{"scope": "note", "path": self.editable["id"], "access": "edit"}])
        self.assertEqual(self.request("PUT", "/api/note", dict(self.editable, content="Changed"), member)[0], 200)
        self.assertEqual(self.request("POST", "/api/notes", {"title": "New", "category": "Drafts", "content": "No"}, member)[0], 403)

    def test_all_read_covers_current_and_future_notes_without_owner_privileges(self):
        (self.server.store.root / "Root.md").write_text("Root note", encoding="utf-8")
        member = self.member_login("gpt_reader", [{"scope": "all", "access": "read"}])
        status, session, _ = self.request("GET", "/api/auth/session", identity=member)
        self.assertEqual(status, 200)
        self.assertFalse(session["user"]["owner"])
        self.assertEqual(session["user"]["role"], "member")
        self.assertEqual(session["user"]["grants"], [{"scope": "all", "path": "", "access": "read"}])
        original_ids = {self.hidden["id"], self.readable["id"], self.editable["id"], "Root.md"}
        _, listing, _ = self.request("GET", "/api/notes", identity=member)
        self.assertEqual({note["id"] for note in listing["notes"]}, original_ids)
        self.assertTrue(all(note["permissions"] == {"read": True, "edit": False} for note in listing["notes"]))
        status, future, _ = self.request("POST", "/api/notes", {
            "title": "Future", "category": "Future/New", "content": "Written after access was approved"
        }, self.owner)
        self.assertEqual(status, 201)
        (self.server.store.root / "FutureRoot.md").write_text("Future root note", encoding="utf-8")
        original_ids.update({future["id"], "FutureRoot.md"})
        _, listing, _ = self.request("GET", "/api/notes", identity=member)
        self.assertEqual({note["id"] for note in listing["notes"]}, original_ids)
        self.assertIn("Future/New", listing["categories"])
        for note_id in original_ids:
            status, note, headers = self.request("GET", "/api/note?" + urlencode({"id": note_id}), identity=member)
            self.assertEqual(status, 200)
            self.assertFalse(note["permissions"]["edit"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            status, raw, _ = self.request("GET", self.note_url("/api/export", note), identity=member, raw=True)
            self.assertEqual(status, 200)
            self.assertEqual(raw.decode("utf-8"), note["content"])
            self.assertEqual(self.request("PUT", "/api/note", dict(note, content="Denied"), member)[0], 403)
        for path in ("/api/admin/users", "/api/admin/requests", "/api/trash", "/api/trash/note?id=anything"):
            self.assertEqual(self.request("GET", path, identity=member)[0], 403)
        for method, path, data in (
            ("POST", "/api/notes", {"title": "Denied", "category": "Future/New", "content": "No"}),
            ("POST", "/api/categories", {"name": "Denied"}),
            ("POST", "/api/admin/users", {"id": "intruder", "grants": [{"scope": "all", "access": "edit"}]}),
            ("PUT", "/api/admin/users", {"id": "gpt_reader", "grants": [{"scope": "all", "access": "edit"}]}),
            ("POST", "/api/admin/requests/approve", {"id": "anything"}),
            ("DELETE", "/api/note", self.hidden),
            ("POST", "/api/trash/restore", {"id": "anything"}),
            ("POST", "/api/task-lists/ensure", {}),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, data, member)[0], 403)
        for note_id in ("../Root.md", "Future/../../Root.md", "Future\\Root.md", "/Root.md", ".auth/secrets.md"):
            for route in ("/api/note", "/api/export"):
                self.assertEqual(self.request("GET", route + "?" + urlencode({"id": note_id}), identity=member)[0], 400)
        self.assertEqual(self.server.store.read(self.hidden["id"])["content"], self.hidden["content"])

    def test_grant_changes_and_disable_take_effect_without_new_login(self):
        member = self.member_login(grants=[{"scope": "category", "path": "Drafts", "access": "edit"}])
        self.assertEqual(self.request("GET", self.note_url("/api/note", self.editable), identity=member)[0], 200)
        self.assertEqual(self.request("PUT", "/api/admin/users", {"id": "alice", "grants": []}, self.owner)[0], 200)
        self.assertEqual(self.request("GET", self.note_url("/api/note", self.editable), identity=member)[0], 404)
        self.assertEqual(self.request("PUT", "/api/note", dict(self.editable, content="Denied"), member)[0], 403)
        self.assertEqual(self.request("PUT", "/api/admin/users", {"id": "alice", "enabled": False}, self.owner)[0], 200)
        self.assertEqual(self.request("GET", "/api/notes", identity=member)[0], 401)
        self.assertEqual(self.request("PUT", "/api/admin/users", {"id": "alice", "enabled": True}, self.owner)[0], 200)
        self.assertEqual(self.request("GET", "/api/notes", identity=member)[0], 401)
        self.assertEqual(self.request("PUT", "/api/admin/users", {"id": "fl", "enabled": False}, self.owner)[0], 403)

    def test_public_host_requires_https_origin_and_sets_secure_http_only_cookie(self):
        public = {"Host": "kb.example.test", "Origin": "https://kb.example.test"}
        token = self.server.auth.create_owner_token()
        status, _, headers = self.request("POST", "/api/auth/bootstrap", {"token": token}, headers=public)
        self.assertEqual(status, 200)
        for part in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, headers["Set-Cookie"])
        identity = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        status, session, _ = self.request("GET", "/api/auth/session", identity=identity, headers=public)
        self.assertEqual(status, 200)
        self.assertEqual(session["user"]["role"], "owner")
        self.assertEqual(self.request("GET", "/api/health", headers=public)[1], {"app": "zhixu"})
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": "kb.example.test", "Origin": "http://kb.example.test"})[0], 403)
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": "evil.test"})[0], 403)

    def test_cross_site_top_level_home_navigation_only_serves_public_shell(self):
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                      "Sec-Fetch-Dest": "document", "Origin": "https://chat.example.test"}
        for host in (f"127.0.0.1:{self.server.server_port}", "kb.example.test"):
            for path in ("/", "/index.html", "/?" + urlencode({"note": self.hidden["id"]})):
                with self.subTest(host=host, path=path):
                    status, body, headers = self.request("GET", path, identity=self.owner,
                                                       headers=dict(navigation, Host=host), raw=True)
                    self.assertEqual(status, 200)
                    self.assertEqual(body, b"<h1>Login shell</h1>")
                    self.assertNotIn(b"SECRET_BODY_7ea2", body)
                    self.assertNotIn("Set-Cookie", headers)
                    self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/", headers=dict(navigation, Host="evil.test"), raw=True)[0], 403)

    def test_cross_site_navigation_exception_never_allows_api_fetch_embed_or_writes(self):
        navigation = {"Host": "kb.example.test", "Origin": "https://kb.example.test",
                      "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        for path in ("/api/notes", "/api/auth/session", self.note_url("/api/note", self.hidden),
                     self.note_url("/api/export", self.hidden), "/app.js"):
            with self.subTest(path=path):
                status, body, _ = self.request("GET", path, identity=self.owner, headers=navigation, raw=True)
                self.assertEqual(status, 403)
                self.assertNotIn(b"SECRET_BODY_7ea2", body)
        for mode, destination in (("cors", "empty"), ("same-origin", "empty"), ("navigate", "iframe"), ("navigate", "frame")):
            with self.subTest(mode=mode, destination=destination):
                headers = dict(navigation, **{"Sec-Fetch-Mode": mode, "Sec-Fetch-Dest": destination})
                self.assertEqual(self.request("GET", "/", headers=headers, raw=True)[0], 403)
        for method in ("POST", "PUT", "DELETE"):
            for path in ("/", "/api/note"):
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.request(method, path, self.hidden, self.owner, navigation, raw=True)[0], 403)
        self.assertEqual(self.server.store.read(self.hidden["id"])["content"], "SECRET_BODY_7ea2")

    def test_proxy_headers_never_grant_owner_and_local_service_is_restricted(self):
        proxy_headers = [
            {"X-Forwarded-For": "127.0.0.1"}, {"X-Forwarded-Host": "localhost"},
            {"Forwarded": "for=127.0.0.1;host=localhost"}, {"CF-Connecting-IP": "127.0.0.1"},
        ]
        for headers in proxy_headers:
            self.assertEqual(self.request("GET", "/api/notes", headers=headers)[0], 401)
        service = {"Authorization": "Bearer " + self.server.local_token}
        status, health, _ = self.request("GET", "/api/health", headers=service)
        self.assertEqual(status, 200)
        self.assertEqual(health["data_dir"], str(self.server.store.root))
        self.assertEqual(self.request("GET", "/api/notes", headers=service)[0], 200)
        self.assertEqual(self.request("GET", "/api/notes", headers=dict(service, Host="kb.example.test"))[0], 401)
        for headers in proxy_headers:
            self.assertEqual(self.request("GET", "/api/notes", headers=dict(service, **headers))[0], 401)
        self.assertEqual(self.request("GET", "/api/notes", headers={"Authorization": "Bearer fake"})[0], 401)


if __name__ == "__main__":
    unittest.main()
