"""fix_code: resolve the target file, repair it against the symptom, verify, back up, save."""
import unittest

from core.config import SKILLS_DIR
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase


def _skill():
    reg = SkillRegistry(SKILLS_DIR)
    reg.reload()
    return reg.get("fix_code")


class StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, prompt, system=None, temperature=0, max_tokens=0):
        self.calls.append(prompt)
        return self.replies.pop(0) if self.replies else ""


class StubActions:
    def __init__(self, file_text=""):
        self.file_text = file_text
        self.writes = []
        self.ran = []

    def read_file(self, path):
        return self.file_text

    def write_file(self, path, text):
        self.writes.append((path, text))
        return f"Wrote {len(text)} characters to {path}."

    def run_python(self, path):
        self.ran.append(path)
        return f"Running {path}."

    def install_package(self, pkg):
        return f"installed {pkg}"

    def last_written(self):
        return None


class ResolveTargetTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.skill = _skill()

    def _resolve(self, request, focus=None, actions=None):
        ctx = {"actions": actions or StubActions()}
        if focus is not None:
            ctx["focus"] = focus
        return self.skill.module._resolve_target(request, ctx)

    def test_explicit_path(self):
        f = self.tmp / "app.py"
        f.write_text("x=1", encoding="utf-8")
        self.assertEqual(self._resolve(f"fix {f} please"), f)

    def test_bare_name_via_focus(self):
        f = self.tmp / "time.py"
        f.write_text("x=1", encoding="utf-8")
        self.assertEqual(self._resolve("fix time.py", focus={"file": str(f)}), f)

    def test_no_name_falls_back_to_focus_file(self):
        f = self.tmp / "weather.py"
        f.write_text("x=1", encoding="utf-8")
        self.assertEqual(self._resolve("the app still isn't working", focus={"file": str(f)}), f)

    def test_unknown_named_file_is_none(self):
        self.assertIsNone(self._resolve("fix nonexistent_zzz.py"))


class RunTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.skill = _skill()

    def test_declines_when_not_about_code(self):
        # No file, no code words -> must not hijack (would otherwise answer wrongly).
        self.assertIsNone(self.skill.run("the printer isn't working", {"actions": StubActions()}))

    def test_asks_which_file_when_named_but_missing(self):
        out = self.skill.run("fix gone.py", {"actions": StubActions(), "llm": StubLLM([])})
        self.assertIn("Which file", out)

    def test_fixes_backs_up_and_saves(self):
        f = self.tmp / "time.py"
        f.write_text("print(broken_thing)\n", encoding="utf-8")
        actions = StubActions(file_text="print(broken_thing)\n")
        llm = StubLLM(["print('fixed now')\n"])
        out = self.skill.run(f"fix {f} - it crashes", {"actions": actions, "llm": llm})
        paths = [p for p, _t in actions.writes]
        self.assertTrue(any(p.endswith("time.py.bak") for p in paths))  # original backed up
        self.assertTrue(any(p.endswith("time.py") and not p.endswith(".bak") for p in paths))
        # the backup holds the ORIGINAL, the file holds the FIX
        backup_text = next(t for p, t in actions.writes if p.endswith(".bak"))
        self.assertIn("broken_thing", backup_text)
        self.assertIn("Fixed time.py", out)

    def test_unchanged_result_is_reported(self):
        f = self.tmp / "app.py"
        f.write_text("print('same')\n", encoding="utf-8")
        actions = StubActions(file_text="print('same')\n")
        llm = StubLLM(["print('same')\n"])          # model returns it unchanged
        out = self.skill.run(f"fix {f}", {"actions": actions, "llm": llm})
        self.assertIn("unchanged", out)
        self.assertEqual(actions.writes, [])         # nothing written when there's no change


if __name__ == "__main__":
    unittest.main()
