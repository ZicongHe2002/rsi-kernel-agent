from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from auth import AuthError, AuthStore, can_create, can_edit, can_read, normalize_grants


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state" / "auth.sqlite3"
        self.store = AuthStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def login(self, user_id="alice"):
        request = self.store.request_device(user_id, "192.0.2.1", "Test browser")
        request_id = self.store.list_requests()[-1]["id"]
        self.store.approve_device(request_id)
        return request["token"]

    def test_owner_is_fixed_and_bootstrap_is_single_use(self):
        self.assertEqual(self.store.list_users(), [{"id": "fl", "display_name": "风落", "owner": True, "enabled": True, "grants": []}])
        with self.assertRaises(AuthError):
            self.store.delete_user("FL")
        with self.assertRaises(AuthError):
            self.store.update_user("fl", enabled=False)
        with self.assertRaises(AuthError):
            self.store.request_device("fl", "192.0.2.2")
        first = self.store.create_owner_token()
        token = self.store.create_owner_token()
        with self.assertRaises(AuthError):
            self.store.consume_owner_token(first)
        session = self.store.consume_owner_token(token)
        self.assertEqual(self.store.get_user(session)["id"], "fl")
        self.assertTrue(can_edit(self.store.get_user(session), "Secret/note.md"))
        with self.assertRaises(AuthError):
            self.store.consume_owner_token(token)
        self.assertEqual(self.store.get_session(session)["user"]["id"], "fl")
        self.assertNotEqual(self.store.get_session(session)["csrf_token"], session)
        self.assertEqual(self.store.get_session(session), self.store.get_session(session))

    def test_two_instances_cannot_consume_one_bootstrap_twice(self):
        other = AuthStore(self.path)
        try:
            token = self.store.create_owner_token()
            def consume(store):
                try:
                    return bool(store.consume_owner_token(token))
                except AuthError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(consume, [self.store, other]))
            self.assertEqual(sorted(results), [False, True])
        finally:
            other.close()

    def test_unknown_and_disabled_users_have_generic_pending_responses(self):
        self.store.create_user("alice")
        self.store.create_user("bob", enabled=False)
        responses = [self.store.request_device(name, "192.0.2.1") for name in ("alice", "bob", "unknown")]
        for name, response in zip(("alice", "bob", "unknown"), responses):
            status = self.store.device_status(response["token"])
            self.assertEqual(status["status"], "pending")
            self.assertEqual(status["user_id"], name)
            self.assertNotIn("user", status)
            self.assertEqual(set(response), {"token", "status", "expires_at"})
            self.assertIsNone(self.store.get_user(response["token"]))
            self.assertIsNone(self.store.get_session(response["token"]))
        requests = self.store.list_requests()
        for request in requests[1:]:
            with self.assertRaises(AuthError):
                self.store.approve_device(request["id"])
        self.store.approve_device(requests[0]["id"])
        self.assertEqual(self.store.get_user(responses[0]["token"])["id"], "alice")

    def test_default_deny_read_edit_and_create(self):
        user = self.store.create_user("ALICE", "Alice")
        self.assertEqual(user["id"], "alice")
        self.assertFalse(can_read(user, "Work/Planning.md"))
        self.assertFalse(can_create(user, "Work"))
        user = self.store.update_user("alice", grants=[
            {"scope": "category", "path": "Work", "access": "read"},
            {"scope": "note", "path": "Work/Planning.md", "access": "edit"},
            {"scope": "category", "path": "Team", "access": "edit"},
        ])
        self.assertTrue(can_read(user, "work/sub/note.MD"))
        self.assertFalse(can_read(user, "Work2/note.md"))
        self.assertFalse(can_edit(user, "Work/sub/note.md"))
        self.assertTrue(can_edit(user, "WORK/planning.md"))
        self.assertFalse(can_create(user, "Work"))
        self.assertFalse(can_create(user, "Work/Planning.md"))
        self.assertTrue(can_read(user, "Team/Sub/Any.md"))
        self.assertTrue(can_edit(user, "Team/Sub/Any.md"))
        self.assertTrue(can_create(user, "TEAM"))
        self.assertTrue(can_create(user, "Team/Sub"))

    def test_unclassified_category_grant_matches_root_note_but_note_grant_keeps_original_id(self):
        category_reader = self.store.create_user(
            "rootreader", grants=[{"scope": "category", "path": "未分类", "access": "read"}]
        )
        self.assertTrue(can_read(category_reader, "README.md"))
        self.assertFalse(can_read(category_reader, "Work/README.md"))
        self.assertFalse(can_create(category_reader, "README.md"))
        category_editor = self.store.create_user(
            "rooteditor", grants=[{"scope": "category", "path": "未分类", "access": "edit"}]
        )
        self.assertTrue(can_edit(category_editor, "README.md"))
        self.assertTrue(can_create(category_editor, "未分类"))
        note_reader = self.store.create_user(
            "notereader", grants=[{"scope": "note", "path": "README.md", "access": "read"}]
        )
        self.assertTrue(can_read(note_reader, "README.md"))
        self.assertFalse(can_read(note_reader, "未分类/README.md"))

    def test_all_read_covers_current_and_future_notes_without_write_or_owner(self):
        user = self.store.create_user("reader", grants=[{"scope": "all", "access": "read"}])
        self.assertEqual(user["grants"], [{"scope": "all", "path": "", "access": "read"}])
        self.assertFalse(user["owner"])
        for note_id in ("README.md", "Team/current.md", "Future/New/created-later.md"):
            with self.subTest(note_id=note_id):
                self.assertTrue(can_read(user, note_id))
                self.assertFalse(can_edit(user, note_id))
        self.assertFalse(can_create(user, "Future/New"))

    def test_all_edit_allows_future_categories_but_not_malformed_paths_or_disabled_users(self):
        user = self.store.create_user("editor", grants=[{"scope": "all", "path": "ignored", "access": "edit"}])
        self.assertEqual(user["grants"], [{"scope": "all", "path": "", "access": "edit"}])
        self.assertFalse(user["owner"])
        for note_id in ("root.md", "Uncreated/Deep/new.md"):
            self.assertTrue(can_read(user, note_id))
            self.assertTrue(can_edit(user, note_id))
        self.assertTrue(can_create(user, "BrandNew"))
        self.assertTrue(can_create(user, "BrandNew/Sub"))
        for path in ("../secret.md", "/secret.md", "A/../secret.md", "A\\secret.md", ".auth/key.md", "A/CON.md", "*", ""):
            with self.subTest(path=path):
                self.assertFalse(can_read(user, path))
                self.assertFalse(can_edit(user, path))
                self.assertFalse(can_create(user, path))
        disabled = self.store.update_user("editor", enabled=False)
        self.assertFalse(can_read(disabled, "root.md"))
        self.assertFalse(can_edit(disabled, "root.md"))
        self.assertFalse(can_create(disabled, "BrandNew"))

    def test_all_read_combines_with_specific_edit_and_all_edit_deduplicates(self):
        user = self.store.create_user("combined", grants=[
            {"scope": "all", "access": "read"},
            {"scope": "category", "path": "Team", "access": "edit"},
            {"scope": "note", "path": "root.md", "access": "edit"},
        ])
        self.assertTrue(can_read(user, "Private/secret.md"))
        self.assertFalse(can_edit(user, "Private/secret.md"))
        self.assertFalse(can_create(user, "Private"))
        self.assertTrue(can_edit(user, "Team/sub/task.md"))
        self.assertTrue(can_create(user, "Team/Sub"))
        self.assertTrue(can_edit(user, "ROOT.md"))
        self.assertEqual(normalize_grants([
            {"scope": "all", "path": "anything", "access": "read"},
            {"scope": "all", "access": "edit"},
            {"scope": "all", "path": "", "access": "read"},
        ]), [{"scope": "all", "path": "", "access": "edit"}])
        with self.assertRaises(AuthError):
            normalize_grants([{"scope": "category", "path": "*", "access": "edit"}])

    def test_permissions_refresh_and_disabling_revokes_existing_sessions(self):
        self.store.create_user("alice", grants=[{"scope": "category", "path": "Team", "access": "edit"}])
        token = self.login()
        self.assertTrue(can_edit(self.store.get_user(token), "Team/Note.md"))
        self.store.update_user("alice", grants=[])
        self.assertFalse(can_read(self.store.get_user(token), "Team/Note.md"))
        self.store.update_user("alice", enabled=False)
        self.assertIsNone(self.store.get_user(token))
        self.store.update_user("alice", enabled=True)
        self.assertIsNone(self.store.get_user(token))
        another = self.login()
        self.store.delete_user("alice")
        self.store.create_user("alice")
        self.assertIsNone(self.store.get_user(another))

    def test_logout_rejection_expiry_and_persistence(self):
        self.store.create_user("alice")
        token = self.login()
        another = AuthStore(self.path)
        try:
            self.assertEqual(another.get_user(token)["id"], "alice")
            another.logout(token)
            self.assertIsNone(self.store.get_user(token))
        finally:
            another.close()
        pending = self.store.request_device("alice", "192.0.2.1")
        self.store.reject_device(self.store.list_requests()[0]["id"])
        rejected = self.store.device_status(pending["token"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["user_id"], "alice")
        with mock.patch("auth.time.time", return_value=1000):
            owner = self.store.create_owner_token(ttl=10)
            pending = self.store.request_device("alice", "192.0.2.1")
        with mock.patch("auth.time.time", return_value=1011):
            with self.assertRaises(AuthError):
                self.store.consume_owner_token(owner)
        with mock.patch("auth.time.time", return_value=1600):
            self.assertEqual(self.store.device_status(pending["token"])["status"], "expired")
            self.assertEqual(self.store.list_requests(), [])

    def test_raw_tokens_not_stored_and_invalid_tokens_fail_closed(self):
        self.store.create_user("alice")
        token = self.login()
        owner = self.store.create_owner_token()
        content = self.path.read_bytes()
        self.assertNotIn(token.encode(), content)
        self.assertNotIn(owner.encode(), content)
        for invalid in (None, 12, {}, "", "' OR 1=1 --", "a" * 100000):
            self.assertIsNone(self.store.get_session(invalid))
            self.store.logout(invalid)
            with self.assertRaises(AuthError):
                self.store.consume_owner_token(invalid)

    def test_path_and_id_validation_cannot_escape_grants(self):
        for value in ("../bob", "bob smith", "", "风落", "a" * 41, None):
            with self.assertRaises(AuthError):
                self.store.create_user(value)
        for path in ("../Secret", "Work/../Secret", "/Work", "Work/", "Work\\Secret", "Work//Secret", "Work/.secret", "Work/CON", "Work/Note.", "Work/ Note", "Work:Secret", "Work\x00Secret"):
            with self.subTest(path=path):
                with self.assertRaises(AuthError):
                    normalize_grants([{"scope": "category", "path": path, "access": "read"}])
        with self.assertRaises(AuthError):
            normalize_grants([{"scope": "note", "path": "Work/data.json", "access": "read"}])
        self.assertEqual(normalize_grants([
            {"scope": "category", "path": "Work", "access": "edit"},
            {"scope": "category", "path": "work", "access": "read"},
        ]), [{"scope": "category", "path": "Work", "access": "edit"}])
        owner = self.store.list_users()[0]
        self.assertFalse(can_edit(owner, "../secret.md"))

    def test_rate_limit_is_bounded_per_ip_and_globally(self):
        with mock.patch("auth.REQUESTS_PER_IP", 2), mock.patch("auth.REQUESTS_TOTAL", 3):
            self.store.request_device("unknown", "192.0.2.1")
            self.store.request_device("unknown", "192.0.2.1")
            with self.assertRaises(AuthError) as caught:
                self.store.request_device("unknown", "192.0.2.1")
            self.assertEqual(caught.exception.status, 429)
            self.store.request_device("unknown", "192.0.2.2")
            with self.assertRaises(AuthError):
                self.store.request_device("unknown", "192.0.2.3")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
