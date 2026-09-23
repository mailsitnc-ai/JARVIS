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
import logging
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


log = logging.getLogger("jarvis.hotkey")


class ComboMatcher:
    """Tracks which modifiers are down and fires when the combination is pressed. Separate from the
    listener so it can be tested without a keyboard."""

    def __init__(self, mods: set, main: str, callback, debug=None):
        self.mods, self.main, self.callback = set(mods), main, callback
        self.held: set = set()
        # Diagnostics only (window.hotkey_debug): reports keys pressed WHILE A MODIFIER IS HELD, and
        # stops after 30, so it can never act as a keylogger.
        self.debug, self.debug_left = debug, 30

    def _log(self, identity):
        if self.debug and self.debug_left > 0 and self.held:
            self.debug_left -= 1
            self.debug(f"key {identity!r} with {sorted(self.held) or 'no modifiers'} "
                       f"(waiting for {sorted(self.mods)} + {self.main!r})")

    def press(self, key) -> bool:
        mod = HotkeyListener.modifier_name(key)
        if mod:
            self.held.add(mod)
            return False
        identity = HotkeyListener.key_identity(key)
        self._log(identity)
        if self.held >= self.mods and identity == self.main:
            try:
                self.callback()
            except Exception:
                pass
            return True
        return False

    def release(self, key) -> None:
        mod = HotkeyListener.modifier_name(key)
        if mod:
            self.held.discard(mod)


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
        # Named keys (space, enter, tab, esc, f1...) are written '<space>' in pynput; letters stay bare.
        return "+".join(mods.get(p.lower()) or (p if len(p) == 1 else f"<{p.lower()}>") for p in parts)

    # ---- macOS matching ------------------------------------------------------------------------
    # Ctrl+<letter> arrives as a CONTROL CHARACTER on macOS (Ctrl+J is chr(10), Ctrl+C is chr(3)), so
    # pynput's own GlobalHotKeys never matches "<ctrl>+<shift>+j" - the hotkey registers fine and then
    # silently does nothing. We watch the modifier keys ourselves and undo that mapping.
    _MOD_ALIASES = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "opt": "alt",
                    "shift": "shift", "cmd": "cmd", "command": "cmd", "win": "cmd", "super": "cmd"}

    @classmethod
    def parse_spec(cls, spec: str):
        """'ctrl+shift+j' -> ({'ctrl','shift'}, 'j'); 'ctrl+shift+space' -> (..., 'space')."""
        parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
        mods, main = set(), None
        for part in parts:
            if part in cls._MOD_ALIASES:
                mods.add(cls._MOD_ALIASES[part])
            else:
                main = part
        return (mods, main) if main else (mods, None)

    @staticmethod
    def key_identity(key) -> str | None:
        """What key was pressed, as a plain name: 'j', '3', 'space', 'f5'... None for modifier keys."""
        char = getattr(key, "char", None)
        if char:
            if len(char) == 1 and ord(char) < 32:      # Ctrl+letter -> control character
                return chr(ord(char) + 96)             # chr(10) -> 'j', chr(3) -> 'c'
            return char.lower()
        name = getattr(key, "name", None) or str(key).replace("Key.", "")
        if name in ("ctrl", "ctrl_l", "ctrl_r", "shift", "shift_l", "shift_r", "shift_r",
                    "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r"):
            return None
        return name.lower() or None

    @staticmethod
    def modifier_name(key) -> str | None:
        name = getattr(key, "name", None) or str(key).replace("Key.", "")
        for prefix, mod in (("ctrl", "ctrl"), ("shift", "shift"), ("alt", "alt"), ("cmd", "cmd")):
            if name.startswith(prefix):
                return mod
        return None

    @staticmethod
    def input_monitoring() -> int | None:
        """macOS only: may this process LISTEN to keys? 0 granted, 1 denied, 2 not asked yet, None n/a.

        Registering a global hotkey "succeeds" without this permission - the keystrokes simply never
        arrive - so check it explicitly instead of reporting a working hotkey that does nothing.
        Permission to listen (Input Monitoring) is separate from permission to move the mouse
        (Accessibility), which is why hand control can work while the hotkey doesn't.
        """
        if sys.platform != "darwin":
            return None
        try:
            import ctypes.util
            iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
            iokit.IOHIDCheckAccess.restype = ctypes.c_int
            iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint]
            state = int(iokit.IOHIDCheckAccess(1))          # kIOHIDRequestTypeListenEvent
            if state != 0:                                   # ask macOS to show the prompt once
                iokit.IOHIDRequestAccess.restype = ctypes.c_bool
                iokit.IOHIDRequestAccess.argtypes = [ctypes.c_uint]
                iokit.IOHIDRequestAccess(1)
            return state
        except Exception:
            return None

    # macOS virtual key codes (ANSI layout) - the physical key, whatever character it produces. Using
    # these avoids the Ctrl+<letter> control-character mess entirely.
    _MAC_KEYS = {0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c", 9: "v", 11: "b",
                 12: "q", 13: "w", 14: "e", 15: "r", 16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4",
                 22: "6", 23: "5", 24: "=", 25: "9", 26: "7", 27: "-", 28: "8", 29: "0", 30: "]", 31: "o",
                 32: "u", 33: "[", 34: "i", 35: "p", 36: "return", 37: "l", 38: "j", 39: "'", 40: "k",
                 41: ";", 42: "\\", 43: ",", 44: "/", 45: "n", 46: "m", 47: ".", 48: "tab", 49: "space",
                 50: "`", 51: "delete", 53: "escape", 96: "f5", 97: "f6", 98: "f7", 99: "f3", 100: "f8",
                 101: "f9", 103: "f11", 109: "f10", 111: "f12", 118: "f4", 120: "f2", 122: "f1",
                 123: "left", 124: "right", 125: "down", 126: "up"}

    def _run_mac_tap(self, mods_wanted, main_wanted, debug) -> bool:
        """Listen with macOS's own event tap (Quartz). Returns False if it can't be set up, so the
        caller can fall back to pynput. This is the reliable path: pynput's listener can sit there
        receiving nothing at all, with no error, even when Input Monitoring is granted."""
        try:
            import CoreFoundation
            import Quartz
        except Exception as exc:
            log.info("hotkey: Quartz unavailable (%s) - falling back to pynput", exc)
            return False
        wanted_flags = {"ctrl": Quartz.kCGEventFlagMaskControl, "shift": Quartz.kCGEventFlagMaskShift,
                        "alt": Quartz.kCGEventFlagMaskAlternate, "cmd": Quartz.kCGEventFlagMaskCommand}
        need = 0
        for mod in mods_wanted:
            need |= wanted_flags.get(mod, 0)

        def handler(proxy, event_type, event, refcon):
            try:
                if event_type == Quartz.kCGEventTapDisabledByTimeout and self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)     # macOS switched us off; switch back on
                    log.info("hotkey: event tap re-enabled after a timeout")
                    return event
                if event_type != Quartz.kCGEventKeyDown:
                    return event
                code = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
                flags = int(Quartz.CGEventGetFlags(event))
                key = self._MAC_KEYS.get(code, str(code))
                if debug and (flags & (Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskCommand |
                                       Quartz.kCGEventFlagMaskAlternate)):
                    debug(f"key {key!r} (code {code}) flags {flags:#x}")
                if (flags & need) == need and key == main_wanted:
                    threading.Thread(target=self.callback, daemon=True).start()
            except Exception as exc:
                log.warning("hotkey: %s", exc)
            return event

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        self._tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                            Quartz.kCGEventTapOptionListenOnly, mask, handler, None)
        if not self._tap:
            log.info("hotkey: couldn't create an event tap - falling back to pynput")
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = CoreFoundation.CFRunLoopGetCurrent()
        CoreFoundation.CFRunLoopAddSource(loop, source, CoreFoundation.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self._runloop = loop
        self.ready.set()
        log.info("hotkey[%s]: listening via the macOS event tap", self.spec)
        CoreFoundation.CFRunLoopRun()
        return True

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
        mods_wanted, main_wanted = self.parse_spec(self.spec)
        if not main_wanted:
            self.error = f"couldn't parse hotkey {self.spec!r}"
            self.ready.set()
            return
        self._tap = getattr(self, "_tap", None)
        self._runloop = getattr(self, "_runloop", None)
        access = self.input_monitoring()
        if access not in (0, None):
            self.error = ("macOS isn't letting me hear the keyboard yet - allow JARVIS in System Settings > "
                          "Privacy & Security > Input Monitoring (not just Accessibility), then restart "
                          "JARVIS. Until then, summon me from the menu or say 'Jarvis'.")
            self.ready.set()
            return
        debug = None
        try:
            from core.config import load_settings
            if load_settings().get("window.hotkey_debug", False):
                debug = lambda text: log.info("hotkey[%s]: %s", self.spec, text)  # noqa: E731
        except Exception:
            pass
        if sys.platform == "darwin" and self._run_mac_tap(mods_wanted, main_wanted, debug):
            return
        matcher = ComboMatcher(mods_wanted, main_wanted, self.callback, debug)
        if debug:
            log.info("hotkey[%s]: listening for %s + %r (input monitoring: %s)", self.spec,
                     sorted(mods_wanted), main_wanted, self.input_monitoring())
        try:
            self._listener = keyboard.Listener(on_press=matcher.press, on_release=matcher.release)
            self._listener.start()
            self.ready.set()
            self._listener.join()
        except Exception as exc:
            self.error = f"hotkey unavailable: {exc}"
            self.ready.set()

    def stop(self) -> None:
        """Stop the listener thread."""
        loop = getattr(self, "_runloop", None)
        if loop is not None:
            try:
                import CoreFoundation
                CoreFoundation.CFRunLoopStop(loop)
            except Exception:
                pass
            self._runloop = None
        listener = getattr(self, "_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        if _WIN and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)