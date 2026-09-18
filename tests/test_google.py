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


class DocsClientTests(unittest.TestCase):
    def test_insert_requests_style_headings(self):
        from core.google import _doc_insert_requests
        reqs = _doc_insert_requests("# Title\n\n## Section\nBody text.")
        self.assertEqual(reqs[0]["insertText"]["text"], "Title\n\nSection\nBody text.")  # markers stripped
        styles = [r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"]
                  for r in reqs if "updateParagraphStyle" in r]
        self.assertEqual(styles, ["HEADING_1", "HEADING_2"])
        self.assertEqual(_doc_insert_requests("   "), [])

    def test_create_doc_returns_edit_url(self):
        from core.google import GoogleClient
        c = GoogleClient.__new__(GoogleClient)
        c._post = lambda url, body: {"documentId": "DOC123"} if url.endswith("/documents") else {}
        self.assertEqual(c.create_doc("Notes", ""), "https://docs.google.com/document/d/DOC123/edit")

    def test_search_docs_formats(self):
        from core.google import GoogleClient
        c = GoogleClient.__new__(GoogleClient)
        c._get = lambda url: {"files": [{"name": "Budget", "webViewLink": "http://x", "id": "1"}]}
        out = c.search_docs("budget")
        self.assertIn("Budget", out)


class GoogleDocsSkillTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        from core.config import SKILLS_DIR
        from core.skill_loader import SkillRegistry
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        self.skill = reg.get("google_docs")

    def _run(self, request, actions, llm=None):
        return self.skill.run(request, {"actions": actions, "llm": llm})

    def test_create_dispatch(self):
        calls = {}
        actions = type("A", (), {"create_doc": lambda self, t, c="": calls.update(title=t, content=c) or "http://d"})()
        self._run("create a google doc titled Trip Plan about Japan", actions,
                  llm=lambda *a, **k: "## Day 1\nArrive.")
        self.assertEqual(calls["title"], "Trip Plan")
        self.assertIn("Day 1", calls["content"])

    def test_search_dispatch(self):
        calls = {}
        actions = type("A", (), {"search_docs": lambda self, q: calls.update(q=q) or "ok"})()
        self._run("search my google docs for the budget", actions)
        self.assertEqual(calls["q"], "the budget")

    def test_append_dispatch(self):
        calls = {}
        actions = type("A", (), {"append_to_doc": lambda self, n, t: calls.update(name=n, text=t) or "http://d"})()
        self._run("add This is the end. to my google doc Report", actions)
        self.assertEqual(calls["name"], "Report")
        self.assertIn("This is the end", calls["text"])


class SheetsClientTests(unittest.TestCase):
    def test_read_sheet_formats_a_grid(self):
        from core.google import GoogleClient
        c = GoogleClient.__new__(GoogleClient)
        c._resolve_id = lambda n, m: "SID"
        c._get = lambda url: {"values": [["Item", "Cost"], ["Coffee", "4.50"]]}
        out = c.read_sheet("Budget")
        self.assertIn("Item", out)
        self.assertIn("Coffee", out)

    def test_append_row_posts_values(self):
        from core.google import GoogleClient
        calls = {}
        c = GoogleClient.__new__(GoogleClient)
        c._resolve_id = lambda n, m: "SID"
        c._send = lambda method, url, body: calls.update(method=method, body=body) or {}
        out = c.append_row("Expenses", ["Coffee", "4.50"])
        self.assertEqual(calls["method"], "POST")
        self.assertEqual(calls["body"], {"values": [["Coffee", "4.50"]]})
        self.assertIn("Added a row", out)


class GoogleSheetsSkillTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        from core.config import SKILLS_DIR
        from core.skill_loader import SkillRegistry
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        self.skill = reg.get("google_sheets")

    def test_append_dispatch(self):
        calls = {}
        actions = type("A", (), {"append_row": lambda self, n, v: calls.update(n=n, v=v) or "ok"})()
        self.skill.run("add a row to sheet Expenses: Coffee, 4.50", {"actions": actions})
        self.assertEqual(calls["n"], "Expenses")
        self.assertEqual(calls["v"], ["Coffee", "4.50"])

    def test_set_cell_dispatch(self):
        calls = {}
        actions = type("A", (), {"write_sheet": lambda self, n, r, v: calls.update(n=n, r=r, v=v) or "ok"})()
        self.skill.run("set A1 in sheet Log to Done", {"actions": actions})
        self.assertEqual(calls["r"], "A1")
        self.assertEqual(calls["v"], [["Done"]])

    def test_read_dispatch(self):
        calls = {}
        actions = type("A", (), {"read_sheet": lambda self, n, r="A1:Z50": calls.update(n=n, r=r) or "ok"})()
        self.skill.run("read my sheet Budget", {"actions": actions})
        self.assertEqual(calls["n"], "Budget")


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
