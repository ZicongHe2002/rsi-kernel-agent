"""Verify local owner entry links without changing real accounts or launching browsers."""

import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from auth import AuthError, AuthStore
import launch
from local_access import auth_state_dir
import owner_admin


class OwnerEntryTests(unittest.TestCase):
    def test_generated_link_is_local_single_use_and_not_in_database(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "content"
            parsed = urlsplit(owner_admin.owner_url(data_dir, 9876))
            self.assertEqual(parsed.scheme, "http")
            self.assertEqual(parsed.netloc, "127.0.0.1:9876")
            self.assertEqual(parsed.query, "")
            token = parsed.fragment.removeprefix("owner-token=")
            database = auth_state_dir(data_dir) / "auth.sqlite3"
            self.assertNotIn(token.encode(), database.read_bytes())
            store = AuthStore(database)
            try:
                self.assertTrue(store.consume_owner_token(token))
                with self.assertRaises(AuthError):
                    store.consume_owner_token(token)
            finally:
                store.close()

    def test_open_cli_does_not_print_owner_token(self):
        link = "http://127.0.0.1:8765/#owner-token=TEST_PRIVATE_TOKEN"
        output = io.StringIO()
        with patch("sys.argv", ["owner_admin.py", "bootstrap", "--open"]), \
                patch.object(owner_admin.subprocess, "run") as start, \
                patch.object(owner_admin, "owner_url", return_value=link), \
                patch.object(owner_admin.webbrowser, "open", return_value=True) as browser, \
                contextlib.redirect_stdout(output):
            owner_admin.main()
        self.assertNotIn("TEST_PRIVATE_TOKEN", output.getvalue())
        self.assertNotIn(link, output.getvalue())
        self.assertIn("--no-browser", start.call_args.args[0])
        browser.assert_called_once_with(link)

    def test_background_launch_does_not_generate_owner_credentials(self):
        health = {"app": "zhixu", "data_dir": str(launch.DATA_DIR)}
        with patch("sys.argv", ["launch.py", "--no-browser"]), \
                patch.object(launch, "get_health", return_value=health), \
                patch.object(launch, "owner_url") as create, \
                patch.object(launch.webbrowser, "open") as browser, \
                contextlib.redirect_stdout(io.StringIO()):
            launch.main()
        create.assert_not_called()
        browser.assert_not_called()

    def test_launcher_waits_until_local_service_credential_is_ready(self):
        token = "test-launcher-shutdown-token"
        ready = {"app": "zhixu", "data_dir": str(launch.DATA_DIR), "pid": 1234,
                 "instance": hashlib.sha256(token.encode()).hexdigest()}
        with tempfile.TemporaryDirectory() as directory, \
                patch("sys.argv", ["launch.py", "--no-browser"]), \
                patch.object(launch, "LOG_DIR", Path(directory)), \
                patch.object(launch, "get_health", side_effect=[None, {"app": "zhixu"}, ready]), \
                patch.object(launch.secrets, "token_urlsafe", return_value=token), \
                patch.object(launch.subprocess, "Popen"), \
                patch.object(launch.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            launch.main()
            saved = launch.state_path(8765).read_text(encoding="utf-8")
            self.assertIn('"pid": 1234', saved)


if __name__ == "__main__":
    unittest.main()
