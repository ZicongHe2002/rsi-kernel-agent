"""Exercise sharing bookkeeping only; never contact Cloudflare or publish a site."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import sys

from share import ShareLock, clear_origin, publish_origin, tunnel_origin
import share


class ShareHelperTests(unittest.TestCase):
    def test_only_quick_tunnel_https_origins_are_accepted(self):
        self.assertEqual(tunnel_origin(" | https://quiet-paper-moon.trycloudflare.com |"),
                         "https://quiet-paper-moon.trycloudflare.com")
        for line in ("http://example.trycloudflare.com", "https://dash.cloudflare.com",
                     "https://example.trycloudflare.com.evil.test", "https://-bad.trycloudflare.com",
                     "https://bad-.trycloudflare.com", "https://trycloudflare.com"):
            self.assertIsNone(tunnel_origin(line), line)

    def test_cleanup_does_not_remove_a_replacement_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "public-origin.json"
            first, replacement = "https://one.trycloudflare.com", "https://two.trycloudflare.com"
            publish_origin(path, first)
            self.assertEqual(json.loads(path.read_text())["origin"], first)
            publish_origin(path, replacement)
            clear_origin(path, first)
            self.assertEqual(json.loads(path.read_text())["origin"], replacement)
            clear_origin(path, replacement)
            self.assertFalse(path.exists())

    def test_only_one_share_lock_and_reusable_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "share.lock"
            with ShareLock(path):
                with self.assertRaises(RuntimeError):
                    with ShareLock(path):
                        self.fail("second share acquired the lock")
            with ShareLock(path):
                self.assertTrue(path.exists())

    def test_stop_notifies_only_this_share_and_cleans_its_public_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            errors = []

            def worker():
                try:
                    share.run_share(9876)
                except Exception as error:
                    errors.append(error)

            fake_child = [sys.executable, "-u", "-c",
                          "import time; print('https://qa-local.trycloudflare.com', flush=True); time.sleep(30)"]
            with patch.object(share, "BASE_DIR", base), \
                    patch.object(share, "DATA_DIR", base / "content"), \
                    patch.object(share, "CLOUDFLARED", Path(sys.executable)), \
                    patch.object(share, "tunnel_command", return_value=fake_child), \
                    patch.object(share.subprocess, "run") as local_start:
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                state = base / ".auth" / "share-state.json"
                deadline = time.monotonic() + 6
                try:
                    while not share.read_state(state).get("origin") and time.monotonic() < deadline:
                        if errors:
                            raise errors[0]
                        time.sleep(0.05)
                    self.assertEqual(share.read_state(state).get("origin"), "https://qa-local.trycloudflare.com")
                finally:
                    share.request_stop()
                    thread.join(timeout=6)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertFalse((base / ".auth" / "public-origin.json").exists())
                self.assertFalse(state.exists())
                self.assertIn("--no-browser", local_start.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
