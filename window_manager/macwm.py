"""macOS 60/40 window docking via the Accessibility API (pyobjc).

Mirrors what win32.py does on Windows: on summon, move the window you were working in to 60% of the
screen and dock the JARVIS panel into the other 40%; on hide, put your window back where it was. Moving
another app's window needs Accessibility permission (the same one the global hotkey uses).

Everything is best-effort and never raises: if pyobjc isn't installed, permission is missing, or an app
refuses to be moved, JARVIS just shows as a normal floating panel. `available()` gates the whole thing.
"""
from __future__ import annotations

import os
import sys

_AVAILABLE = False
if sys.platform == "darwin":
    try:
        from AppKit import NSApplication, NSScreen, NSWorkspace
        from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateApplication,
                                          AXUIElementSetAttributeValue, AXValueCreate, AXValueGetValue)
        from Quartz import CGPoint, CGSize
        _AVAILABLE = True
    except Exception:
        _AVAILABLE = False

# AX attribute names are plain strings; using the literals avoids pyobjc version-to-version constant
# renames. AXValueType: CGPoint = 1, CGSize = 2.
_POS, _SIZE, _FOCUSED, _TITLE = "AXPosition", "AXSize", "AXFocusedWindow", "AXTitle"
_T_POINT, _T_SIZE = 1, 2


def available() -> bool:
    return _AVAILABLE


class MacWindow:
    """A handle to another app's window, movable through the Accessibility API."""

    def __init__(self, pid: int, ax_window):
        self.pid = pid
        self.ax = ax_window

    def title(self) -> str:
        try:
            err, val = AXUIElementCopyAttributeValue(self.ax, _TITLE, None)
            return str(val) if err == 0 and val else ""
        except Exception:
            return ""

    def frame(self):
        """(x, y, w, h) in top-left screen coordinates, or None."""
        try:
            e1, pos = AXUIElementCopyAttributeValue(self.ax, _POS, None)
            e2, size = AXUIElementCopyAttributeValue(self.ax, _SIZE, None)
            if e1 or e2 or pos is None or size is None:
                return None
            okp, point = AXValueGetValue(pos, _T_POINT, None)
            oks, dims = AXValueGetValue(size, _T_SIZE, None)
            if not (okp and oks):
                return None
            return float(point.x), float(point.y), float(dims.width), float(dims.height)
        except Exception:
            return None

    def move_resize(self, x, y, w, h) -> bool:
        try:
            AXUIElementSetAttributeValue(self.ax, _POS, AXValueCreate(_T_POINT, CGPoint(float(x), float(y))))
            AXUIElementSetAttributeValue(self.ax, _SIZE, AXValueCreate(_T_SIZE, CGSize(float(w), float(h))))
            return True
        except Exception:
            return False


def frontmost_window():
    """The focused window of the frontmost app (excluding JARVIS itself), as a MacWindow, or None.
    Captured at hotkey-press time, so it's the window you were actually working in."""
    if not _AVAILABLE:
        return None
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        if pid == os.getpid():
            return None  # JARVIS is frontmost; nothing to dock beside
        ax_app = AXUIElementCreateApplication(pid)
        err, win = AXUIElementCopyAttributeValue(ax_app, _FOCUSED, None)
        if err or win is None:
            return None
        return MacWindow(pid, win)
    except Exception:
        return None


def work_area():
    """(left, top, width, height) of the main screen minus the menu bar and Dock, in top-left coords
    (AX and Tk both use a top-left origin; NSScreen reports bottom-left, so we flip Y)."""
    screen = NSScreen.mainScreen()
    visible = screen.visibleFrame()
    full = screen.frame()
    left = float(visible.origin.x)
    top = float(full.size.height - (visible.origin.y + visible.size.height))
    return left, top, float(visible.size.width), float(visible.size.height)


def activate_self() -> None:
    """Bring JARVIS (this process) to the foreground so the panel can take keyboard focus."""
    try:
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


class MacSplit:
    """The macOS 60/40 split, matching SplitController's role on Windows."""

    def __init__(self, ratio: float = 0.6, jarvis_side: str = "right"):
        self.ratio = ratio
        self.jarvis_side = jarvis_side
        self.active = False
        self.target: MacWindow | None = None
        self._saved = None

    def enter(self, root, target: MacWindow | None) -> str:
        from .split import compute_layout
        from .win32 import Rect

        left, top, width, height = work_area()
        main, jarvis = compute_layout(Rect(int(left), int(top), int(width), int(height)),
                                      self.ratio, self.jarvis_side)
        self.target, self._saved = None, None
        if target is not None:
            saved = target.frame()
            if saved is not None:
                self.target, self._saved = target, saved
                target.move_resize(main.left, main.top, main.width, main.height)
        try:
            root.geometry(f"{jarvis.width}x{jarvis.height}+{jarvis.left}+{jarvis.top}")
        except Exception:
            pass
        self.active = True
        if self.target is not None:
            return f"Split with '{self.target.title() or 'your window'}'."
        return "No app window to split with; JARVIS docked alone."

    def exit(self, root=None) -> None:
        if self.active and self.target is not None and self._saved is not None:
            x, y, w, h = self._saved
            self.target.move_resize(x, y, w, h)
        self.active = False
        self.target, self._saved = None, None
