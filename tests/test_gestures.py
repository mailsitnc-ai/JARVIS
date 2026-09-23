"""Hand-gesture control: privacy gating, finger counting, and skill start/stop dispatch."""
import math
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


def _hand(fingers="01000", thumb_to=None, dx=0.0, dy=0.0):
    """Synthetic 21-point hand. fingers = thumb,index,middle,ring,pinky as 1 (up) / 0 (curled).
    thumb_to = 'index'/'middle' puts the thumb tip on that fingertip (a pinch)."""
    pts = [(0.5, 0.8)]
    thumb_up = fingers[0] == "1"
    pts += [(0.42, 0.75), (0.37, 0.70), (0.33, 0.66), (0.29, 0.62)] if thumb_up else \
           [(0.44, 0.75), (0.45, 0.70), (0.46, 0.67), (0.47, 0.66)]
    for x, up in zip((0.44, 0.49, 0.54, 0.59), fingers[1:]):
        pts += [(x, 0.6), (x, 0.5), (x, 0.45), (x, 0.40)] if up == "1" else \
               [(x, 0.6), (x, 0.55), (x, 0.60), (x, 0.63)]
    if thumb_to:
        tip = {"index": 8, "middle": 12}[thumb_to]
        pts[4] = (pts[tip][0] - 0.01, pts[tip][1] + 0.01)
    return [(x + dx, y + dy) for x, y in pts]


class HandInterpreterTests(unittest.TestCase):
    def setUp(self):
        from core.gestures import HandInterpreter
        self.h = HandInterpreter((1000, 800))
        self.t = 0.0

    def feed(self, pts, frames=1, dt=1 / 30):
        acts = []
        for _ in range(frames):
            self.t += dt
            acts += self.h.update(pts, self.t)
        return acts

    def kinds(self, acts):
        return [a[0] for a in acts]

    def test_poses(self):
        c = self.h.classify
        self.assertEqual(c(_hand("01000")), "point")
        self.assertEqual(c(_hand("01100")), "two")
        self.assertEqual(c(_hand("01110")), "three")
        self.assertEqual(c(_hand("01111")), "four")
        self.assertEqual(c(_hand("11111")), "palm")
        self.assertEqual(c(_hand("00000")), "fist")

    def test_point_moves_the_pointer_the_way_the_hand_moves(self):
        self.feed(_hand("01000"), 10)                      # settle, pointing
        start = self.h.cursor
        for i in range(1, 12):                             # slide the hand right and down
            self.feed(_hand("01000", dx=0.01 * i, dy=0.005 * i), 1)
        self.assertGreater(self.h.cursor[0], start[0])
        self.assertGreater(self.h.cursor[1], start[1])

    def test_a_resting_hand_does_not_drift(self):
        self.feed(_hand("01000"), 10)
        at = self.h.cursor
        self.assertEqual(self.kinds(self.feed(_hand("01000"), 30)), [])   # held still: no movement at all
        self.assertEqual(self.h.cursor, at)

    def test_dropping_and_raising_the_hand_re_centres_instead_of_jumping(self):
        """The point of trackpad mode: rest your arm, bring the hand back anywhere, carry on."""
        self.feed(_hand("01000"), 10)
        for i in range(1, 10):
            self.feed(_hand("01000", dx=0.01 * i), 1)
        moved_to = self.h.cursor
        self.feed(None, 3)                                  # hand out of view (resting on the desk)
        self.feed(_hand("01000", dx=-0.3, dy=0.25), 8)      # back, much lower and to the left
        self.assertLess(math.dist(self.h.cursor, moved_to), 60)

    def test_a_fast_flick_travels_further_than_the_same_slow_move(self):
        from core.gestures import HandInterpreter
        def travel(frames_per_step):
            h, t = HandInterpreter((1000, 800)), 0.0
            for f in range(10):
                t += 1 / 30
                h.update(_hand("01000"), t)
            start = h.cursor
            for i in range(1, 9):
                for _ in range(frames_per_step):
                    t += 1 / 30
                    h.update(_hand("01000", dx=0.02 * i), t)
            return abs(h.cursor[0] - start[0])
        self.assertGreater(travel(1), travel(4) * 1.15)     # same distance, faster hand -> further

    def test_pinch_clicks_and_releases(self):
        self.feed(_hand("01000"), 10)
        acts = self.feed(_hand("01000", thumb_to="index"), 3)
        self.assertIn(("down", "left", 1), acts)
        acts = self.feed(_hand("01000"), 3)
        self.assertIn(("up", "left"), acts)

    def test_quick_second_pinch_is_a_double_click(self):
        self.feed(_hand("01000"), 10)
        self.feed(_hand("01000", thumb_to="index"), 2)
        self.feed(_hand("01000"), 3)
        acts = self.feed(_hand("01000", thumb_to="index"), 2)
        self.assertIn(("down", "left", 2), acts)

    def test_held_pinch_drags_after_the_freeze(self):
        self.feed(_hand("01000"), 10)
        self.feed(_hand("01000", thumb_to="index"), 2)
        acts = self.feed(_hand("01000", thumb_to="index", dx=0.1), 20)
        self.assertIn("drag", self.kinds(acts))

    def test_thumb_to_middle_with_index_folded_is_a_right_click(self):
        self.feed(_hand("00100"), 10)
        acts = self.feed(_hand("00100", thumb_to="middle"), 3)
        self.assertIn(("down", "right", 1), acts)

    # --- misfires seen in the user's real session log (2026-09-22 22:06) ---
    def test_thumb_near_middle_while_scrolling_does_not_right_click(self):
        self.feed(_hand("01100"), 10)
        acts = self.feed(_hand("01100", thumb_to="middle"), 10)
        self.assertNotIn("down", self.kinds(acts))

    def test_opening_a_fist_through_a_pinch_shape_does_not_click(self):
        self.feed(_hand("00000"), 10)
        acts = self.feed(_hand("01000", thumb_to="index"), 3)
        self.assertNotIn("down", self.kinds(acts))

    def test_a_single_frame_pinch_is_ignored(self):
        self.feed(_hand("01000"), 10)
        acts = self.feed(_hand("01000", thumb_to="index"), 1) + self.feed(_hand("01000"), 3)
        self.assertNotIn("down", self.kinds(acts))

    def test_no_click_right_after_the_hand_appears(self):
        acts = self.feed(_hand("01000", thumb_to="index"), 4)   # ~0.13s < settle time
        self.assertNotIn("down", self.kinds(acts))

    def test_pointer_speed_setting_scales_the_mapping(self):
        from core.gestures import HandInterpreter
        slow, fast = HandInterpreter((1000, 800), 0.7), HandInterpreter((1000, 800), 1.5)
        self.assertLess(slow.gain, fast.gain)
        box_slow, box_fast = HandInterpreter((1000, 800), 0.7, "absolute"), HandInterpreter((1000, 800), 1.5, "absolute")
        self.assertGreater(box_slow.BOX[2] - box_slow.BOX[0], box_fast.BOX[2] - box_fast.BOX[0])

    def test_absolute_mode_still_maps_the_box(self):
        from core.gestures import HandInterpreter
        h, t = HandInterpreter((1000, 800), 1.0, "absolute"), 0.0
        for _ in range(12):
            t += 1 / 30
            h.update(_hand("01000", dx=0.2), t)
        left = h.cursor[0]
        for _ in range(30):
            t += 1 / 30
            h.update(_hand("01000", dx=-0.2), t)
        self.assertLess(h.cursor[0], left)

    def test_a_fist_never_clicks(self):
        acts = self.feed(_hand("00000", thumb_to="index"), 30)
        self.assertNotIn("down", self.kinds(acts))

    def test_losing_the_hand_releases_a_held_button(self):
        self.feed(_hand("01000"), 10)
        self.feed(_hand("01000", thumb_to="index"), 3)
        self.assertIn(("up", "left"), self.feed(None))

    def test_two_fingers_scroll_like_a_joystick(self):
        self.feed(_hand("01100"), 10)                         # anchor here - no scroll yet
        self.assertNotIn("scroll", self.kinds(self.feed(_hand("01100"), 5)))
        up = [a for a in self.feed(_hand("01100", dy=-0.1), 5) if a[0] == "scroll"]
        self.assertTrue(up and all(a[2] for a in up))          # hand up -> scroll up
        down = [a for a in self.feed(_hand("01100", dy=0.12), 5) if a[0] == "scroll"]
        self.assertTrue(down and not any(a[2] for a in down))

    def test_one_shots_need_a_hold_and_fire_once(self):
        acts = self.feed(_hand("01111"), 10)                   # 0.33s: not yet
        self.assertNotIn("screenshot", self.kinds(acts))
        acts = self.feed(_hand("01111"), 60)                   # 2s more: exactly once (cooldown)
        self.assertEqual(self.kinds(acts).count("screenshot"), 1)
        self.assertIn("stop", self.kinds(self.feed(_hand("11111"), 40)))


class OneEuroTests(unittest.TestCase):
    def test_smooths_jitter_but_follows_real_moves(self):
        from core.gestures import OneEuro
        f, t, out = OneEuro(), 0.0, []
        for i in range(60):                                   # still hand with +/-0.01 noise
            t += 1 / 30
            out.append(f(0.5 + (0.01 if i % 2 else -0.01), t))
        self.assertLess(max(out[20:]) - min(out[20:]), 0.01)  # jitter shrinks
        for _ in range(15):                                   # a real, fast move
            t += 1 / 30
            y = f(0.8, t)
        self.assertGreater(y, 0.75)


if __name__ == "__main__":
    unittest.main()
