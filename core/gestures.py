"""Control your Mac/PC with your hand through the webcam.

With MediaPipe hand landmarks (21 points per hand - `pip install mediapipe` + the hand_landmarker.task
model), the hand works like a mouse:

  point (index finger up)        move the pointer (smoothed; hand anywhere in view)
  pinch thumb + index            click  (pinch twice quickly = double-click; hold the pinch and move = drag)
  pinch thumb + middle finger    right-click
  two fingers (index + middle)   scroll like a joystick: move the hand up/down from where you started -
                                 further = faster
  three fingers (hold)           identify what you're holding (vision)
  four fingers (hold)            screenshot
  open palm (hold)               stop hand control          fist = rest (nothing happens)

Without MediaPipe it falls back to skin-colour finger counting inside a box: 1 finger = scroll down,
2 = scroll up (hold), 3 = identify, 4 = screenshot, open palm = stop.

Privacy: only runs from an explicit command you approve (broker gates it on the SENSITIVE 'camera'
capability), never a background task. The preview window (or an open palm) stops it.

On macOS the camera loop runs in its own process (`python -m core.gestures`): OpenCV's preview window
must own the main thread there, which inside JARVIS belongs to the Tk panel. Events come back to the
panel as JSON lines on stdout. Windows keeps the in-process thread. Each run writes a short diagnostics
log (fps, detection rate, poses, actions) to <data dir>/hand_control.log for tuning.
"""
from __future__ import annotations

import collections
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

_WINDOW = "JARVIS hand control"
_ACTIVE = None
_LOCK = threading.Lock()


# ---- pointer smoothing --------------------------------------------------------------------------

class OneEuro:
    """One-Euro filter (Casiez et al.): heavy smoothing when the hand is still (kills jitter), light
    smoothing when it moves fast (no lag). Values are normalised 0..1 camera coordinates."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 6.0, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x = self._dx = self._t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self._x = self._dx = self._t = None

    def __call__(self, x: float, t: float) -> float:
        if self._t is None:
            self._x, self._dx, self._t = x, 0.0, t
            return x
        dt = max(1e-3, t - self._t)
        dx = (x - self._x) / dt
        self._dx += self._alpha(self.d_cutoff, dt) * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        self._x += self._alpha(cutoff, dt) * (x - self._x)
        self._t = t
        return self._x


# ---- landmarks -> actions (pure logic, no camera/OS, unit-tested) --------------------------------

class HandInterpreter:
    """Turns a stream of 21-point hand landmarks (normalised, mirrored like a mirror) into mouse actions:
      ("move", x, y) ("down", button, clicks) ("up", button) ("drag", x, y) ("scroll", px, up)
      ("identify",) ("screenshot",) ("stop",)
    Screen coordinates come from `screen` (w, h)."""

    # The part of the camera view mapped onto the whole screen at speed 1.0. A bigger box = a slower,
    # more precise pointer (your hand travels further per screen). Tuned from a real session where a
    # 0.6-wide box felt "too responsive". `speed` (config gestures.pointer_speed) scales it.
    BOX_W, BOX_H, BOX_CX, BOX_CY = 0.76, 0.62, 0.5, 0.45
    PINCH_ON, PINCH_OFF = 0.26, 0.40       # thumb-tip distance / palm size, with hysteresis
    PINCH_FRAMES = 2                       # a pinch must hold this many frames (passing shapes don't click)
    SETTLE = 0.25                          # s after the hand appears before any click can happen
    CLICK_FREEZE = 0.22                    # s the pointer holds still after a pinch (clean clicks)
    DOUBLE_CLICK = 0.45                    # s between pinches that counts as a double-click
    DEADZONE = 3.0                         # px: smaller pointer changes are ignored (no micro-jitter)
    HOLD = {"point": 0.1, "two": 0.15, "three": 1.0, "four": 0.6, "palm": 0.8}
    COOLDOWN = 3.0

    def __init__(self, screen=(1440, 900), speed: float = 1.0):
        self.sw, self.sh = screen
        speed = min(max(float(speed or 1.0), 0.3), 3.0)
        w, h = min(0.98, self.BOX_W / speed), min(0.98, self.BOX_H / speed)
        cx = min(max(self.BOX_CX, w / 2), 1 - w / 2)
        cy = min(max(self.BOX_CY, h / 2), 1 - h / 2)
        self.BOX = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        # calmer than the defaults: more smoothing while nearly still, still quick on big moves
        self.fx, self.fy = OneEuro(0.7, 2.5), OneEuro(0.7, 2.5)
        self._cand, self._cand_frames = None, 0
        self.hand_since = None
        self.votes = collections.deque(maxlen=5)
        self.pose, self.pose_since = "none", 0.0
        self.pinch = None                  # None | "left" | "right"
        self.pinch_since = 0.0
        self.cursor = None                 # last emitted screen position
        self.last_click = (0.0, None)
        self.scroll_anchor = None
        self.next_fire = 0.0
        self.label = ""

    # -- geometry --
    @staticmethod
    def _d(p, a, b):
        return math.dist(p[a], p[b])

    def fingers(self, p):
        """(thumb, index, middle, ring, pinky) extended?"""
        d = lambda a, b: self._d(p, a, b)  # noqa: E731
        ext = [d(tip, 0) > d(pip, 0) * 1.1 for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18))]
        palm = d(0, 9) or 1e-6
        thumb = d(4, 5) / palm > 0.55 and d(4, 0) > d(3, 0)
        return (thumb, *ext)

    def _pinch_state(self, p):
        d = lambda a, b: self._d(p, a, b)  # noqa: E731
        palm = d(0, 9) or 1e-6
        # a finger curled into a fist also ends near the thumb - only count it if it isn't curled
        left_ok = d(8, 0) > d(6, 0) * 0.9
        # right-click = fold the index and touch thumb to middle; with the index still up (two-finger
        # scroll) a thumb drifting near the middle finger must NOT right-click
        right_ok = d(12, 0) > d(10, 0) * 0.9 and d(8, 0) <= d(6, 0) * 1.1
        rl, rr = d(4, 8) / palm, d(4, 12) / palm
        if self.pinch == "left":
            return "left" if rl < self.PINCH_OFF else None
        if self.pinch == "right":
            return "right" if rr < self.PINCH_OFF else None
        # index and middle tips sit close together, so pick the fingertip the thumb is clearly nearest
        if right_ok and rr < self.PINCH_ON and rr < rl * 0.8:
            return "right"
        if left_ok and rl < self.PINCH_ON and rl <= rr:
            return "left"
        return None

    def _debounced_pinch(self, raw, t):
        """An engaged pinch stays until released. A NEW one must hold PINCH_FRAMES frames, the hand must
        have settled, and a left click must come from pointing - so opening a fist, or a thumb drifting
        while you scroll, can't click."""
        if self.pinch:
            return raw if raw == self.pinch else None
        if raw != self._cand:
            self._cand, self._cand_frames = raw, 0
        self._cand_frames += bool(raw)
        if not raw or self._cand_frames < self.PINCH_FRAMES or t - self.hand_since < self.SETTLE:
            return None
        if raw == "left" and self.pose not in ("point", "pinch"):
            return None
        return raw

    def classify(self, p) -> str:
        thumb, i, m, r, k = self.fingers(p)
        if i and not (m or r or k):
            return "point"
        if i and m and not (r or k):
            return "two"
        if i and m and r and not k:
            return "three"
        if i and m and r and k:
            return "palm" if thumb else "four"
        if not (i or m or r or k):
            return "fist"
        return "other"

    def _to_screen(self, p, t):
        x0, y0, x1, y1 = self.BOX
        # the index knuckle: steady while pinching (the fingertip itself moves when you pinch)
        nx = self.fx(p[5][0], t)
        ny = self.fy(p[5][1], t)
        sx = min(max((nx - x0) / (x1 - x0), 0.0), 1.0) * (self.sw - 1)
        sy = min(max((ny - y0) / (y1 - y0), 0.0), 1.0) * (self.sh - 1)
        return sx, sy

    # -- main step --
    def update(self, p, t: float) -> list:
        acts = []
        if p is None:                      # hand lost: never leave a button stuck down
            if self.pinch:
                acts.append(("up", self.pinch))
            self.pinch, self.scroll_anchor, self.label = None, None, ""
            self._cand, self._cand_frames, self.hand_since = None, 0, None
            self.votes.clear()
            self.pose = "none"
            self.fx.reset()
            self.fy.reset()
            return acts

        if self.hand_since is None:
            self.hand_since = t
        pinch = self._debounced_pinch(self._pinch_state(p), t)
        self.votes.append(self.classify(p))
        top, n = collections.Counter(self.votes).most_common(1)[0]
        pose = "pinch" if pinch else (top if n * 2 > len(self.votes) else self.pose)
        if pose != self.pose:
            self.pose, self.pose_since = pose, t
            if pose != "two":
                self.scroll_anchor = None
        held = t - self.pose_since
        pointer = pose in ("point", "pinch") and (pose == "pinch" or held >= self.HOLD["point"])
        sx, sy = self._to_screen(p, t)

        # pinch edges -> button down/up
        if pinch != self.pinch:
            if self.pinch:
                acts.append(("up", self.pinch))
            if pinch:
                clicks = 1
                last_t, last_pos = self.last_click
                if pinch == "left" and t - last_t < self.DOUBLE_CLICK and last_pos and \
                        math.dist(last_pos, (sx, sy)) < 40:
                    clicks = 2
                pos = self.cursor or (sx, sy)
                acts.append(("down", pinch, clicks))
                self.last_click = (t, pos) if pinch == "left" else self.last_click
                self.pinch_since = t
            self.pinch = pinch

        if pointer:
            frozen = self.pinch and t - self.pinch_since < self.CLICK_FREEZE
            if not frozen and (self.cursor is None or math.dist(self.cursor, (sx, sy)) >= self.DEADZONE):
                acts.append(("drag" if self.pinch == "left" else "move", sx, sy))
                self.cursor = (sx, sy)
            self.label = {"left": "click / drag", "right": "right-click"}.get(self.pinch, "pointer")
        elif pose == "two" and held >= self.HOLD["two"]:
            y = p[5][1]
            if self.scroll_anchor is None:
                self.scroll_anchor = y
            off = y - self.scroll_anchor
            if abs(off) > 0.03:
                speed = min(90.0, (abs(off) - 0.03) * 900)
                acts.append(("scroll", int(max(4, speed)), off < 0))   # hand up = scroll up
                self.label = "scroll up" if off < 0 else "scroll down"
            else:
                self.label = "scroll (move hand up/down)"
        elif pose in ("three", "four", "palm") and held >= self.HOLD[pose] and t >= self.next_fire:
            acts.append({"three": ("identify",), "four": ("screenshot",), "palm": ("stop",)}[pose])
            self.label = {"three": "identify", "four": "screenshot", "palm": "stop"}[pose]
            self.next_fire = t + self.COOLDOWN
        elif pose in ("fist", "other"):
            self.label = "rest"
        return acts


class LandmarkTracker:
    """MediaPipe's hand-landmark model. `create()` returns None when mediapipe or the model file is
    missing (the caller then falls back to skin-colour counting)."""

    def __init__(self, landmarker):
        self._lm = landmarker
        self._t0 = time.monotonic()

    @classmethod
    def create(cls):
        try:
            from mediapipe.tasks.python import BaseOptions, vision
            from core.oslayer import user_data_dir
        except Exception:
            return None
        model = user_data_dir() / "models" / "hand_landmarker.task"
        if not model.is_file():
            return None
        try:
            opts = vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model), delegate=BaseOptions.Delegate.CPU),
                running_mode=vision.RunningMode.VIDEO, num_hands=1,
                min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5)
            return cls(vision.HandLandmarker.create_from_options(opts))
        except Exception:
            return None

    def detect(self, frame_bgr, cv2):
        """21 normalised (x, y) points for the hand in view, or None."""
        import mediapipe as mp
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._lm.detect_for_video(image, int((time.monotonic() - self._t0) * 1000))
        if not result.hand_landmarks:
            return None
        return [(pt.x, pt.y) for pt in result.hand_landmarks[0]]


def fingers_up(pts) -> int:
    """Extended-finger count (0-5) from 21 landmarks."""
    return sum(HandInterpreter().fingers(pts))


class _Diag:
    """Once-a-second summary lines in <data dir>/hand_control.log, so a run can be tuned afterwards."""

    def __init__(self):
        self.f = None
        try:
            from core.oslayer import user_data_dir
            self.f = open(user_data_dir() / "hand_control.log", "w", encoding="utf-8")
        except OSError:
            pass
        self._reset(time.time())

    def _reset(self, t):
        self.t0, self.frames, self.hands = t, 0, 0
        self.poses = collections.Counter()
        self.acts = collections.Counter()

    def line(self, text):
        if self.f:
            self.f.write(time.strftime("%H:%M:%S ") + text + "\n")
            self.f.flush()

    def frame(self, t, hand, pose, acts):
        self.frames += 1
        self.hands += bool(hand)
        self.poses[pose] += 1
        for a in acts:
            self.acts[a[0] if a[0] not in ("down", "up") else f"{a[0]}-{a[1]}"] += 1
        if t - self.t0 >= 1.0:
            dt = t - self.t0
            self.line(f"fps={self.frames / dt:.1f} hand={100 * self.hands // max(1, self.frames)}% "
                      f"poses={dict(self.poses)} actions={dict(self.acts)}")
            self._reset(t)


# ---- the camera loop --------------------------------------------------------------------------

class GestureController:
    def __init__(self, runner, emit=None, camera_index=0, hold_scroll=0.2, hold_action=0.6, cooldown=3.0,
                 scroll_px=(18, 70), ramp_secs=1.5, vote_frames=7):
        self.runner = runner or (lambda cmd: None)
        self.emit = emit or (lambda *a: None)
        self.camera_index = camera_index
        # skin-colour fallback tuning
        self.hold_scroll = hold_scroll
        self.hold_action = hold_action
        self.cooldown = cooldown
        self.scroll_px = scroll_px
        self.ramp_secs = ramp_secs
        self.vote_frames = vote_frames
        self.error = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # ---- actions ------------------------------------------------------------------------------

    def _scroll(self, up: bool, held: float):
        from core.oslayer import scroll_pixels
        lo, hi = self.scroll_px
        speed = lo + (hi - lo) * min(1.0, max(0.0, held) / self.ramp_secs)
        scroll_pixels(int(speed), up)

    def _fire_async(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _identify(self, frame_copy):
        """Name what you're holding up. PRIVACY: the photo never goes to your Pictures or anywhere
        permanent - it's written to a private (owner-only) temp folder, sent once to the vision model
        (Google Gemini) and deleted as soon as the answer comes back; the whole folder is wiped when
        hand control stops. Camera frames otherwise never leave memory, and the hand tracking itself
        runs on this computer."""
        path = None
        try:
            import cv2
            from core.vision import available, describe_image
            if not available():
                self.emit("gesture", "I need a vision key to name things (jarvis setkey gemini).")
                return
            fd, path = tempfile.mkstemp(suffix=".jpg", dir=self._private_dir())
            os.close(fd)
            cv2.imwrite(path, frame_copy, [cv2.IMWRITE_JPEG_QUALITY, 80])
            desc = describe_image(path, "What object is the person holding up to the camera? "
                                        "Answer in a short phrase.")
            if desc and not desc.startswith("("):
                self.emit("gesture", f"I see: {desc}  (photo sent to Gemini once, not saved)")
            else:
                self.emit("gesture", "I couldn't identify it (try again / better light). Photo not saved.")
        except Exception as exc:
            self.emit("gesture", f"Identify failed: {exc}")
        finally:
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _private_dir(self):
        if getattr(self, "_tmpdir", None) is None:
            self._tmpdir = tempfile.mkdtemp(prefix="jarvis-hand-")   # mode 0700: only you can read it
        return self._tmpdir

    def _wipe_private(self):
        d = getattr(self, "_tmpdir", None)
        if d:
            shutil.rmtree(d, ignore_errors=True)
            self._tmpdir = None

    def _perform(self, act, frame):
        from core import oslayer
        kind = act[0]
        if kind in ("move", "drag"):
            oslayer.mouse_event(kind, act[1], act[2])
        elif kind in ("down", "up"):
            pos = oslayer.mouse_position() or (0, 0)
            oslayer.mouse_event(kind, pos[0], pos[1], button=act[1], clicks=act[2] if kind == "down" else 1)
        elif kind == "scroll":
            oslayer.scroll_pixels(act[1], act[2])
        elif kind == "identify":
            self._fire_async(self._identify, frame.copy())
        elif kind == "screenshot":
            self._fire_async(self.runner, "take a screenshot")
        elif kind == "stop":
            self._stop.set()

    # ---- loop ---------------------------------------------------------------------------------

    def _open_camera(self):
        try:
            import cv2
        except ImportError:
            self.error = "the camera library (opencv) isn't available"
            return None, None
        from core import oslayer
        if oslayer.request_camera_access() is False:
            self.error = "camera access is blocked (enable JARVIS/Python in System Settings > Privacy > Camera)"
            return cv2, None
        cap = cv2.VideoCapture(self.camera_index, oslayer.camera_backend(cv2))
        if not cap or not cap.isOpened():
            self.error = "couldn't open the webcam (is it in use by another app?)"
            if cap:
                cap.release()
            return cv2, None
        # 640x480 is plenty for hand tracking and far cheaper than the webcam's native 1080p.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cv2, cap

    def _read(self, cap, cv2):
        ok, frame = cap.read()
        if not ok:
            return None
        if frame.shape[1] > 720:   # the camera ignored the size request - shrink it ourselves
            frame = cv2.resize(frame, (640, int(frame.shape[0] * 640 / frame.shape[1])))
        return cv2.flip(frame, 1)  # mirror, so moving your hand right moves things right

    def _show(self, cv2, frame, gui):
        if not gui:
            time.sleep(0.005)
            return True
        try:
            cv2.imshow(_WINDOW, frame)
            if (cv2.waitKey(1) & 0xFF) == ord("q") or cv2.getWindowProperty(_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                return None      # closed -> stop
        except cv2.error:
            return False         # window died - keep going headless
        return True

    @staticmethod
    def _sweep_stale():
        """Remove private folders left by a crashed earlier session."""
        import glob
        for d in glob.glob(os.path.join(tempfile.gettempdir(), "jarvis-hand-*")):
            shutil.rmtree(d, ignore_errors=True)

    def _run(self):
        self._sweep_stale()
        cv2, cap = self._open_camera()
        if cap is None:
            return
        gui = True
        try:
            cv2.namedWindow(_WINDOW, cv2.WINDOW_AUTOSIZE)
        except cv2.error:
            gui = False
            self.emit("gesture", "No preview window (headless OpenCV) - gestures still work; open palm to stop.")
        tracker = LandmarkTracker.create()
        diag = _Diag()
        try:
            if tracker:
                self._run_landmarks(cv2, cap, gui, tracker, diag)
            else:
                self._run_counts(cv2, cap, gui, diag)
        except Exception as exc:
            self.error = str(exc)
            diag.line(f"error: {exc}")
        finally:
            cap.release()
            try:
                cv2.destroyWindow(_WINDOW)
                cv2.waitKey(1)
            except Exception:
                pass
            self._wipe_private()   # nothing from the camera outlives the session
            diag.line("stopped")
            self.emit("gesture", "Hand control stopped - nothing from the camera was kept.")

    def _run_landmarks(self, cv2, cap, gui, tracker, diag):
        from core import oslayer
        allowed = oslayer.can_control_mouse(request=True)
        diag.line(f"mode=landmarks screen={oslayer.screen_size()} mouse_allowed={allowed}")
        if allowed is False:
            self.emit("gesture", "I can see your hand, but macOS isn't letting me move the pointer yet: allow "
                                 "JARVIS in System Settings > Privacy & Security > Accessibility, then restart "
                                 "hand control.")
        self.emit("gesture", "Hand control on - point to move the pointer, pinch thumb+index to click (hold to "
                             "drag), thumb+middle = right-click, two fingers = scroll, 3 = identify, "
                             "4 = screenshot, open palm = stop.")
        try:
            from core.config import load_settings
            speed = float(load_settings().get("gestures.pointer_speed", 1.0) or 1.0)
        except Exception:
            speed = 1.0
        hand = HandInterpreter(oslayer.screen_size(), speed)
        diag.line(f"pointer_speed={speed} box={tuple(round(v, 2) for v in hand.BOX)}")
        while not self._stop.is_set():
            frame = self._read(cap, cv2)
            if frame is None:
                time.sleep(0.01)
                continue
            now = time.time()
            pts = tracker.detect(frame, cv2)
            acts = hand.update(pts, now)
            for act in acts:
                self._perform(act, frame)
            diag.frame(now, pts is not None, hand.pose, acts)
            if gui:
                self._draw_landmarks(frame, cv2, hand, pts)
            shown = self._show(cv2, frame, gui)
            if shown is None:
                break
            gui = gui and shown
        for act in hand.update(None, time.time()):   # release anything still held
            self._perform(act, None)

    def _draw_landmarks(self, frame, cv2, hand, pts):
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = hand.BOX
        cv2.rectangle(frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (90, 90, 90), 1)
        colour = {"pointer": (0, 255, 0), "click / drag": (0, 140, 255), "right-click": (255, 0, 200)}.get(
            hand.label, (255, 200, 0) if hand.label.startswith("scroll") else (0, 200, 200))
        for x, y in pts or ():
            cv2.circle(frame, (int(x * w), int(y * h)), 3, colour, -1)
        if pts:
            cv2.circle(frame, (int(pts[5][0] * w), int(pts[5][1] * h)), 8, colour, 2)   # the pointer point
        cv2.putText(frame, hand.label or ("no hand" if not pts else hand.pose), (12, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, colour, 2)
        cv2.putText(frame, "point=move  pinch=click/drag  thumb+middle=right  2=scroll  3=identify  "
                           "4=shot  palm=stop", (8, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 220, 220), 1)

    def _run_counts(self, cv2, cap, gui, diag):
        """Skin-colour fallback: count fingers in a box; 1/2 = scroll down/up, 3 identify, 4 screenshot."""
        import numpy as np
        diag.line("mode=skin-colour")
        self.emit("gesture", "Hand control on (basic mode - install mediapipe for pointer control): keep your "
                             "hand in the box; 1 finger = scroll down, 2 = up, 3 identify, 4 screenshot, "
                             "open palm = stop.")
        recent = collections.deque(maxlen=self.vote_frames)
        current, since, next_fire = 0, time.time(), 0.0
        while not self._stop.is_set():
            frame = self._read(cap, cv2)
            if frame is None:
                time.sleep(0.01)
                continue
            h, w = frame.shape[:2]
            x0, y0, x1, y1 = int(w * 0.55), int(h * 0.10), w - 10, int(h * 0.65)
            recent.append(count_fingers(frame[y0:y1, x0:x1], cv2, np))
            top, votes = collections.Counter(recent).most_common(1)[0]
            count = top if votes * 2 > len(recent) else current
            now = time.time()
            if count != current:
                current, since = count, now
            held = now - since
            label, acts = None, []
            if count >= 5 and held >= self.hold_action:
                self._stop.set()
            elif count in (1, 2) and held >= self.hold_scroll:
                label = "scroll down" if count == 1 else "scroll up"
                self._scroll(up=(count == 2), held=held - self.hold_scroll)
                acts.append(("scroll",))
            elif count == 3 and held >= self.hold_action and now >= next_fire:
                label = "identify"
                self._fire_async(self._identify, frame.copy())
                next_fire = now + self.cooldown
            elif count == 4 and held >= self.hold_action and now >= next_fire:
                label = "screenshot"
                self._fire_async(self.runner, "take a screenshot")
                next_fire = now + self.cooldown
            diag.frame(now, count > 0, str(count), acts)
            if gui:
                ready = count and held >= (self.hold_scroll if count in (1, 2) else self.hold_action)
                colour = (0, 255, 0) if ready else (0, 180, 180)
                cv2.rectangle(frame, (x0, y0), (x1, y1), colour, 2)
                cv2.putText(frame, f"Fingers: {count}", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                if label:
                    cv2.putText(frame, label, (12, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
            shown = self._show(cv2, frame, gui)
            if shown is None:
                break
            gui = gui and shown


def count_fingers(roi, cv2, np) -> int:
    """Extended-finger count (0-5) for a hand in the ROI, via skin mask + convex-hull defects."""
    if roi is None or getattr(roi, "size", 0) == 0:
        return 0
    ycrcb = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127))
    mask = cv2.GaussianBlur(mask, (5, 5), 0)
    _, mask = cv2.threshold(mask, 50, 255, cv2.THRESH_BINARY)
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    if not contours:
        return 0
    hand = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(hand)
    if area < 3000:
        return 0
    hull = cv2.convexHull(hand, returnPoints=False)
    if hull is None or len(hull) < 4:
        return 0
    try:
        defects = cv2.convexityDefects(hand, hull)
    except cv2.error:
        return 0
    if defects is None:
        return 0
    gaps = 0
    for i in range(defects.shape[0]):
        s, e, f, depth = defects[i, 0]
        start, end, far = hand[s][0], hand[e][0], hand[f][0]
        a = math.dist(start, end)
        b = math.dist(start, far)
        c = math.dist(end, far)
        if b * c == 0:
            continue
        angle = math.acos(max(-1.0, min(1.0, (b * b + c * c - a * a) / (2 * b * c))))
        if angle <= math.pi / 2 and depth > 10000:
            gaps += 1
    if gaps > 0:
        return min(gaps + 1, 5)
    return 1 if area > 6000 else 0


# ---- module-level singleton -------------------------------------------------------------------

_ON_MSG = ("Hand control is ON - watch the webcam window. Point with your index finger to move the pointer, "
           "pinch thumb+index to click (twice = double-click, hold and move = drag), thumb+middle = "
           "right-click, two fingers = scroll (move your hand up/down), 3 fingers = identify, 4 = "
           "screenshot; open palm = stop (or say 'stop watching my hands').")


class _ProcessController:
    """Runs the camera loop in a child process (macOS: the preview window needs a main thread) and
    relays its JSON-line events: {"emit": text} -> emit, {"run": command} -> runner."""

    def __init__(self, runner, emit, camera_index):
        self.runner = runner or (lambda cmd: None)
        self.emit = emit or (lambda *a: None)
        self.error = None
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.proc = subprocess.Popen([sys.executable, "-m", "core.gestures", str(camera_index)], cwd=root,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "error" in msg:
                self.error = msg["error"]
            elif "run" in msg:
                threading.Thread(target=self.runner, args=(msg["run"],), daemon=True).start()
            elif "emit" in msg:
                self.emit("gesture", msg["emit"])

    def running(self):
        return self.proc.poll() is None

    def stop(self):
        if self.running():
            self.proc.terminate()


def start(runner, emit=None, camera_index=0) -> str:
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            return "Hand control is already on. Show an open palm (or say 'stop watching my hands')."
        if sys.platform == "darwin":
            _ACTIVE = _ProcessController(runner, emit, camera_index)
        else:
            _ACTIVE = GestureController(runner, emit, camera_index)
            _ACTIVE.start()
    deadline = time.time() + 2.5   # the camera takes a moment to open; catch an early failure
    while time.time() < deadline and _ACTIVE.running() and not _ACTIVE.error:
        time.sleep(0.1)
    if _ACTIVE.error:
        return f"Couldn't start hand control: {_ACTIVE.error}"
    if not _ACTIVE.running():
        return "Couldn't start hand control: the camera loop exited straight away."
    return _ON_MSG


def stop() -> str:
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            _ACTIVE.stop()
            return "Stopping hand control."
    return "Hand control isn't running."


def running() -> bool:
    with _LOCK:
        return _ACTIVE is not None and _ACTIVE.running()


def _child_main(camera_index: int = 0) -> None:
    """`python -m core.gestures`: the camera loop on this process's main thread, events as JSON lines."""
    def out(**msg):
        try:
            print(json.dumps(msg), flush=True)
        except (BrokenPipeError, ValueError):
            os._exit(0)   # the panel went away - stop

    ctl = GestureController(lambda cmd: out(run=cmd), lambda _kind, text: out(emit=text), camera_index)
    import signal
    # JARVIS stops us with SIGTERM: stop the loop cleanly so the camera is released and the private
    # temp folder is wiped (the default SIGTERM would kill us before any cleanup ran).
    signal.signal(signal.SIGTERM, lambda *_: ctl.stop())
    ctl._run()
    if ctl.error:
        out(error=ctl.error)


if __name__ == "__main__":
    _child_main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
