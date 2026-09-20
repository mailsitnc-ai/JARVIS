"""Global hotkey through RegisterHotKey, served by its own Win32 message loop thread.

Default Ctrl+Alt+J. On a French AZERTY keyboard Ctrl+Alt is AltGr, and AltGr+J types nothing,
so the shortcut never collides with a character.

The listener also supports a special spec ``clap`` which triggers the callback after
detecting two claps.  The actual audio‑processing implementation is deliberately
light‑weight: it uses the ``sounddevice`` library (if available) to capture short
audio chunks and counts peaks that exceed a configurable threshold.  If the library
is missing the ``clap`` spec degrades gracefully and simply invokes the callback
immediately twice.
"""
from __future__ import annotations

import ctypes
import re
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

_WIN = sys.platform.startswith("win")

try:
    import numpy as np
    import sounddevice as sd
except Exception:  # pragma: no cover – optional dependency
    sd = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

MODIFIERS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "home": 0x24,
    "end": 0x23,
    "insert": 0x2D,
    "delete": 0x2E,
    "pageup": 0x21,
    "pagedown": 0x22,
    "pause": 0x13,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse a hotkey specification like ``'ctrl+alt+j'`` into (modifiers, vk)."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    modifiers, key = 0, None
    for part in parts:
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
        elif key is None:
            key = part
        else:
            raise ValueError(f"'{spec}' has more than one non-modifier key")
    if key is None:
        raise ValueError(f"'{spec}' has no key, only modifiers")
    if modifiers == 0:
        raise ValueError(f"'{spec}' needs at least one of ctrl, alt, shift, win")
    if len(key) == 1 and key.isascii() and key.isalnum():
        return modifiers, ord(key.upper())
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key):
        return modifiers, 0x70 + int(key[1:]) - 1
    if key in NAMED_KEYS:
        return modifiers, NAMED_KEYS[key]
    raise ValueError(f"unknown key '{key}' in '{spec}'")


class HotkeyListener(threading.Thread):
    """Listen for a Windows hotkey or a double‑clap gesture."""

    HOTKEY_ID = 0x4A41
    _CLAP_THRESHOLD = 0.6  # RMS amplitude threshold for a clap
    _CLAP_MIN_INTERVAL = 0.2  # seconds between two claps
    _CLAP_MAX_INTERVAL = 1.5  # seconds allowed between first and second clap

    def __init__(
        self,
        spec: str,
        callback: Callable[[], None],
        hotkey_id: Optional[int] = None,
        name: str = "jarvis-hotkey",
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.spec = spec.strip().lower()
        self.callback = callback
        self.hotkey_id = hotkey_id if hotkey_id is not None else self.HOTKEY_ID
        self.error: Optional[str] = None
        self.ready = threading.Event()
        self._thread_id: Optional[int] = None

    # --------------------------------------------------------------------- #
    # Clap detection helpers
    # --------------------------------------------------------------------- #
    def _detect_claps(self) -> None:
        """Detect two claps using the microphone and invoke the callback."""
        if sd is None or np is None:
            # Fallback: immediately invoke callback twice
            self.callback()
            self.callback()
            return

        def _callback(indata, frames, time_info, status):
            rms = np.sqrt(np.mean(indata**2))
            if rms > self._CLAP_THRESHOLD:
                timestamps.append(time.time())

        timestamps: list[float] = []
        try:
            with sd.InputStream(callback=_callback, channels=1, samplerate=44100):
                while len(timestamps) < 2:
                    time.sleep(0.05)
                    # prune old timestamps
                    now = time.time()
                    timestamps = [t for t in timestamps if now - t <= self._CLAP_MAX_INTERVAL]
                    if len(timestamps) == 2:
                        interval = timestamps[1] - timestamps[0]
                        if self._CLAP_MIN_INTERVAL <= interval <= self._CLAP_MAX_INTERVAL:
                            self.callback()
                        else:
                            # Not a valid double clap; reset and keep listening
                            timestamps = []
        except Exception as exc:  # pragma: no cover – hardware issues
            self.error = f"clap detection failed: {exc}"
            self.callback()  # still fire once to avoid silent failure

    # --------------------------------------------------------------------- #
    # Thread run implementation
    # --------------------------------------------------------------------- #
    def run(self) -> None:
        if self.spec == "clap":
            # No Win32 registration needed – just signal readiness and start detection.
            self.ready.set()
            self._detect_claps()
            return

        if not _WIN:
            self._run_non_windows()
            return

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32")
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL

        try:
            modifiers, vk = parse_hotkey(self.spec)
        except ValueError as exc:
            self.error = str(exc)
            self.ready.set()
            return

        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, self.hotkey_id, modifiers | MOD_NOREPEAT, vk):
            self.error = (
                f"{self.spec} is already used by another app; choose another with: "
                "jarvis config --set window.hotkey=..."
            )
            self.ready.set()
            return

        self.ready.set()

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, self.hotkey_id)

    def _pynput_combo(self, spec: str) -> str | None:
        """Turn 'ctrl+alt+j' into pynput's '<ctrl>+<alt>+j' form."""
        mods = {"ctrl": "<ctrl>", "control": "<ctrl>", "alt": "<alt>", "option": "<alt>",
                "shift": "<shift>", "cmd": "<cmd>", "command": "<cmd>", "win": "<cmd>", "super": "<cmd>"}
        parts = [p.strip() for p in spec.split("+") if p.strip()]
        if not parts:
            return None
        return "+".join(mods.get(p, p) for p in parts)

    def _run_non_windows(self) -> None:
        """macOS/Linux global hotkey via pynput (needs Accessibility permission on macOS). Degrades to a
        clear error if pynput isn't installed, without crashing the daemon."""
        try:
            from pynput import keyboard
        except Exception:
            self.error = ("global hotkey needs 'pynput' on macOS/Linux (pip install pynput) "
                          "and Accessibility permission")
            self.ready.set()
            return
        combo = self._pynput_combo(self.spec)
        if not combo:
            self.error = f"couldn't parse hotkey {self.spec!r}"
            self.ready.set()
            return
        try:
            self._listener = keyboard.GlobalHotKeys({combo: self.callback})
            self._listener.start()
            self.ready.set()
            self._listener.join()
        except Exception as exc:
            self.error = f"hotkey unavailable: {exc}"
            self.ready.set()

    def stop(self) -> None:
        """Stop the listener thread."""
        listener = getattr(self, "_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        if _WIN and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)