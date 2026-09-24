"""A floating on-screen keyboard that pops up over everything and never steals focus (macOS).

The camera preview can't be used for typing: the moment you click in another app, that app comes
forward and the preview goes behind it. So the point-to-type keyboard gets its own window instead -
borderless, above every app and every desktop, click-through (it can never swallow a click), and
non-activating, so showing it does NOT take focus away from whatever you are typing into.

The keys are highlighted as your fingertip moves, so you watch the keyboard on screen, not the camera.
On Windows (and if pyobjc isn't there) this reports unavailable and the keyboard stays drawn inside
the preview window.
"""
from __future__ import annotations

import sys

MAC = sys.platform == "darwin"

HINT = "point at a letter, rest on it to type   -   thumbs-up (or 'hide the keyboard') to close"
LABELS = {"back": "delete", "enter": "enter", "space": "space", "done": "done"}


def render(rows, highlight, width: int, height: int, progress: float = 0.0, word: str = "",
           note: str = ""):
    """The keyboard as a BGRA image: dark translucent panel, the key under your finger lit up with a
    bar filling as it's about to type, and the word you're typing (plus any autocorrect) along the top."""
    import cv2
    import numpy as np

    img = np.zeros((height, width, 4), dtype=np.uint8)
    pad = max(6, width // 90)
    top = int(height * 0.15)                      # status strip above the keys
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.rectangle(img, (0, 0), (width - 1, height - 1), (28, 26, 24, 240), -1)
    cv2.rectangle(img, (0, 0), (width - 1, height - 1), (120, 200, 255, 255), 2)
    left = f"{word}|" if word else HINT
    cv2.putText(img, left, (pad + 4, int(top * 0.68)), font,
                max(0.34, width / (1300 if word else 2200)),
                (150, 245, 190, 255) if word else (170, 200, 220, 255), 2 if word else 1, cv2.LINE_AA)
    if note:
        scale = max(0.34, width / 2000)
        (tw, _), _ = cv2.getTextSize(note, font, scale, 1)
        cv2.putText(img, note, (width - tw - pad - 6, int(top * 0.68)), font, scale,
                    (120, 200, 255, 255), 1, cv2.LINE_AA)
    rh = (height - top - pad) / max(1, len(rows))
    for r, keys in enumerate(rows):
        kw = (width - 2 * pad) / max(1, len(keys))
        for c, key in enumerate(keys):
            x, y = pad + c * kw, top + r * rh
            x2, y2 = x + kw - pad / 2, y + rh - pad / 2
            on = key == highlight
            box = (int(x), int(y), int(x2), int(y2))
            cv2.rectangle(img, box[:2], box[2:], (90, 215, 120, 255) if on else (64, 60, 56, 255), -1)
            if not on:
                cv2.rectangle(img, box[:2], box[2:], (110, 104, 98, 255), 1)
            elif progress > 0:            # the bar fills while you rest: move away and nothing is typed
                bar = int((x2 - x - 8) * min(1.0, progress))
                cv2.rectangle(img, (int(x) + 4, int(y2) - 12), (int(x) + 4 + bar, int(y2) - 5),
                              (25, 70, 30, 255), -1)
            label = LABELS.get(key, key.upper())
            scale = max(0.4, rh / (52 if len(label) <= 2 else 110))
            (tw, th), _ = cv2.getTextSize(label, font, scale, 2)
            cv2.putText(img, label, (int(x + (kw - tw) / 2), int(y + (rh + th) / 2)), font, scale,
                        (20, 30, 20, 255) if on else (235, 235, 235, 255), 2, cv2.LINE_AA)
    return img


class KeyboardOverlay:
    """show() puts it on screen, update() lights the key under your finger, hide() takes it away."""

    MARGIN = 60          # px above the bottom of the screen
    MAX_WIDTH = 1040

    def __init__(self):
        self.window = None
        self.view = None
        self.size = (0, 0)
        self._drawn = None          # what we last drew: (key, progress, word, note)

    @staticmethod
    def available() -> bool:
        if not MAC:
            return False
        try:
            import AppKit          # noqa: F401
            import cv2             # noqa: F401
            import numpy           # noqa: F401
        except Exception:
            return False
        return True

    def _build(self):
        import AppKit

        AppKit.NSApplication.sharedApplication()
        screen = AppKit.NSScreen.mainScreen()
        frame = screen.frame() if screen else AppKit.NSMakeRect(0, 0, 1440, 900)
        sw, sh = float(frame.size.width), float(frame.size.height)
        w = int(min(self.MAX_WIDTH, sw * 0.72))
        h = int(w * 0.34)
        rect = AppKit.NSMakeRect((sw - w) / 2, self.MARGIN, w, h)
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        win = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False)
        win.setLevel_(AppKit.NSStatusWindowLevel)      # above ordinary and floating windows
        win.setOpaque_(False)
        win.setBackgroundColor_(AppKit.NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)               # clicks go straight through to the app below
        win.setHidesOnDeactivate_(False)               # stays put when another app is in front
        win.setBecomesKeyOnlyIfNeeded_(True)
        win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                   AppKit.NSWindowCollectionBehaviorStationary |
                                   AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary |
                                   AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        view = AppKit.NSImageView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, w, h))
        view.setImageScaling_(AppKit.NSImageScaleAxesIndependently)
        win.setContentView_(view)
        self.window, self.view, self.size = win, view, (w, h)

    def show(self, rows) -> bool:
        """Put the keyboard on screen. Returns False if this machine can't (then use the preview)."""
        if not self.available():
            return False
        try:
            if self.window is None:
                self._build()
            self._drawn = None
            self.update(rows, None)
            self.window.orderFrontRegardless()        # visible without activating JARVIS
            self.pump()
            return True
        except Exception:
            self.window = self.view = None
            return False

    def update(self, rows, highlight, progress: float = 0.0, word: str = "", note: str = "") -> None:
        """Redraw only when something visible changed - a few times a second, not thirty."""
        state = (highlight, round(float(progress or 0.0) * 8), word, note)
        if self.window is None or state == self._drawn:
            return
        self._drawn = state
        try:
            import AppKit
            import cv2

            w, h = self.size
            ok, buf = cv2.imencode(".png", render(rows, highlight, w, h, progress, word, note))
            if not ok:
                return
            raw = buf.tobytes()
            data = AppKit.NSData.dataWithBytes_length_(raw, len(raw))
            image = AppKit.NSImage.alloc().initWithData_(data)
            self.view.setImage_(image)
            self.window.displayIfNeeded()
            self.pump()
        except Exception:
            pass

    def pump(self) -> None:
        """Let AppKit draw. The camera loop owns the main thread, so nobody runs an event loop for us."""
        try:
            from Foundation import NSDate, NSRunLoop

            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.0))
        except Exception:
            pass

    def hide(self) -> None:
        if self.window is None:
            return
        try:
            self.window.orderOut_(None)
            self.pump()
        except Exception:
            pass
        self._drawn = None

    def visible(self) -> bool:
        try:
            return bool(self.window is not None and self.window.isVisible())
        except Exception:
            return False
