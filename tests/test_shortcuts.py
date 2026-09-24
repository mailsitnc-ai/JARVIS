"""Keyboard shortcuts: phrase parsing, key mapping, and the skills that use them."""
import unittest

from core.config import SKILLS_DIR
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase


class ParseTests(unittest.TestCase):
    def parse(self, text):
        from core.shortcuts import parse
        return parse(text)

    def test_tabs(self):
        self.assertEqual(self.parse("next tab"), ("next_tab", None))
        self.assertEqual(self.parse("previous tab"), ("prev_tab", None))
        self.assertEqual(self.parse("close the tab"), ("close_tab", None))
        self.assertEqual(self.parse("cross this tab"), ("close_tab", None))
        self.assertEqual(self.parse("open a new tab"), ("new_tab", None))
        self.assertEqual(self.parse("reopen that tab"), ("reopen_tab", None))

    def test_tab_by_number(self):
        self.assertEqual(self.parse("go to tab 3"), ("tab_n", 3))
        self.assertEqual(self.parse("switch to tab number 5"), ("tab_n", 5))
        self.assertEqual(self.parse("tab two"), ("tab_n", 2))
        self.assertEqual(self.parse("tab 9"), ("tab_n", 9))          # 9 = rightmost, as browsers do
        self.assertEqual(self.parse("jump to the last tab"), ("prev_tab", None))   # "last" = previous

    def test_apps_desktops_screen(self):
        self.assertEqual(self.parse("switch app"), ("switch_app", None))
        self.assertEqual(self.parse("next desktop"), ("next_desktop", None))
        self.assertEqual(self.parse("previous space"), ("prev_desktop", None))
        self.assertEqual(self.parse("lock the screen"), ("lock", None))
        self.assertEqual(self.parse("put the mac to sleep"), ("sleep", None))

    def test_nothing_recognised(self):
        self.assertEqual(self.parse("what's the weather"), (None, None))


class KeyMappingTests(unittest.TestCase):
    def test_every_shortcut_maps_to_a_key_this_platform_knows(self):
        from core import oslayer
        from core.shortcuts import SHORTCUTS
        table = oslayer._MAC_VK if oslayer.IS_MAC else oslayer._WIN_VK
        for name, (key, mods) in SHORTCUTS.items():
            with self.subTest(name):
                self.assertTrue(key in table or len(key) == 1, f"{name}: unknown key {key!r}")
                for mod in mods:
                    self.assertIn(mod, ("cmd", "ctrl", "shift", "alt", "fn"))

    def test_press_reports_what_it_did(self):
        from unittest import mock

        from core import shortcuts
        with mock.patch("core.oslayer.key_press", return_value=True) as press:
            self.assertIn("Next tab", shortcuts.press("next_tab"))
            self.assertEqual(press.call_args[0][0], "tab")
        with mock.patch("core.oslayer.key_press", return_value=True) as press:
            shortcuts.press("tab_n", 3)
            self.assertEqual(press.call_args[0][0], "3")
        self.assertIn("Which tab", shortcuts.press("tab_n", None))

    def test_a_refused_keypress_explains_the_permission(self):
        from unittest import mock

        from core import shortcuts
        with mock.patch("core.oslayer.key_press", return_value=False):
            self.assertIn("Accessibility", shortcuts.press("next_tab"))


class SkillTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.reg = SkillRegistry(SKILLS_DIR)
        self.reg.reload()

    def names(self, text):
        return [s.name for s, _ in self.reg.match(text)]

    def test_routing(self):
        for text in ("next tab", "close the tab", "go to tab 3", "switch app", "next desktop",
                     "lock the screen", "reopen that tab"):
            self.assertIn("shortcuts", self.names(text), text)
        self.assertEqual(self.names("type hello everyone")[0], "type_text")

    def test_disruptive_shortcuts_go_through_the_broker(self):
        calls = []

        class A:
            def run_shortcut(self, name, number=None):
                calls.append((name, number))
                return "locked"
        self.reg.get("shortcuts").run("lock the screen", {"actions": A()})
        self.assertEqual(calls, [("lock", None)])

    def test_dry_run(self):
        self.assertIn("Would press", self.reg.get("shortcuts").run("next tab", {"dry_run": True}))
        self.assertIn("Would type", self.reg.get("type_text").run("type hello", {"dry_run": True}))

    def test_type_text_strips_the_command_words(self):
        typed = []

        class A:
            def type_text(self, text):
                typed.append(text)
                return "Typed: " + text
        for said, expect in (("type hello everyone", "hello everyone"),
                             ("Jarvis, type out see you at six", "see you at six"),
                             ('type this: "meeting at four"', "meeting at four")):
            self.reg.get("type_text").run(said, {"actions": A()})
            self.assertEqual(typed[-1], expect, said)


if __name__ == "__main__":
    unittest.main()
