"""Hand-gesture control: privacy gating, finger counting, and skill start/stop dispatch."""
import unittest

from core.actions import ActionBroker, Blocked
from core.config import SKILLS_DIR
from core.permissions import PermissionRegistry
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase


def _skill():
    reg = SkillRegistry(SKILLS_DIR)
    reg.reload()
    return reg.get("hand_control")


class GatingTests(IsolatedCase):
    def test_start_is_camera_gated_and_dry_runs(self):
        self.assertIn("Would start hand-gesture control",
                      ActionBroker(dry_run=True).start_gesture_control(lambda c: None))
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("camera", "deny")
        with self.assertRaises(Blocked):
            ActionBroker(permissions=perms).start_gesture_control(lambda c: None)

    def test_camera_gate_survives_autonomy(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set_autonomy(True)
        self.assertEqual(perms.state("camera"), "ask")  # hand control (camera) still asks, even unleashed

    def test_stop_when_not_running(self):
        self.assertIn("isn't running", ActionBroker().stop_gesture_control())


class FingerCountTests(unittest.TestCase):
    def test_blank_and_empty_frames_are_zero(self):
        import cv2
        import numpy as np
        from core.gestures import count_fingers
        self.assertEqual(count_fingers(np.zeros((200, 200, 3), np.uint8), cv2, np), 0)
        self.assertEqual(count_fingers(None, cv2, np), 0)


class DispatchTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.skill = _skill()

    def test_start_dispatch_passes_a_runner(self):
        calls = {}

        class A:
            def start_gesture_control(self, runner, emit=None):
                calls["started"] = runner
                return "on"

        out = self.skill.run("watch my hands", {"actions": A(), "run": lambda c: None})
        self.assertIn("started", calls)
        self.assertEqual(out, "on")

    def test_stop_dispatch(self):
        calls = {}

        class A:
            def stop_gesture_control(self):
                calls["stopped"] = True
                return "off"

        out = self.skill.run("stop watching my hands", {"actions": A()})
        self.assertTrue(calls.get("stopped"))
        self.assertEqual(out, "off")

    def test_dry_run(self):
        self.assertIn("Would start", self.skill.run("enable hand control", {"dry_run": True}))
        self.assertIn("Would stop", self.skill.run("stop hand control", {"dry_run": True}))


if __name__ == "__main__":
    unittest.main()
