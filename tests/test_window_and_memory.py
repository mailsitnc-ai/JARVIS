import unittest

from memory.store import MemoryStore
from tests.helpers import IsolatedCase
from window_manager import ipc, win32
from window_manager.hotkey import parse_hotkey
from window_manager.ipc import ControlServer
from window_manager.split import compute_layout
from window_manager.win32 import Rect


class LayoutTests(unittest.TestCase):
    def test_sixty_forty_on_this_laptop(self):
        main, jarvis = compute_layout(Rect(0, 0, 1280, 760), 0.6, "right")
        self.assertEqual(main, Rect(0, 0, 768, 760))
        self.assertEqual(jarvis, Rect(768, 0, 512, 760))

    def test_left_side_and_offset_monitor(self):
        main, jarvis = compute_layout(Rect(1920, 40, 1000, 1000), 0.6, "left")
        self.assertEqual(jarvis, Rect(1920, 40, 400, 1000))
        self.assertEqual(main, Rect(2320, 40, 600, 1000))

    def test_work_area_is_real(self):
        area = win32.work_area()
        self.assertGreater(area.width, 0)
        self.assertGreater(area.height, 0)


class HotkeyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hotkey("ctrl+alt+j"), (0x3, ord("J")))
        self.assertEqual(parse_hotkey("Win+Shift+F5"), (0xC, 0x74))
        self.assertEqual(parse_hotkey("ctrl+space"), (0x2, 0x20))
        self.assertEqual(parse_hotkey("ctrl+alt+c"), (0x3, ord("C")))  # the interrupt hotkey
        for bad in ("j", "ctrl+alt", "ctrl+a+b", "ctrl+nope"):
            with self.assertRaises(ValueError):
                parse_hotkey(bad)

    def test_listener_hotkey_id_is_configurable(self):
        from window_manager.hotkey import HotkeyListener

        toggle = HotkeyListener("ctrl+alt+j", lambda: None)  # not started: no real registration
        interrupt = HotkeyListener("ctrl+alt+c", lambda: None, hotkey_id=0x4A42, name="x")
        self.assertNotEqual(toggle.hotkey_id, interrupt.hotkey_id)  # so both can coexist


class ControlChannelTests(IsolatedCase):
    def test_round_trip_and_shutdown(self):
        server = ControlServer(lambda cmd: {"ok": True, "echo": cmd}, port=0)
        server.start()
        try:
            self.assertEqual(ipc.send("ping"), {"ok": True, "echo": "ping"})
        finally:
            server.close()
        self.assertIsNone(ipc.send("ping", timeout=0.5))


class MemoryTests(IsolatedCase):
    def test_record_and_recall(self):
        store = MemoryStore(self.tmp / "memory")
        store.record("convert 30 celsius to fahrenheit", "30 °C = 86 °F", "temperature_converter", "trigger")
        store.record("open notepad", "Opening Notepad.", "open_app", "trigger")
        store.record("what is 2+2", "4", "calculator", "trigger")
        recalled = store.recall("fahrenheit to celsius please", k=1)
        self.assertEqual(recalled[0]["skill"], "temperature_converter")

    def test_trims_old_interactions(self):
        store = MemoryStore(self.tmp / "memory", max_interactions=100)
        for i in range(320):
            store.record(f"request {i}", "ok")
        entries = store.interactions()
        self.assertLessEqual(len(entries), 300)
        self.assertEqual(entries[-1]["request"], "request 319")


if __name__ == "__main__":
    unittest.main()
