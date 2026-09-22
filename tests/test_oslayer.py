"""Cross-platform OS layer: platform-correct commands and safe fallbacks (core.oslayer)."""
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from core import oslayer
import tests.helpers  # noqa: F401  - redirects JARVIS_DATA_DIR away from the real user data


class PlatformFlagTests(unittest.TestCase):
    def test_exactly_one_platform_flag(self):
        self.assertEqual(sum([oslayer.IS_WINDOWS, oslayer.IS_MAC, oslayer.IS_LINUX]), 1)
        self.assertIn(oslayer.PLATFORM_NAME, ("windows", "mac", "linux"))

    def test_user_data_dir_exists_and_named(self):
        path = oslayer.user_data_dir()
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "JARVIS")


class SpeakCommandTests(unittest.TestCase):
    def test_speak_command_shape_per_platform(self):
        with mock.patch.multiple(oslayer, IS_WINDOWS=True, IS_MAC=False, IS_LINUX=False):
            self.assertEqual(oslayer.speak_command("hi")[0], "powershell")
        with mock.patch.multiple(oslayer, IS_WINDOWS=False, IS_MAC=True, IS_LINUX=False):
            self.assertEqual(oslayer.speak_command("hi"), ["say", "hi"])
        with mock.patch.multiple(oslayer, IS_WINDOWS=False, IS_MAC=False, IS_LINUX=True):
            self.assertEqual(oslayer.speak_command("hi"), ["spd-say", "hi"])


class MacAppTests(unittest.TestCase):
    def test_aliases_map_windows_names_to_mac_apps(self):
        self.assertEqual(oslayer._mac_app_name("chrome"), "Google Chrome")
        self.assertEqual(oslayer._mac_app_name("settings"), "System Settings")
        self.assertEqual(oslayer._mac_app_name("terminal"), "Terminal")
        self.assertEqual(oslayer._mac_app_name("SomeUnknownApp"), "SomeUnknownApp")

    def test_mac_app_exists_false_off_mac(self):
        with mock.patch.object(oslayer, "IS_MAC", False):
            self.assertFalse(oslayer.mac_app_exists("Safari"))


class GuiDetectionTests(unittest.TestCase):
    def test_pyw_is_gui(self):
        self.assertTrue(oslayer.is_gui_python(Path("app.pyw")))
        self.assertFalse(oslayer.is_gui_python(Path("app.py")))


class OpenPathTests(unittest.TestCase):
    def test_mac_open_path_uses_open(self):
        with mock.patch.multiple(oslayer, IS_WINDOWS=False, IS_MAC=True, IS_LINUX=False), \
                mock.patch.object(oslayer, "_popen") as popen:
            oslayer.open_path("/tmp/x.txt")
            popen.assert_called_once()
            self.assertEqual(popen.call_args[0][0][0], "open")

    def test_linux_open_path_uses_xdg_open(self):
        with mock.patch.multiple(oslayer, IS_WINDOWS=False, IS_MAC=False, IS_LINUX=True), \
                mock.patch.object(oslayer, "_popen") as popen:
            oslayer.open_path("/tmp/x.txt")
            self.assertEqual(popen.call_args[0][0][0], "xdg-open")


if __name__ == "__main__":
    unittest.main()
