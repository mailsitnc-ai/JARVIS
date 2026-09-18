"""Control JARVIS with hand gestures through the webcam - a lightweight, dependency-light approach.

No MediaPipe (it won't run on this CPU): just OpenCV. It watches a region of the webcam, segments the
hand by skin colour, and counts extended fingers via convex-hull defects. A stable finger count fires a
mapped JARVIS command (with a cooldown so one gesture = one action). An open palm (5) stops it.

Privacy: this only runs from an explicit command you approve - the broker gates it on the SENSITIVE
'camera' capability, so it never starts from a background/autonomy task. A preview window shows what it
sees and the count, and closing that window (or the open-palm gesture) stops it.
"""
from __future__ import annotations

import math
import threading
import time

# finger count -> the command JARVIS runs. Deliberately non-webcam actions (the loop holds the camera),
# so a fired command never fights the gesture loop for the device. 5 = stop, 0 = rest.
DEFAULT_MAP = {1: "take a screenshot", 2: "what time is it", 3: "what's on my screen"}
_WINDOW = "JARVIS hand control"
_ACTIVE = None
_LOCK = threading.Lock()


class GestureController:
    def __init__(self, runner, emit=None, mapping=None, camera_index=0, stable_frames=10, cooldown=3.0):
        self.runner = runner or (lambda cmd: None)
        self.emit = emit or (lambda *a: None)
        self.mapping = dict(mapping or DEFAULT_MAP)
        self.camera_index = camera_index
        self.stable_frames = stable_frames
        self.cooldown = cooldown
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
        self.emit("gesture", "Hand control on - open palm (5 fingers) to stop.")
        last, stable, next_fire = -1, 0, 0.0
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
                fired = None
                now = time.time()
                if stable == self.stable_frames and now >= next_fire:
                    if count >= 5:
                        self._stop.set()
                    elif count in self.mapping:
                        fired = self.mapping[count]
                        try:
                            self.runner(fired)
                        except Exception:
                            pass
                        self.emit("gesture", f"{count} fingers -> {fired}")
                    next_fire = now + self.cooldown
                self._draw(frame, cv2, (x0, y0, x1, y1), count, fired)
                cv2.imshow(_WINDOW, frame)
                if (cv2.waitKey(1) & 0xFF) == ord("q") or cv2.getWindowProperty(_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break
        except Exception as exc:            # never let a CV hiccup crash the daemon
            self.error = str(exc)
        finally:
            cap.release()
            try:
                cv2.destroyWindow(_WINDOW)
                cv2.waitKey(1)
            except Exception:
                pass
            self.emit("gesture", "Hand control stopped.")

    def _draw(self, frame, cv2, box, count, fired):
        x0, y0, x1, y1 = box
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 255, 0), 2)
        cv2.putText(frame, f"Fingers: {count}", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        legend = "1 shot  2 time  3 screen  |  open palm = stop"
        cv2.putText(frame, legend, (12, frame.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
        if fired:
            cv2.putText(frame, fired, (12, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)


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
    if area < 3000:                       # nothing hand-sized in view
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
        if angle <= math.pi / 2 and depth > 10000:   # a V-gap between two fingers
            gaps += 1
    if gaps > 0:
        return min(gaps + 1, 5)
    return 1 if area > 6000 else 0        # one finger / fist have no gaps


# ---- module-level singleton (the active session) ----------------------------------------------

def start(runner, emit=None, mapping=None, camera_index=0) -> str:
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            return "Hand control is already on. Show an open palm (or say 'stop watching my hands')."
        _ACTIVE = GestureController(runner, emit, mapping, camera_index)
        _ACTIVE.start()
    time.sleep(0.5)
    if _ACTIVE.error:
        return f"Couldn't start hand control: {_ACTIVE.error}"
    return ("Hand control is ON - watch the webcam window. 1 finger = screenshot, 2 = time, "
            "3 = read my screen; open palm = stop (or say 'stop watching my hands').")


def stop() -> str:
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            _ACTIVE.stop()
            return "Stopping hand control."
    return "Hand control isn't running."


def running() -> bool:
    with _LOCK:
        return _ACTIVE is not None and _ACTIVE.running()
