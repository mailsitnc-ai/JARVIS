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
"""
from __future__ import annotations

import math
import threading
import time

_WINDOW = "JARVIS hand control"
_ACTIVE = None
_LOCK = threading.Lock()


class GestureController:
    def __init__(self, runner, emit=None, camera_index=0, stable_frames=12, cooldown=4.0,
                 scroll_delta=100, scroll_every=2):
        self.runner = runner or (lambda cmd: None)
        self.emit = emit or (lambda *a: None)
        self.camera_index = camera_index
        self.stable_frames = stable_frames        # hold this many steady frames before anything fires (deliberate)
        self.cooldown = cooldown                  # seconds between one-shot fires
        self.scroll_delta = scroll_delta          # wheel notches per scroll step
        self.scroll_every = scroll_every          # scroll every Nth frame while held (lower = faster)
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

    def _scroll(self, up: bool):
        try:
            import ctypes
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(self.scroll_delta if up else -self.scroll_delta), 0)
        except Exception:
            pass

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
        cap = cv2.VideoCapture(self.camera_index, getattr(cv2, "CAP_DSHOW", 0))
        if not cap or not cap.isOpened():
            self.error = "couldn't open the webcam (is it in use by another app?)"
            if cap:
                cap.release()
            return
        gui = True                     # opencv-python-headless has no HighGUI - detect and run without a window
        try:
            cv2.namedWindow(_WINDOW, cv2.WINDOW_AUTOSIZE)
        except cv2.error:
            gui = False
            self.emit("gesture", "No preview window (headless OpenCV) - gestures still work; open palm to stop.")
        self.emit("gesture", "Hand control on - 1 down, 2 up (hold to keep scrolling), 3 identify, "
                             "4 screenshot; open palm = stop.")
        last, stable, next_fire, tick = -1, 0, 0.0, 0
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    continue
                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                x0, y0, x1, y1 = int(w * 0.55), int(h * 0.10), w - 10, int(h * 0.65)
                count = count_fingers(frame[y0:y1, x0:x1], cv2, np)
                stable = stable + 1 if count == last else 1
                last = count
                now = time.time()
                label = None
                if stable >= self.stable_frames:
                    if count >= 5:
                        self._stop.set()
                    elif count in (1, 2):                       # HOLD: scroll while shown
                        label = "scroll down" if count == 1 else "scroll up"
                        tick += 1
                        if tick % self.scroll_every == 0:
                            self._scroll(up=(count == 2))
                    elif count == 3 and now >= next_fire:       # ONE-SHOT: identify (vision)
                        label = "identify"
                        self._fire_async(self._identify, frame.copy())
                        next_fire = now + self.cooldown
                    elif count == 4 and now >= next_fire:       # ONE-SHOT: screenshot
                        label = "screenshot"
                        self._fire_async(self.runner, "take a screenshot")
                        next_fire = now + self.cooldown
                else:
                    tick = 0
                if gui:
                    self._draw(frame, cv2, (x0, y0, x1, y1), count, label, stable)
                    try:
                        cv2.imshow(_WINDOW, frame)
                        if (cv2.waitKey(1) & 0xFF) == ord("q") or \
                                cv2.getWindowProperty(_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                            break
                    except cv2.error:
                        gui = False          # window died mid-run - keep going headless
                else:
                    time.sleep(0.03)         # no waitKey to pace us; ~30fps and easy on the CPU
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

    def _draw(self, frame, cv2, box, count, label, stable):
        x0, y0, x1, y1 = box
        ready = stable >= self.stable_frames
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 255, 0) if ready else (0, 180, 180), 2)
        cv2.putText(frame, f"Fingers: {count}", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        cv2.putText(frame, "1 down  2 up (hold)  3 identify  4 shot  |  open palm = stop",
                    (12, frame.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
        if label:
            cv2.putText(frame, label, (12, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)


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

def start(runner, emit=None, camera_index=0) -> str:
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            return "Hand control is already on. Show an open palm (or say 'stop watching my hands')."
        _ACTIVE = GestureController(runner, emit, camera_index)
        _ACTIVE.start()
    time.sleep(0.5)
    if _ACTIVE.error:
        return f"Couldn't start hand control: {_ACTIVE.error}"
    return ("Hand control is ON - watch the webcam window. Hold 1 finger to scroll DOWN, 2 to scroll UP "
            "(keeps going while you hold it); 3 = identify what you're holding, 4 = screenshot; "
            "open palm = stop (or say 'stop watching my hands').")


def stop() -> str:
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            _ACTIVE.stop()
            return "Stopping hand control."
    return "Hand control isn't running."


def running() -> bool:
    with _LOCK:
        return _ACTIVE is not None and _ACTIVE.running()
