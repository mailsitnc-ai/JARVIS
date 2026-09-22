"""Clipboard tools, reminders, file finder, document Q&A and the performance doctor."""
import datetime as dt
import json
import unittest
from pathlib import Path

from core.config import SKILLS_DIR
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase

NOW = dt.datetime(2026, 9, 22, 16, 0)


class ReminderParsingTests(unittest.TestCase):
    def when(self, text):
        from core.reminders import parse_when
        w = parse_when(text, NOW)
        return w and w.strftime("%a %H:%M")

    def test_relative(self):
        self.assertEqual(self.when("remind me in 20 minutes to check the oven"), "Tue 16:20")
        self.assertEqual(self.when("remind me in an hour and a half to stretch"), "Tue 17:30")
        self.assertEqual(self.when("remind me in 1 hour 30 minutes to leave"), "Tue 17:30")
        self.assertEqual(self.when("remind me in half an hour to take the pizza out"), "Tue 16:30")
        self.assertEqual(self.when("set a timer for 25 minutes"), "Tue 16:25")
        self.assertEqual(self.when("remind me to drink water in 45 mins"), "Tue 16:45")

    def test_absolute(self):
        self.assertEqual(self.when("remind me at 6pm to call Maa"), "Tue 18:00")
        self.assertEqual(self.when("remind me at 6 to go to chess"), "Tue 18:00")        # 4pm now -> 6pm
        self.assertEqual(self.when("remind me tomorrow at 9 to submit the essay"), "Wed 09:00")
        self.assertEqual(self.when("remind me tonight at 8 about MUN prep"), "Tue 20:00")
        self.assertEqual(self.when("remind me at 3:30 pm to join the call"), "Wed 15:30")  # already passed
        self.assertEqual(self.when("remind me at noon tomorrow to email Stuti"), "Wed 12:00")
        self.assertIsNone(self.when("remind me to call Maa"))

    def test_what(self):
        from core.reminders import parse_what
        self.assertEqual(parse_what("remind me tomorrow at 9 to submit the essay"), "submit the essay")
        self.assertEqual(parse_what("remind me tonight at 8 about MUN prep"), "MUN prep")
        self.assertEqual(parse_what("remind me to drink water in 45 mins"), "drink water")


class ReminderStoreTests(IsolatedCase):
    def test_add_list_pop_cancel(self):
        from core.reminders import ReminderStore, announcement
        store = ReminderStore(self.tmp / "r.json")
        store.add(NOW + dt.timedelta(minutes=5), "call Maa")
        store.add(NOW + dt.timedelta(minutes=1), "timer", "timer")
        self.assertEqual([i["text"] for i in store.list()], ["timer", "call Maa"])
        due = store.pop_due((NOW + dt.timedelta(minutes=2)).timestamp())
        self.assertEqual([i["text"] for i in due], ["timer"])
        self.assertIn("timer", announcement(due[0], (NOW + dt.timedelta(minutes=2)).timestamp()))
        self.assertEqual(len(store.cancel("maa")), 1)
        self.assertEqual(store.list(), [])

    def test_late_reminders_say_so(self):
        from core.reminders import announcement
        item = {"text": "call Maa", "kind": "reminder", "due": 1000.0}
        self.assertIn("late", announcement(item, 1000.0 + 3600))
        self.assertNotIn("late", announcement(item, 1001.0))


class _Broker:
    """Records calls; stands in for the ActionBroker."""
    def __init__(self, clip="", files=()):
        self.clip, self.files, self.calls = clip, list(files), []

    def read_clipboard(self):
        return self.clip

    def write_clipboard(self, text):
        self.calls.append(("write_clipboard", text))
        return "Copied to your clipboard."

    def find_files(self, **kw):
        self.calls.append(("find_files", kw))
        return self.files

    def remember_focus(self, slot, value):
        self.calls.append(("focus", slot, str(value)))

    def open_path(self, p):
        self.calls.append(("open", p))

    def read_document(self, p):
        return Path(p).read_text()

    def quit_app(self, name):
        self.calls.append(("quit", name))
        return f"Asked {name} to quit."


def _skill(name):
    reg = SkillRegistry(SKILLS_DIR)
    reg.reload()
    return reg.get(name)


class ClipboardToolTests(IsolatedCase):
    def test_transform_goes_back_on_the_clipboard(self):
        b = _Broker("their late")
        out = _skill("clipboard_tools").run("fix the grammar in what I copied",
                                             {"actions": b, "llm": lambda *a, **k: "They're late."})
        self.assertIn(("write_clipboard", "They're late."), b.calls)
        self.assertIn("They're late.", out)

    def test_questions_do_not_overwrite_the_clipboard(self):
        b = _Broker("def f(): pass")
        _skill("clipboard_tools").run("explain the code I copied", {"actions": b, "llm": lambda *a, **k: "It does nothing."})
        self.assertFalse([c for c in b.calls if c[0] == "write_clipboard"])

    def test_empty_clipboard(self):
        self.assertIn("empty", _skill("clipboard_tools").run("summarise what I copied", {"actions": _Broker("")}))


class FileFinderTests(IsolatedCase):
    def test_parse(self):
        from skills.file_finder import parse
        self.assertEqual(parse("find the chemistry pdf I downloaded last week"), (["chemistry"], ["pdf"], 8))
        words, exts, days = parse("find screenshots from today")
        self.assertEqual((words, days), ([], 1))
        self.assertIn("png", exts)

    def test_falls_back_to_searching_inside_documents(self):
        f = self.tmp / "handout.pdf"
        f.write_text("x")

        class B(_Broker):
            def find_files(self, **kw):
                self.calls.append(("find_files", kw))
                return [f] if "content" in kw else []
        b = B()
        out = _skill("file_finder").run("find the geography pdf", {"actions": b})
        self.assertIn("handout.pdf", out)
        self.assertIn(("focus", "file", str(f)), b.calls)       # becomes "it"


class DocumentQATests(IsolatedCase):
    def test_summarise_it_uses_the_focused_file(self):
        f = self.tmp / "notes.txt"
        f.write_text("Photosynthesis turns light into chemical energy.")
        seen = {}

        def llm(prompt, **_):
            seen["prompt"] = prompt
            return "A summary."
        out = _skill("document_qa").run("summarise it", {"actions": _Broker(), "llm": llm, "focus": {"file": str(f)}})
        self.assertIn("Photosynthesis", seen["prompt"])
        self.assertIn("A summary.", out)

    def test_no_document_asks_which(self):
        self.assertIn("Which document", _skill("document_qa").run("summarise it", {"actions": _Broker(),
                                                                                  "llm": lambda *a, **k: ""}))


class PerformanceTests(IsolatedCase):
    def test_quit_maps_aliases_and_never_quits_itself(self):
        b = _Broker()
        _skill("performance").run("quit chrome", {"actions": b})
        self.assertIn(("quit", "Google Chrome"), b.calls)
        self.assertIn("rather not", _skill("performance").run("quit jarvis", {"actions": b}))


class ToolRoutingTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.reg = SkillRegistry(SKILLS_DIR)
        self.reg.reload()

    def names(self, text):
        return {s.name for s, _ in self.reg.match(text)}

    def test_routes(self):
        cases = {
            "summarise what I copied": "clipboard_tools", "translate my clipboard to French": "clipboard_tools",
            "remind me in 20 minutes to check the oven": "reminders", "set a timer for 5 minutes": "reminders",
            "what are my reminders": "reminders", "find the chemistry pdf from last week": "file_finder",
            "summarise it": "document_qa", "quiz me on it": "document_qa",
            "summarise /Users/me/Downloads/What-Makes-Population-Change.pdf": "document_qa",
            "why is my mac slow": "performance", "quit spotify": "performance",
        }
        for text, skill in cases.items():
            self.assertIn(skill, self.names(text), text)

    def test_no_hijacking(self):
        self.assertNotIn("file_finder", self.names("find my INS document on google docs"))
        self.assertNotIn("document_qa", self.names("summarize my google docs Report"))
        self.assertNotIn("performance", self.names("quit hand control"))
        self.assertNotIn("performance", self.names("send a whatsapp to mom saying quit it"))
        self.assertNotIn("reminders", self.names("whatsapp Inaya saying hi"))


if __name__ == "__main__":
    unittest.main()
