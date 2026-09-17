"""write_file: the generated code is smoke-run and repaired before it's ever saved."""
import unittest

from core.config import SKILLS_DIR
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase


def _module():
    reg = SkillRegistry(SKILLS_DIR)
    reg.reload()
    return reg.get("write_file").module


class StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, prompt, system=None, temperature=0, max_tokens=0):
        self.calls.append(prompt)
        return self.replies.pop(0) if self.replies else ""


class StubActions:
    def __init__(self):
        self.writes = []          # every (path, text) written, in order
        self.installed = []
        self.ran = []

    @property
    def written(self):
        return self.writes[-1] if self.writes else None

    def install_package(self, pkg):
        self.installed.append(pkg)
        return f"installed {pkg}"

    def write_file(self, path, text):
        self.writes.append((path, text))
        return f"Wrote {len(text)} characters to {path}."

    def run_python(self, path):
        self.ran.append(path)
        return f"Running {path}."

    def open_path(self, path):
        self.ran.append(path)
        return f"Opening {path}."


class SmokeRunTests(unittest.TestCase):
    def setUp(self):
        self.wf = _module()

    def test_valid_script_passes(self):
        ok, err = self.wf._smoke_run("print('hello world')\n")
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_structural_errors_are_caught(self):
        for code in ("print(nope)\n", "import totally_missing_pkg_zzz\n", "def f(:\n pass\n"):
            ok, err = self.wf._smoke_run(code)
            self.assertFalse(ok, code)
            self.assertTrue(err)

    def test_interactive_app_is_not_flagged(self):
        # Blank smoke-test input makes int('') raise ValueError - that must NOT be treated as broken.
        ok, _err = self.wf._smoke_run("x = input('n? '); print(int(x) * 2)\n")
        self.assertTrue(ok)

    def test_long_running_app_counts_as_started(self):
        ok, _err = self.wf._smoke_run("import turtle, time\ntime.sleep(10)\n")  # GUI marker -> 3s timeout
        self.assertTrue(ok)


class VerifyRepairTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.wf = _module()

    def test_broken_code_is_repaired_before_saving(self):
        llm = StubLLM(["print('now it works')\n"])  # the fix the model returns
        ctx = {"llm": llm, "actions": StubActions()}
        content, note = self.wf._verify_python("app.py", "print(undefined_name)\n", ctx)
        self.assertEqual(content.strip(), "print('now it works')")
        self.assertIn("verified it runs", note)
        self.assertEqual(len(llm.calls), 1)  # one repair round

    def test_good_code_needs_no_repair(self):
        llm = StubLLM([])  # must not be called
        ctx = {"llm": llm, "actions": StubActions()}
        content, note = self.wf._verify_python("ok.py", "print('fine')\n", ctx)
        self.assertIn("verified it runs", note)
        self.assertEqual(llm.calls, [])

    def test_gives_up_gracefully_after_repeated_failures(self):
        llm = StubLLM(["print(still_broken)\n"] * 6)  # every "fix" is still broken
        ctx = {"llm": llm, "actions": StubActions()}
        _content, note = self.wf._verify_python("bad.py", "print(broken)\n", ctx)
        self.assertIn("still errors", note)


class LauncherAndExtensionTests(IsolatedCase):
    def _run(self, request, replies):
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        actions = StubActions()
        out = reg.get("write_file").run(request, {"llm": StubLLM(replies), "actions": actions})
        return out, actions

    def test_console_app_gets_a_bat_launcher_and_the_app_is_the_last_write(self):
        out, act = self._run("write a python script that prints hi and save it as hi.py",
                             ["print('hi')\n"])
        paths = [p for p, _t in act.writes]
        self.assertTrue(any(p.endswith("hi.bat") for p in paths))   # launcher written
        self.assertTrue(paths[-1].endswith("hi.py"))                # app written last -> stays "last file"
        self.assertIn("hi.bat", out)

    def test_gui_app_is_saved_as_pyw_without_a_launcher(self):
        out, act = self._run("make a tkinter app and save it as app.py",
                             ["import tkinter\nprint('ok')\n"])
        paths = [p for p, _t in act.writes]
        self.assertTrue(any(p.endswith("app.pyw") for p in paths))
        self.assertFalse(any(p.endswith(".bat") for p in paths))    # GUI needs no console launcher
        self.assertIn(".pyw", out)


class TargetPathTests(unittest.TestCase):
    def setUp(self):
        self.wf = _module()

    def test_prefers_the_code_file_when_two_are_named(self):
        target, name = self.wf._target_path("make a qr code, save the image as hi.png and the script as makeqr.py")
        self.assertEqual(name, "makeqr.py")

    def test_dry_run_message(self):
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        skill = reg.get("write_file")
        self.assertIn("Would generate", skill.run("write a snake game and save it as snake.py", {"dry_run": True}))


if __name__ == "__main__":
    unittest.main()
