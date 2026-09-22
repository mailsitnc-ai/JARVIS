"""Control JARVIS - and your browser - with hand gestures through the webcam. OpenCV only (no MediaPipe,
which won't run on this CPU).

It watches a webcam region, segments the hand by skin colour, and counts extended fingers (0-5) via
convex-hull defects. Gestures split into two kinds:

  * HOLD gestures act continuously the whole time you hold them - scrolling the window you're looking at
    (real OS mouse-wheel events, so it drives your actual browser, not just JARVIS's Chrome).
  * ONE-SHOT gestures fire once, then wait for a cooldown - identify what you're holding (vision), or a
    screenshot.

  1 finger  = scroll down (hold)      3 fingers = identify what I'm holding (vision)
  2 fingers = scroll up   (hold)      4 fingers = take a screenshot
  open palm (5) = stop                fist (0)  = rest

Privacy: only runs from an explicit command you approve (broker gates it on the SENSITIVE 'camera'
capability), never a background task. The preview window (or an open palm) stops it.

On macOS the camera loop runs in its own process (`python -m core.gestures`): OpenCV's preview window
must own the main thread there, which inside JARVIS belongs to the Tk panel. Events come back to the
panel as JSON lines on stdout. Windows keeps the in-process thread.
"""
from __future__ import annotations

import collections
import json
import math
import os
import subprocess
import sys
import threading
import time

_WINDOW = "JARVIS hand control"
_ACTIVE = None
_LOCK = threading.Lock()


class GestureController:
    def __init__(self, runner, emit=None, camera_index=0, hold_scroll=0.2, hold_action=0.6, cooldown=3.0,
                 scroll_px=(18, 70), ramp_secs=1.5, vote_frames=7):
        self.runner = runner or (lambda cmd: None)
        self.emit = emit or (lambda *a: None)
        self.camera_index = camera_index
        self.hold_scroll = hold_scroll            # seconds a scroll gesture must be steady before it starts
        self.hold_action = hold_action            # ... and a one-shot (identify/screenshot/stop) before it fires
        self.cooldown = cooldown                  # seconds between one-shot fires
        self.scroll_px = scroll_px                # (start, max) pixels per frame while scrolling
        self.ramp_secs = ramp_secs                # holding longer speeds the scroll up to max over this time
        self.vote_frames = vote_frames            # majority vote over this many frames (smooths flicker)
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
        scroll_pixels(int(speed), up)  # OS-level smooth wheel event: Windows mouse_event / macOS Quartz

    def _fire_async(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _identify(self, frame_copy):
        try:
            import time as _t
            from pathlib import Path

            import cv2
            from core.vision import available, describe_image
            target = Path.home() / "Pictures" / f"JARVIS-cam-{_t.strftime('%Y%m%d-%H%M%S')}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(target), frame_copy)
            if not available():
                self.emit("gesture", "Captured it, but I need a vision key to name it (jarvis setkey gemini).")
                return
            desc = describe_image(str(target), "What object is the person holding up to the camera? "
                                               "Answer in a short phrase.")
            if desc and not desc.startswith("("):
                self.emit("gesture", f"I see: {desc}")
            else:
                self.emit("gesture", "I captured it but couldn't identify it (try again / better light).")
        except Exception as exc:
            self.emit("gesture", f"Identify failed: {exc}")

    # ---- loop ---------------------------------------------------------------------------------

    def _run(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.error = "the camera library (opencv) isn't available"
            return
        from core import oslayer
        if oslayer.request_camera_access() is False:
            self.error = "camera access is blocked (enable JARVIS/Python in System Settings > Privacy > Camera)"
            return
        cap = cv2.VideoCapture(self.camera_index, oslayer.camera_backend(cv2))
        if not cap or not cap.isOpened():
            self.error = "couldn't open the webcam (is it in use by another app?)"
            if cap:
                cap.release()
            return
        # 640x480 is plenty for counting fingers and far cheaper than the webcam's native 1080p.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        gui = True                     # opencv-python-headless has no HighGUI - detect and run without a window
        try:
            cv2.namedWindow(_WINDOW, cv2.WINDOW_AUTOSIZE)
        except cv2.error:
            gui = False
            self.emit("gesture", "No preview window (headless OpenCV) - gestures still work; open palm to stop.")
        self.emit("gesture", "Hand control on - 1 down, 2 up (hold to keep scrolling), 3 identify, "
                             "4 screenshot; open palm = stop.")
        counter = LandmarkCounter.create()   # None -> skin-colour fallback inside the green box
        self.emit("gesture", "Tracking with MediaPipe hand landmarks (hand anywhere in view)." if counter
                  else "Tracking by skin colour - keep your hand inside the box.")
        recent = collections.deque(maxlen=self.vote_frames)
        current, since, next_fire = 0, time.time(), 0.0
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.01)
                    continue
                if frame.shape[1] > 720:   # the camera ignored the size request - shrink it ourselves
                    frame = cv2.resize(frame, (640, int(frame.shape[0] * 640 / frame.shape[1])))
                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                x0, y0, x1, y1 = int(w * 0.55), int(h * 0.10), w - 10, int(h * 0.65)
                raw = counter.count(frame, cv2) if counter else count_fingers(frame[y0:y1, x0:x1], cv2, np)
                recent.append(raw)
                # Majority vote: one misread frame no longer resets the gesture (that made scrolling
                # stutter and stop). The count must hold a majority of the recent frames.
                top, votes = collections.Counter(recent).most_common(1)[0]
                count = top if votes * 2 > len(recent) else current
                now = time.time()
                if count != current:
                    current, since = count, now
                held = now - since
                label = None
                if count >= 5 and held >= self.hold_action:
                    self._stop.set()
                elif count in (1, 2) and held >= self.hold_scroll:       # HOLD: scroll while shown
                    label = "scroll down" if count == 1 else "scroll up"
                    self._scroll(up=(count == 2), held=held - self.hold_scroll)
                elif count == 3 and held >= self.hold_action and now >= next_fire:   # ONE-SHOT: identify
                    label = "identify"
                    self._fire_async(self._identify, frame.copy())
                    next_fire = now + self.cooldown
                elif count == 4 and held >= self.hold_action and now >= next_fire:   # ONE-SHOT: screenshot
                    label = "screenshot"
                    self._fire_async(self.runner, "take a screenshot")
                    next_fire = now + self.cooldown
                if gui:
                    ready = count and held >= (self.hold_scroll if count in (1, 2) else self.hold_action)
                    self._draw(frame, cv2, None if counter else (x0, y0, x1, y1), count, label, ready,
                               counter.points if counter else None)
                    try:
                        cv2.imshow(_WINDOW, frame)
                        if (cv2.waitKey(1) & 0xFF) == ord("q") or \
                                cv2.getWindowProperty(_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                            break
                    except cv2.error:
                        gui = False          # window died mid-run - keep going headless
                else:
                    time.sleep(0.01)
        except Exception as exc:
            self.error = str(exc)
        finally:
            cap.release()
            try:
                cv2.destroyWindow(_WINDOW)
                cv2.waitKey(1)
            except Exception:
                pass
            self.emit("gesture", "Hand control stopped.")

    def _draw(self, frame, cv2, box, count, label, ready, points=None):
        colour = (0, 255, 0) if ready else (0, 180, 180)
        if box:
            x0, y0, x1, y1 = box
            cv2.rectangle(frame, (x0, y0), (x1, y1), colour, 2)
        for x, y in points or ():
            cv2.circle(frame, (x, y), 4, colour, -1)
        cv2.putText(frame, f"Fingers: {count}", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        cv2.putText(frame, "1 down  2 up (hold)  3 identify  4 shot  |  open palm = stop",
                    (12, frame.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
        if label:
            cv2.putText(frame, label, (12, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)


class LandmarkCounter:
    """Finger counting with MediaPipe's hand-landmark model (21 points per hand) - far steadier than skin
    colour, works anywhere in the frame and in any lighting. Needs `pip install mediapipe` and the model
    at <data dir>/models/hand_landmarker.task; `create()` returns None when either is missing, and the
    caller falls back to count_fingers()."""

    _TIPS, _PIPS = (8, 12, 16, 20), (6, 10, 14, 18)

    def __init__(self, landmarker):
        self._lm = landmarker
        self._t0 = time.monotonic()
        self.points = None           # last hand's landmarks in pixels, for the preview overlay

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
                min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5)
            return cls(vision.HandLandmarker.create_from_options(opts))
        except Exception:
            return None

    def count(self, frame_bgr, cv2) -> int:
        import mediapipe as mp
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._lm.detect_for_video(image, int((time.monotonic() - self._t0) * 1000))
        if not result.hand_landmarks:
            self.points = None
            return 0
        lm = result.hand_landmarks[0]
        h, w = frame_bgr.shape[:2]
        self.points = [(int(p.x * w), int(p.y * h)) for p in lm]
        return self.fingers_up([(p.x, p.y) for p in lm])

    @classmethod
    def fingers_up(cls, pts) -> int:
        """Extended fingers from 21 (x, y) landmarks: a finger is up when its tip is further from the
        wrist than its middle joint (works whatever way the hand is rotated); the thumb is up when its
        tip is far from the index knuckle compared with the palm's size."""
        def d(a, b):
            return math.dist(pts[a], pts[b])
        n = sum(1 for tip, pip in zip(cls._TIPS, cls._PIPS) if d(tip, 0) > d(pip, 0) * 1.1)
        palm = d(0, 9) or 1e-6
        if d(4, 5) / palm > 0.55 and d(4, 0) > d(3, 0):
            n += 1
        return n


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

_ON_MSG = ("Hand control is ON - watch the webcam window. Hold 1 finger to scroll DOWN, 2 to scroll UP "
           "(it speeds up the longer you hold); 3 = identify what you're holding, 4 = screenshot; "
           "open palm = stop (or say 'stop watching my hands').")


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
    ctl._run()
    if ctl.error:
        out(error=ctl.error)


if __name__ == "__main__":
    _child_main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
