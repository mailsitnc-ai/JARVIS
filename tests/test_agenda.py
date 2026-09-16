import datetime
import unittest

from core.agenda import AgendaStore, from_iso, next_run, to_iso
from core.config import load_settings
from core.orchestrator import Jarvis
from core.skill_loader import SkillRegistry
from memory.store import MemoryStore
from tests.helpers import FakeLLM, IsolatedCase

BASE = datetime.datetime(2026, 1, 1, 10, 0, 0)


class ScheduleTests(unittest.TestCase):
    def test_next_run_interval_daily_once(self):
        self.assertEqual(next_run({"type": "interval", "seconds": 300}, BASE),
                         BASE + datetime.timedelta(seconds=300))
        self.assertEqual(next_run({"type": "interval", "seconds": 5}, BASE),
                         BASE + datetime.timedelta(seconds=30))  # floored at 30s
        self.assertEqual(next_run({"type": "daily", "time": "18:00"}, BASE).hour, 18)   # later today
        rolled = next_run({"type": "daily", "time": "08:00"}, BASE)                     # already past -> tomorrow
        self.assertEqual((rolled.day, rolled.hour), (2, 8))
        self.assertEqual(next_run({"type": "once", "at": to_iso(BASE)},
                                  datetime.datetime(2026, 1, 1, 9, 0, 0)), BASE)


class AgendaStoreTests(IsolatedCase):
    def store(self):
        return AgendaStore(self.tmp / "agenda.json")

    def test_add_due_and_reschedule(self):
        store = self.store()
        task = store.add("T", "do it", {"type": "interval", "seconds": 60}, at=BASE)
        self.assertEqual(len(store.list()), 1)
        self.assertEqual(store.due(BASE), [])                       # next_run is BASE+60, not due yet
        later = BASE + datetime.timedelta(seconds=61)
        self.assertEqual([t["id"] for t in store.due(later)], [task["id"]])
        store.mark_ran(task["id"], "ok", later)
        again = store.get(task["id"])
        self.assertEqual(again["runs"], 1)
        self.assertEqual(from_iso(again["next_run"]), later + datetime.timedelta(seconds=60))

    def test_once_disables_after_running(self):
        store = self.store()
        t = store.add("O", "once", {"type": "once", "at": to_iso(BASE)}, at=BASE)
        store.mark_ran(t["id"], "done", BASE)
        self.assertFalse(store.get(t["id"])["enabled"])

    def test_remove_and_master_switch(self):
        store = self.store()
        t = store.add("T", "x", {"type": "interval", "seconds": 60})
        self.assertTrue(store.remove(t["id"]))
        self.assertEqual(store.list(), [])
        self.assertTrue(store.enabled())     # default on
        store.set_enabled(False)
        self.assertFalse(AgendaStore(self.tmp / "agenda.json").enabled())  # persisted


class ReflectTests(IsolatedCase):
    def make(self):
        return Jarvis(load_settings(), llm=FakeLLM(), registry=SkillRegistry(self.tmp / "skills"),
                      memory=MemoryStore(self.tmp / "memory"))

    def test_reflect_with_no_evolved_skills_is_a_noop(self):
        jarvis = self.make()
        self.assertIn("no evolved skills", jarvis.reflect().lower())

    def test_run_agenda_task_routes_reflection(self):
        jarvis = self.make()
        result = jarvis.run_agenda_task({"kind": "reflection", "title": "r", "prompt": "__reflect__"})
        self.assertIn("reflection", result.lower())


if __name__ == "__main__":
    unittest.main()
