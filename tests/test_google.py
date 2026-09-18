import unittest
from unittest import mock

from core import google
from core.actions import ActionBroker, Blocked
from core.permissions import PermissionRegistry
from tests.helpers import IsolatedCase


class GoogleAuthTests(IsolatedCase):
    def test_access_token_refreshes_then_caches(self):
        auth = google.GoogleAuth()
        auth.set_credentials("client-id", "client-secret")
        data = google._load()
        data["refresh_token"] = "refresh"
        google._save(data)

        with mock.patch("core.google._post_token", return_value={"access_token": "AT1", "expires_in": 3600}) as post:
            self.assertEqual(auth.access_token(), "AT1")
            post.assert_called_once()
        # still valid -> no second refresh call
        with mock.patch("core.google._post_token", side_effect=AssertionError("should not refresh")):
            self.assertEqual(auth.access_token(), "AT1")

    def test_not_connected(self):
        self.assertFalse(google.GoogleAuth().is_connected())
        self.assertIsNone(google.GoogleAuth().access_token())


class GoogleClientTests(IsolatedCase):
    def test_drive_search_formats_results(self):
        client = google.GoogleClient(google.GoogleAuth())
        with mock.patch.object(client, "_get", return_value={"files": [
                {"name": "Budget 2026.xlsx", "webViewLink": "https://drive/x"}]}):
            out = client.search_drive("budget")
        self.assertIn("Budget 2026.xlsx", out)
        self.assertIn("https://drive/x", out)

    def test_gmail_search_reads_headers(self):
        client = google.GoogleClient(google.GoogleAuth())

        def fake_get(url):
            if "/messages/" in url:
                return {"payload": {"headers": [{"name": "Subject", "value": "Your invoice"},
                                                {"name": "From", "value": "alice@x.com"}]}}
            return {"messages": [{"id": "1"}]}

        with mock.patch.object(client, "_get", side_effect=fake_get):
            out = client.search_gmail("from:alice")
        self.assertIn("Your invoice", out)
        self.assertIn("alice@x.com", out)


    def test_gmail_organize_archive_removes_inbox_label(self):
        client = google.GoogleClient(google.GoogleAuth())
        posts = []
        with mock.patch.object(client, "_get", return_value={"messages": [{"id": "a"}, {"id": "b"}]}), \
                mock.patch.object(client, "_post", side_effect=lambda url, body: posts.append((url, body)) or {}):
            out = client.gmail_organize("category:promotions", "archive")
        self.assertIn("Archived: 2", out)
        self.assertIn("batchModify", posts[0][0])
        self.assertEqual(posts[0][1]["removeLabelIds"], ["INBOX"])

    def test_gmail_organize_label_creates_and_applies(self):
        client = google.GoogleClient(google.GoogleAuth())
        calls = []

        def fake_post(url, body):
            calls.append((url, body))
            return {"id": "Label_9"} if url.endswith("/labels") else {}

        with mock.patch.object(client, "_get", side_effect=[{"messages": [{"id": "m1"}]}, {"labels": []}]), \
                mock.patch.object(client, "_post", side_effect=fake_post):
            out = client.gmail_organize("from:boss@x.com", "label", "Work")
        self.assertIn("Labelled 'Work'", out)
        self.assertTrue(any(b.get("addLabelIds") == ["Label_9"] for _u, b in calls))


class GmailOrganizeParsingTests(unittest.TestCase):
    def setUp(self):
        from core.config import SKILLS_DIR
        from core.skill_loader import SkillRegistry
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        self.m = reg.get("gmail_organize").module

    def test_query_translates_time_and_stops_from_capture(self):
        self.assertEqual(self.m._query("label emails from noreply newer than 2 days as JARVIS-test"),
                         "from:noreply newer_than:2d")
        self.assertEqual(self.m._query("trash emails from noreply older than 60 days"),
                         "from:noreply older_than:60d")
        self.assertEqual(self.m._query("mark all promotions as read"), "category:promotions")
        self.assertEqual(self.m._query("archive emails from noreply@x.com"), "from:noreply@x.com")

    def test_label_is_the_trailing_as_clause_not_the_verb(self):
        self.assertEqual(self.m._label("label emails from noreply newer than 2 days as JARVIS-test"), "JARVIS-test")
        self.assertEqual(self.m._label("label emails from boss as Work"), "Work")
        self.assertIsNone(self.m._label("archive emails from noreply"))

    def test_actions(self):
        self.assertEqual(self.m._action("archive emails from bob"), "archive")
        self.assertEqual(self.m._action("trash old emails"), "trash")
        self.assertEqual(self.m._action("mark promotions as read"), "read")
        self.assertEqual(self.m._action("label emails as Work"), "label")


class GoogleBrokerTests(IsolatedCase):
    def test_not_connected_message(self):
        broker = ActionBroker(permissions=PermissionRegistry(self.tmp / "p.json"), confirm=lambda req: "once")
        self.assertIn("isn't connected", broker.search_drive("taxes"))

    def test_google_is_gated(self):
        broker = ActionBroker(permissions=PermissionRegistry(self.tmp / "p.json"), confirm=lambda req: "deny")
        with self.assertRaises(Blocked):
            broker.search_gmail("from:bob")

    def test_dry_run_does_not_touch_google(self):
        self.assertIn("unavailable during verification", ActionBroker(dry_run=True).search_drive("x"))


if __name__ == "__main__":
    unittest.main()
