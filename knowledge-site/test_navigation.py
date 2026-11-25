"""HTTP outline behavior with independent, temporary library fixtures."""

import json
import unittest

import test_auth_http as auth_fixtures


class NavigationHttpTests(unittest.TestCase):
    setUp = auth_fixtures.AuthHttpTests.setUp
    tearDown = auth_fixtures.AuthHttpTests.tearDown
    request = auth_fixtures.AuthHttpTests.request
    owner_login = auth_fixtures.AuthHttpTests.owner_login
    member_login = auth_fixtures.AuthHttpTests.member_login

    def config(self, nodes):
        (self.root / "navigation.json").write_text(json.dumps({"roots": nodes}), encoding="utf-8")

    def tree(self, identity=None, suffix=""):
        status, data, _ = self.request("GET", "/api/notes" + suffix, identity=identity or self.owner)
        self.assertEqual(status, 200)
        self.assertIn("navigation", data)
        return data["navigation"]

    def test_owner_preserves_explicit_order_and_readable_labels(self):
        self.config([{"id": self.readable["id"], "label": "Overview", "ignored": "not public", "children": [
            {"id": self.editable["id"], "label": "First component"},
            {"label": "More", "children": [{"id": self.hidden["id"], "label": "Second component"}]},
        ]}])
        self.assertEqual(self.tree(), [{"id": "Team/Handbook.md", "label": "Overview", "children": [
            {"id": "Drafts/Draft.md", "label": "First component", "children": []},
            {"label": "More", "children": [{"id": "Private/Confidential.md", "label": "Second component", "children": []}]},
        ]}])

    def test_reader_sees_authorized_descendant_without_hidden_parent_metadata(self):
        self.config([{"id": self.hidden["id"], "label": "SECRET_PARENT_LABEL", "children": [
            {"id": self.readable["id"], "label": "Handbook"},
            {"id": self.editable["id"], "label": "SECRET_SIBLING"},
        ]}])
        member = self.member_login(grants=[{"scope": "note", "path": self.readable["id"], "access": "read"}])
        self.assertEqual(self.tree(member), [{"id": "Team/Handbook.md", "label": "Handbook", "children": []}])
        status, body, _ = self.request("GET", "/api/notes", identity=member, raw=True)
        self.assertEqual(status, 200)
        for secret in (b"SECRET_PARENT_LABEL", b"SECRET_SIBLING", b"Private/Confidential", b"Drafts/Draft"):
            self.assertNotIn(secret, body)

    def test_missing_and_deleted_notes_disappear_but_children_survive(self):
        self.config([{"id": self.hidden["id"], "label": "Deleted parent", "children": [
            {"id": self.readable["id"], "label": "Surviving child"},
        ]}, {"label": "Empty group", "children": [{"id": "Gone/Missing.md", "label": "Missing"}]}])
        status, _, _ = self.request("DELETE", "/api/note", {"id": self.hidden["id"], "revision": self.hidden["revision"]}, self.owner)
        self.assertEqual(status, 200)
        self.assertEqual(self.tree(), [{"id": "Team/Handbook.md", "label": "Surviving child", "children": []}])

    def test_search_outline_contains_only_matching_notes(self):
        self.config([{"id": self.hidden["id"], "label": "Private", "children": [
            {"id": self.readable["id"], "label": "Handbook"},
        ]}])
        self.assertEqual(self.tree(suffix="?q=Visible"), [{"id": "Team/Handbook.md", "label": "Handbook", "children": []}])

    def test_missing_or_malformed_config_does_not_break_listing(self):
        self.assertEqual(self.tree(), [])
        for raw in ("{bad", "null", "[]", '{"roots": 3}'):
            with self.subTest(raw=raw):
                (self.root / "navigation.json").write_text(raw, encoding="utf-8")
                self.assertEqual(self.tree(), [])
                status, listing, _ = self.request("GET", "/api/notes", identity=self.owner)
                self.assertEqual(status, 200)
                self.assertEqual(len(listing["notes"]), 3)

    def test_invalid_nodes_duplicates_and_excessive_depth_are_bounded(self):
        deep = {"id": self.hidden["id"], "label": "Too deep"}
        for _ in range(20):
            deep = {"label": "Nested", "children": [deep]}
        self.config([None, "bad", {"id": 1, "label": "Invalid id"},
                     {"id": self.readable["id"], "label": "First"},
                     {"id": self.readable["id"], "label": "Duplicate", "children": [{"id": self.editable["id"], "label": "Child"}]},
                     deep])
        self.assertEqual(self.tree(), [
            {"id": "Team/Handbook.md", "label": "First", "children": []},
            {"id": "Drafts/Draft.md", "label": "Child", "children": []},
        ])


if __name__ == "__main__":
    unittest.main()
