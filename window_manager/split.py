"""The 60/40 split: the window you were working in keeps 60%, JARVIS docks into the other 40%.

Toggling off puts your window back exactly where it was (including maximized state).
"""
from __future__ import annotations

from . import win32
from .win32 import Rect


def compute_layout(area: Rect, ratio: float = 0.6, jarvis_side: str = "right") -> tuple[Rect, Rect]:
    """Returns (your window, JARVIS) rectangles inside the monitor work area."""
    ratio = min(max(float(ratio), 0.2), 0.8)
    main_width = round(area.width * ratio)
    side_width = area.width - main_width
    if jarvis_side == "left":
        jarvis = Rect(area.left, area.top, side_width, area.height)
        main = Rect(area.left + side_width, area.top, main_width, area.height)
    else:
        main = Rect(area.left, area.top, main_width, area.height)
        jarvis = Rect(area.left + main_width, area.top, side_width, area.height)
    return main, jarvis


class SplitController:
    def __init__(self, ratio: float = 0.6, jarvis_side: str = "right"):
        self.ratio = ratio
        self.jarvis_side = jarvis_side
        self.active = False
        self.target: int | None = None
        self._saved = None

    def enter(self, jarvis_hwnd: int, target_hwnd: int | None = None) -> str:
        target = target_hwnd if target_hwnd and target_hwnd != jarvis_hwnd and win32.is_app_window(target_hwnd) else None
        if target is not None and target != self.target:
            self._saved = win32.get_placement(target)
            self.target = target
        elif target is None:
            self.target, self._saved = None, None

        area = win32.work_area(target or jarvis_hwnd)
        main_rect, jarvis_rect = compute_layout(area, self.ratio, self.jarvis_side)
        moved = win32.place(target, main_rect) if target else False
        win32.place(jarvis_hwnd, jarvis_rect)
        self.active = True
        if target and not moved:
            return f"Could not resize '{win32.title(target)}' (apps running as administrator refuse to be moved)."
        return f"Split with '{win32.title(target)}'." if target else "No app window to split with; JARVIS docked alone."

    def exit(self) -> None:
        if self.active and self.target and win32.is_window(self.target) and self._saved is not None:
            win32.set_placement(self.target, self._saved)
            win32.bring_to_front(self.target)
        self.active = False
        self.target, self._saved = None, None
