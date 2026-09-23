"""Global hotkey matching - the macOS Ctrl+<letter> control-character trap."""
import unittest

from window_manager.hotkey import ComboMatcher, HotkeyListener


class _Key:
    """Stands in for a pynput key object."""
    def __init__(self, char=None, name=None):
        self.char, self.name = char, name

    def __str__(self):
        return f"Key.{self.name}" if self.name else str(self.char)


CTRL, SHIFT, CMD = _Key(name="ctrl_l"), _Key(name="shift"), _Key(name="cmd")


class ParseTests(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(HotkeyListener.parse_spec("ctrl+shift+j"), ({"ctrl", "shift"}, "j"))
        self.assertEqual(HotkeyListener.parse_spec("ctrl+alt+c"), ({"ctrl", "alt"}, "c"))
        self.assertEqual(HotkeyListener.parse_spec("cmd+shift+space"), ({"cmd", "shift"}, "space"))
        self.assertEqual(HotkeyListener.parse_spec("ctrl+shift+3"), ({"ctrl", "shift"}, "3"))

    def test_control_characters_map_back_to_letters(self):
        """macOS sends Ctrl+J as chr(10) and Ctrl+C as chr(3) - the reason the hotkey silently died."""
        self.assertEqual(HotkeyListener.key_identity(_Key(char=chr(10))), "j")
        self.assertEqual(HotkeyListener.key_identity(_Key(char=chr(3))), "c")
        self.assertEqual(HotkeyListener.key_identity(_Key(char="J")), "j")
        self.assertEqual(HotkeyListener.key_identity(_Key(name="space")), "space")
        self.assertIsNone(HotkeyListener.key_identity(CTRL))


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.fired = []
        self.m = ComboMatcher({"ctrl", "shift"}, "j", lambda: self.fired.append(1))

    def press(self, *keys):
        for k in keys:
            self.m.press(k)

    def test_ctrl_shift_j_fires_even_as_a_control_character(self):
        self.press(CTRL, SHIFT, _Key(char=chr(10)))
        self.assertEqual(len(self.fired), 1)

    def test_plain_letter_fires_too(self):
        self.press(CTRL, SHIFT, _Key(char="J"))
        self.assertEqual(len(self.fired), 1)

    def test_the_letter_alone_does_nothing(self):
        self.press(_Key(char="j"))
        self.assertEqual(self.fired, [])

    def test_a_missing_modifier_does_nothing(self):
        self.press(CTRL, _Key(char=chr(10)))
        self.assertEqual(self.fired, [])

    def test_releasing_a_modifier_disarms_it(self):
        self.press(CTRL, SHIFT)
        self.m.release(SHIFT)
        self.press(_Key(char=chr(10)))
        self.assertEqual(self.fired, [])

    def test_extra_modifiers_still_fire(self):
        self.press(CMD, CTRL, SHIFT, _Key(char=chr(10)))
        self.assertEqual(len(self.fired), 1)

    def test_a_different_letter_does_nothing(self):
        self.press(CTRL, SHIFT, _Key(char=chr(3)))       # Ctrl+C
        self.assertEqual(self.fired, [])

    def test_named_keys(self):
        fired = []
        m = ComboMatcher({"ctrl", "shift"}, "space", lambda: fired.append(1))
        m.press(CTRL), m.press(SHIFT), m.press(_Key(name="space"))
        self.assertEqual(len(fired), 1)


if __name__ == "__main__":
    unittest.main()
