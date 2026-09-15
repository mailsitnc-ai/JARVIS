import shutil
import unittest

from core.config import SKILLS_DIR, load_settings
from core.orchestrator import Jarvis
from core.skill_loader import SkillRegistry
from memory.learning import LearningStore
from memory.store import MemoryStore
from tests.helpers import FakeLLM, IsolatedCase


class LearningStoreTests(IsolatedCase):
    def store(self):
        return LearningStore(self.tmp / "learn")

    def test_route_hints_match_by_keyword_overlap(self):
        store = self.store()
        self.assertTrue(store.add_route_hint("read me the news", "speak"))
        self.assertEqual(store.hinted_skills("please read me the news"), ["speak"])
        self.assertEqual(store.hinted_skills("open notepad"), [])  # no overlap
        self.assertFalse(store.add_route_hint("read me the news", "speak"))  # duplicate ignored

    def test_teach_persists_and_adds_a_lesson(self):
        self.store().teach("crunch these numbers", "calculator")
        reloaded = self.store()  # fresh instance reads from disk
        self.assertEqual(reloaded.hinted_skills("crunch these numbers for me"), ["calculator"])
        self.assertTrue(any("calculator" in t for t in reloaded.recent_lessons()))

    def test_lessons_are_recent_first_and_deduped(self):
        store = self.store()
        store.add_lesson("build_failed", "boom")
        store.add_lesson("build_failed", "boom")  # dup
        store.add_lesson("skill_crashed", "kaboom")
        self.assertEqual(store.recent_lessons(limit=5), ["boom", "kaboom"])
        self.assertEqual(store.recent_lessons(limit=5, kinds=("skill_crashed",)), ["kaboom"])

    def test_forget_skill(self):
        store = self.store()
        store.add_route_hint("read me the news", "speak")
        self.assertEqual(store.forget_skill("speak"), 1)
        self.assertEqual(store.hinted_skills("read me the news"), [])


class LearningInRoutingTests(IsolatedCase):
    def make(self):
        skills_dir = self.tmp / "skills"
        skills_dir.mkdir()
        for name in ("calculator", "speak", "current_time"):
            shutil.copy(SKILLS_DIR / f"{name}.py", skills_dir)
        return Jarvis(load_settings(), llm=FakeLLM(), registry=SkillRegistry(skills_dir),
                      memory=MemoryStore(self.tmp / "memory"))

    def test_taught_phrase_becomes_a_routing_candidate(self):
        jarvis = self.make()
        self.assertNotIn("calculator", [s.name for s in jarvis.candidates("crunch these numbers")])
        jarvis.learning.teach("crunch these numbers", "calculator")
        self.assertIn("calculator", [s.name for s in jarvis.candidates("crunch these numbers now")])


if __name__ == "__main__":
    unittest.main()
