import gzip
import hashlib
import http.client
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode

from server import make_server


class StaticDeliveryTests(unittest.TestCase):
    def setUp(self):
        logging = mock.patch("server.Handler.log_message")
        logging.start()
        self.addCleanup(logging.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        (self.web / "vendor").mkdir(parents=True)
        self.script = (b"window.fixture = 'a repetitive fixture';\n" * 200)
        (self.web / "vendor" / "mermaid.js").write_bytes(self.script)
        self.module = b"export const fixture = 'ES module fixture';\n" * 200
        self.vendor = self.web / "vendor" / "mermaid-11.17.2"
        self.chunk = self.vendor / "chunks" / "mermaid.esm.min" / "chunk-TEST.mjs"
        self.chunk.parent.mkdir(parents=True)
        self.chunk.write_bytes(self.module)
        (self.vendor / "mermaid.esm.min.mjs").write_bytes(b"export default {};\n")
        (self.vendor / "unlisted.mjs").write_bytes(b"private fixture")
        (self.vendor / "manifest.json").write_text(json.dumps({"files": [
            {"path": "mermaid.esm.min.mjs"},
            {"path": "chunks/mermaid.esm.min/chunk-TEST.mjs", "sha256": hashlib.sha256(self.module).hexdigest()},
        ]}), encoding="utf-8")
        (self.web / "index.html").write_bytes(b"<h1>Version one</h1>")
        self.server = make_server(self.root / "content", 0, self.web)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def get(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_static_gzip_round_trip_and_revalidation_saves_transfer(self):
        status, compressed, headers = self.get("/vendor/mermaid.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(gzip.decompress(compressed), self.script)
        self.assertLess(len(compressed), len(self.script) // 2)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(int(headers["Content-Length"]), len(compressed))
        self.assertEqual(headers["Cache-Control"], "private, no-cache")
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        for condition in (headers["ETag"], "W/" + headers["ETag"], '"old", ' + headers["ETag"], "*"):
            status, body, second = self.get("/vendor/mermaid.js", {"Accept-Encoding": "gzip", "If-None-Match": condition})
            self.assertEqual(status, 304)
            self.assertEqual(body, b"")
            self.assertEqual(second["ETag"], headers["ETag"])
            self.assertEqual(second["Vary"], "Accept-Encoding")
            self.assertNotIn("Content-Length", second)
        status, raw, plain = self.get("/vendor/mermaid.js", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 200)
        self.assertEqual(raw, self.script)
        self.assertNotIn("Content-Encoding", plain)
        self.assertNotEqual(plain["ETag"], headers["ETag"])

    def test_gzip_quality_zero_and_explicit_exclusion_override_wildcard(self):
        denied = ("", "identity", "gzip;q=0", "gzip; q=0.0, br", "gzip;q=0, *;q=1", "*;q=0", "xgzip", "gzip;q=NaN", "gzip;q=invalid")
        allowed = ("gzip", "br, gzip;q=0.5", "GZip; Q=0.5", "*;q=0.5", "gzip;q=1, *;q=0")
        for encoding in denied + allowed:
            with self.subTest(encoding=encoding):
                status, body, headers = self.get("/vendor/mermaid.js", {"Accept-Encoding": encoding})
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Content-Encoding") == "gzip", encoding in allowed)
                self.assertEqual(gzip.decompress(body) if encoding in allowed else body, self.script)

    def test_html_and_changed_scripts_update_on_next_revalidation(self):
        status, _, html = self.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(html["Cache-Control"], "private, no-cache")
        (self.web / "index.html").write_bytes(b"<h1>New version available immediately</h1>")
        status, body, updated = self.get("/", {"If-None-Match": html["ETag"]})
        self.assertEqual(status, 200)
        self.assertIn(b"immediately", body)
        self.assertNotEqual(updated["ETag"], html["ETag"])
        _, _, script = self.get("/vendor/mermaid.js", {"Accept-Encoding": "gzip"})
        changed = self.script + b"\nwindow.updated = true;\n"
        (self.web / "vendor" / "mermaid.js").write_bytes(changed)
        status, body, updated = self.get("/vendor/mermaid.js", {"Accept-Encoding": "gzip", "If-None-Match": script["ETag"]})
        self.assertEqual(status, 200)
        self.assertEqual(gzip.decompress(body), changed)
        self.assertNotEqual(updated["ETag"], script["ETag"])

    def test_repeated_requests_reuse_compressed_bytes(self):
        with mock.patch("server.gzip.compress", wraps=gzip.compress) as compress:
            first = self.get("/vendor/mermaid.js", {"Accept-Encoding": "gzip"})
            second = self.get("/vendor/mermaid.js", {"Accept-Encoding": "gzip"})
            self.assertEqual(first[1], second[1])
            self.assertEqual(compress.call_count, 1)
            (self.web / "vendor" / "mermaid.js").write_bytes(self.script + b"// update")
            self.get("/vendor/mermaid.js", {"Accept-Encoding": "gzip"})
            self.assertEqual(compress.call_count, 2)

    def test_api_session_notes_and_exports_remain_uncached_uncompressed(self):
        note = self.server.store.save({"title": "Private", "category": "QA", "content": "private body " * 300}, create=True)
        token = self.server.auth.consume_owner_token(self.server.auth.create_owner_token())
        headers = {"Cookie": "zhixu_session=" + token, "Accept-Encoding": "gzip", "If-None-Match": "*"}
        paths = ("/api/auth/session", "/api/notes", "/api/note?" + urlencode({"id": note["id"]}), "/api/export?" + urlencode({"id": note["id"]}))
        for path in paths:
            with self.subTest(path=path):
                status, body, response_headers = self.get(path, headers)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertEqual(response_headers["Cache-Control"], "no-store")
                self.assertNotIn("Content-Encoding", response_headers)
                self.assertNotIn("ETag", response_headers)
        status, body, response_headers = self.get("/api/notes", {"Accept-Encoding": "gzip", "If-None-Match": "*"})
        self.assertEqual(status, 401)
        self.assertNotIn("private body", json.loads(body).get("error", ""))
        self.assertEqual(response_headers["Cache-Control"], "no-store")

    def test_cached_static_file_still_requires_allowed_host_and_existing_file(self):
        _, _, cached = self.get("/vendor/mermaid.js")
        status, _, headers = self.get("/vendor/mermaid.js", {"Host": "evil.test", "If-None-Match": cached["ETag"]})
        self.assertEqual(status, 403)
        self.assertEqual(headers["Cache-Control"], "no-store")
        (self.web / "vendor" / "mermaid.js").unlink()
        self.assertEqual(self.get("/vendor/mermaid.js", {"If-None-Match": cached["ETag"]})[0], 404)
        self.assertEqual(self.get("/.auth/auth.sqlite3", {"If-None-Match": "*"})[0], 404)

    def test_manifest_modules_have_javascript_mime_gzip_and_versioned_cache(self):
        url = "/vendor/mermaid-11.17.2/chunks/mermaid.esm.min/chunk-TEST.mjs"
        status, body, headers = self.get(url, {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "private, max-age=31536000, immutable")
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(body), self.module)
        self.assertLess(len(body), len(self.module))
        status, body, repeated = self.get(url, {"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(repeated["Cache-Control"], headers["Cache-Control"])
        status, body, plain = self.get("/vendor/mermaid-11.17.2/mermaid.esm.min.mjs")
        self.assertEqual(status, 200)
        self.assertEqual(plain["Content-Type"], "text/javascript; charset=utf-8")
        self.assertNotIn("Content-Encoding", plain)
        self.assertEqual(body, b"export default {};\n")

    def test_manifest_and_unlisted_or_traversal_paths_are_not_public_assets(self):
        prefix = "/vendor/mermaid-11.17.2/"
        for path in (
            prefix + "manifest.json", prefix + "unlisted.mjs", prefix + "chunks/mermaid.esm.min/unlisted.mjs",
            prefix + "../mermaid.js", prefix + "../../index.html",
            prefix + "%2e%2e/mermaid.js", prefix + "%2e%2e%2fmermaid.js",
            prefix + "%2e%2e%5cmermaid.js", prefix + "%252e%252e%252fmermaid.js",
        ):
            with self.subTest(path=path):
                status, _, headers = self.get(path, {"Accept-Encoding": "gzip", "If-None-Match": "*"})
                self.assertEqual(status, 404)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertNotIn("ETag", headers)

    def test_32_simultaneous_module_connections_deliver_and_revalidate(self):
        """A module-import burst uses separate connections, as the tunnel does."""
        path = "/vendor/mermaid-11.17.2/chunks/mermaid.esm.min/chunk-TEST.mjs"
        etag = None
        with ThreadPoolExecutor(max_workers=32) as pool:
            for expected_status in (200, 304):
                barrier = threading.Barrier(32)
                headers = {"Accept-Encoding": "gzip"}
                if etag:
                    headers["If-None-Match"] = etag

                def request():
                    barrier.wait(timeout=10)
                    return self.get(path, headers)

                responses = list(pool.map(lambda _: request(), range(32)))
                for status, body, response_headers in responses:
                    self.assertEqual(status, expected_status)
                    self.assertEqual(response_headers["Content-Type"], "text/javascript; charset=utf-8")
                    self.assertEqual(response_headers["Cache-Control"], "private, max-age=31536000, immutable")
                    if expected_status == 200:
                        self.assertEqual(gzip.decompress(body), self.module)
                    else:
                        self.assertEqual(body, b"")
                    if etag:
                        self.assertEqual(response_headers["ETag"], etag)
                etag = responses[0][2]["ETag"]


if __name__ == "__main__":
    unittest.main()
