"""Win32 calls the window manager needs (no pywin32), with safe no-op fallbacks off Windows.

On macOS/Linux the ctypes WinDLL bindings don't exist, so this module imports cleanly and the public
functions return sane defaults: the JARVIS panel then runs as an ordinary window (no side-docking or
focus-stealing, which are Windows-only). Those niceties would need AppKit/Accessibility on macOS.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

_WIN = sys.platform.startswith("win")

user32 = ctypes.WinDLL("user32", use_last_error=True) if _WIN else None
try:
    dwmapi = ctypes.WinDLL("dwmapi") if _WIN else None
except OSError:
    dwmapi = None

SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
GA_ROOT = 2
MONITOR_DEFAULTTOPRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", RECT)]


def _bind(name, restype, *argtypes):
    if not _WIN:
        return lambda *a, **k: 0  # off-Windows: harmless stub; public fns below have real fallbacks
    fn = getattr(user32, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


_GetForegroundWindow = _bind("GetForegroundWindow", wintypes.HWND)
_SetForegroundWindow = _bind("SetForegroundWindow", wintypes.BOOL, wintypes.HWND)
_BringWindowToTop = _bind("BringWindowToTop", wintypes.BOOL, wintypes.HWND)
_IsWindow = _bind("IsWindow", wintypes.BOOL, wintypes.HWND)
_IsWindowVisible = _bind("IsWindowVisible", wintypes.BOOL, wintypes.HWND)
_IsIconic = _bind("IsIconic", wintypes.BOOL, wintypes.HWND)
_IsZoomed = _bind("IsZoomed", wintypes.BOOL, wintypes.HWND)
_ShowWindow = _bind("ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_GetAncestor = _bind("GetAncestor", wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetClassNameW = _bind("GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowTextW = _bind("GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowRect = _bind("GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(RECT))
_SetWindowPos = _bind("SetWindowPos", wintypes.BOOL, wintypes.HWND, wintypes.HWND,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
_GetWindowPlacement = _bind("GetWindowPlacement", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT))
_SetWindowPlacement = _bind("SetWindowPlacement", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT))
_MonitorFromWindow = _bind("MonitorFromWindow", wintypes.HMONITOR, wintypes.HWND, wintypes.DWORD)
_MonitorFromPoint = _bind("MonitorFromPoint", wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD)
_GetMonitorInfoW = _bind("GetMonitorInfoW", wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
_keybd_event = _bind("keybd_event", None, wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t)

if dwmapi is not None:
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.UINT]


def set_dpi_awareness() -> None:
    """Work in physical pixels so window math is exact on scaled displays."""
    if not _WIN:
        return
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.restype = wintypes.BOOL
        fn.argtypes = [ctypes.c_ssize_t]
        if fn(-4):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    user32.SetProcessDPIAware()


def foreground() -> int | None:
    return _GetForegroundWindow() or None


def is_window(hwnd) -> bool:
    return bool(hwnd) and bool(_IsWindow(hwnd))


def class_name(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def title(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    _GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def is_app_window(hwnd) -> bool:
    """A real top-level window the user works in (not the desktop, taskbar or Start menu)."""
    if not is_window(hwnd) or not _IsWindowVisible(hwnd):
        return False
    if _GetAncestor(hwnd, GA_ROOT) != hwnd:
        return False
    return class_name(hwnd) not in SHELL_CLASSES


def toplevel(hwnd) -> int:
    return _GetAncestor(hwnd, GA_ROOT) or hwnd


def work_area(hwnd=None) -> Rect:
    """The usable area (screen minus taskbar) of the monitor holding hwnd."""
    if not _WIN:
        return Rect(0, 0, 1440, 900)  # off-Windows the panel doesn't dock; a default is enough
    monitor = _MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) if hwnd else \
        _MonitorFromPoint(wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not _GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    rc = info.rcWork
    return Rect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def frame_margins(hwnd) -> tuple[int, int, int, int]:
    """Windows 10 windows carry invisible resize borders; measure them so visible edges line up."""
    outer = RECT()
    if dwmapi is None or not _GetWindowRect(hwnd, ctypes.byref(outer)):
        return 0, 0, 0, 0
    inner = RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(inner), ctypes.sizeof(inner)) != 0:
        return 0, 0, 0, 0
    margins = (inner.left - outer.left, inner.top - outer.top, outer.right - inner.right, outer.bottom - inner.bottom)
    return margins if all(0 <= m <= 40 for m in margins) else (0, 0, 0, 0)


def place(hwnd, rect: Rect) -> bool:
    """Move and size a window so its visible frame fills rect."""
    if _IsIconic(hwnd) or _IsZoomed(hwnd):
        _ShowWindow(hwnd, SW_RESTORE)
    left, top, right, bottom = frame_margins(hwnd)
    return bool(_SetWindowPos(hwnd, None, rect.left - left, rect.top - top,
                              rect.width + left + right, rect.height + top + bottom,
                              SWP_NOZORDER | SWP_NOACTIVATE))


def get_placement(hwnd) -> WINDOWPLACEMENT | None:
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(placement)
    return placement if _GetWindowPlacement(hwnd, ctypes.byref(placement)) else None


def set_placement(hwnd, placement: WINDOWPLACEMENT) -> bool:
    placement.length = ctypes.sizeof(placement)
    if placement.showCmd == SW_SHOWMINIMIZED:
        placement.showCmd = SW_SHOWNORMAL
    return bool(_SetWindowPlacement(hwnd, ctypes.byref(placement)))


def bring_to_front(hwnd) -> bool:
    if _IsIconic(hwnd):
        _ShowWindow(hwnd, SW_RESTORE)
    if _SetForegroundWindow(hwnd):
        return True
    # Windows only lets the process that received the last input steal focus.
    # A synthetic Alt tap counts as input, which lets the terminal-triggered toggle focus the panel.
    _keybd_event(VK_MENU, 0, 0, 0)
    ok = _SetForegroundWindow(hwnd)
    _keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    _BringWindowToTop(hwnd)
    return bool(ok)
