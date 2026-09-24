"""Platform abstraction so JARVIS runs natively on macOS and Windows (best-effort Linux).

Everything OS-specific - the per-user data directory, opening files/apps, screenshots, text-to-speech,
mouse-wheel scrolling, desktop notifications, launching a Python program in a visible window - lives
here and branches on the running platform. The rest of JARVIS calls these helpers instead of touching
Windows (or macOS) APIs directly, so one codebase serves both machines.
"""
from __future__ import annotations

import os
import shutil
import re
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
PLATFORM_NAME = "windows" if IS_WINDOWS else ("mac" if IS_MAC else "linux")

# 0 everywhere but Windows, so passing it as creationflags is a harmless no-op off-Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _popen(args, **kw):
    """Detached Popen with the windowless flag on Windows; a plain detached process elsewhere."""
    kw.setdefault("close_fds", True)
    if IS_WINDOWS:
        kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(args, **kw)


# ---- per-user data directory ------------------------------------------------------------------

def platform_phrase() -> str:
    """How prompts describe this machine to the model ("Mac (macOS)", "Windows PC", "Linux PC")."""
    return "Mac (macOS)" if IS_MAC else ("Windows PC" if IS_WINDOWS else "Linux PC")


def platform_key() -> str:
    return "macos" if IS_MAC else ("windows" if IS_WINDOWS else "linux")


def user_data_dir() -> Path:
    """Where JARVIS keeps its config/state/keys: %APPDATA%\\JARVIS on Windows,
    ~/Library/Application Support/JARVIS on macOS, ~/.config/JARVIS on Linux. JARVIS_DATA_DIR overrides
    it (the test suite sets it so tests can never touch the real keys/config)."""
    override = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if override:
        path = Path(override)
    elif IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / "JARVIS"
    elif IS_MAC:
        path = Path.home() / "Library" / "Application Support" / "JARVIS"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "JARVIS"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---- opening files, folders and apps ----------------------------------------------------------

def open_path(target: str) -> None:
    """Open a file or folder with its default application."""
    if IS_WINDOWS:
        os.startfile(target)  # noqa: S606 - the documented Windows way to open by association
    elif IS_MAC:
        _popen(["open", str(target)])
    else:
        _popen(["xdg-open", str(target)])


def reveal_in_folder(target: str) -> None:
    """Open the folder containing a file, with the file highlighted."""
    target = str(target)
    if IS_WINDOWS:
        subprocess.run(["explorer", f"/select,{target}"], creationflags=NO_WINDOW)  # returns non-zero on success
    elif IS_MAC:
        subprocess.run(["open", "-R", target])
    else:
        subprocess.run(["xdg-open", str(Path(target).parent)])


# Common Windows app names mapped to their macOS counterparts, so "open chrome/settings/terminal"
# hit the right Mac app instead of falling through to a website guess.
_MAC_ALIASES = {
    "chrome": "Google Chrome", "google chrome": "Google Chrome", "edge": "Microsoft Edge",
    "microsoft edge": "Microsoft Edge", "firefox": "Firefox", "safari": "Safari", "browser": "Safari",
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "visual studio code": "Visual Studio Code",
    "code": "Visual Studio Code", "terminal": "Terminal", "cmd": "Terminal", "command prompt": "Terminal",
    "powershell": "Terminal", "explorer": "Finder", "file explorer": "Finder", "finder": "Finder",
    "settings": "System Settings", "control panel": "System Settings", "calculator": "Calculator",
    "calc": "Calculator", "notepad": "TextEdit", "textedit": "TextEdit", "paint": "Preview",
    "word": "Microsoft Word", "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook", "spotify": "Spotify", "task manager": "Activity Monitor",
    "snipping tool": "Screenshot", "notes": "Notes", "calendar": "Calendar", "mail": "Mail",
    "music": "Music", "photos": "Photos", "messages": "Messages", "maps": "Maps",
}
_MAC_APP_DIRS = ("/Applications", "/System/Applications", "/System/Applications/Utilities",
                 str(Path.home() / "Applications"))


def _mac_app_name(name: str) -> str:
    return _MAC_ALIASES.get(str(name).strip().lower(), str(name).strip())


def mac_app_exists(name: str) -> bool:
    """Whether a macOS application bundle with this (aliased) name exists, without launching it."""
    if not IS_MAC:
        return False
    app = _mac_app_name(name)
    for base in _MAC_APP_DIRS:
        if (Path(base) / f"{app}.app").exists():
            return True
    try:  # fall back to Launch Services / Spotlight for apps installed elsewhere
        out = subprocess.run(["mdfind", f"kMDItemContentType==com.apple.application-bundle && "
                              f"kMDItemDisplayName=='{app}'c"], capture_output=True, text=True, timeout=4)
        return bool(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_app(name: str) -> bool:
    """Launch a named application. Returns True if it was started."""
    if IS_MAC:
        try:
            return subprocess.run(["open", "-a", _mac_app_name(name)],
                                  stderr=subprocess.DEVNULL).returncode == 0
        except OSError:
            return False
    if IS_LINUX:
        exe = shutil.which(name) or shutil.which(name.lower())
        if exe:
            _popen([exe])
            return True
        return False
    return launch_candidates([name])  # Windows


def launch_candidates(candidates) -> bool:
    """Launch the first workable candidate: a URI scheme (ms-settings:), an existing file/folder path,
    or an executable on PATH. Works on every platform (folders open in Finder/Explorer)."""
    for candidate in candidates:
        try:
            if candidate.endswith(":"):            # a URI scheme (e.g. ms-settings:)
                open_path(candidate)
                return True
            path = Path(candidate).expanduser()
            if path.exists():                      # a concrete file, folder or .app bundle
                open_path(str(path))
                return True
            if IS_WINDOWS:
                target = shutil.which(candidate) or candidate
                os.startfile(target)  # noqa: S606
                return True
            exe = shutil.which(candidate)
            if exe:
                _popen([exe])
                return True
        except OSError:
            continue
    return False


# ---- text to speech ---------------------------------------------------------------------------

def speak_command(text: str) -> list[str]:
    """The argv that speaks `text` aloud through the system voice (for the broker to run)."""
    if IS_WINDOWS:
        escaped = str(text).replace("'", "''")
        script = ("Add-Type -AssemblyName System.Speech; "
                  f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{escaped}')")
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    if IS_MAC:
        return ["say", str(text)]
    return ["spd-say", str(text)]  # Linux (speech-dispatcher); best-effort


def speak(text: str) -> bool:
    try:
        _popen(speak_command(text))
        return True
    except OSError:
        return False


# ---- screenshots ------------------------------------------------------------------------------

_PS_SCREENSHOT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
    "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
    "$g=[System.Drawing.Graphics]::FromImage($bmp);"
    "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
    "$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$bmp.Dispose()"
)


def screenshot(target: Path) -> tuple[bool, str]:
    """Capture the whole screen to `target` (PNG). Returns (ok, error_message)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if IS_WINDOWS:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                _PS_SCREENSHOT.format(path=target)],
                               creationflags=NO_WINDOW, timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        elif IS_MAC:
            # -x: no capture sound. Needs Screen Recording permission (System Settings > Privacy).
            r = subprocess.run(["screencapture", "-x", str(target)], timeout=30,
                               stderr=subprocess.PIPE, text=True)
        else:
            r = subprocess.run(["import", "-window", "root", str(target)], timeout=30,
                               stderr=subprocess.PIPE, text=True)  # ImageMagick
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if r.returncode != 0 or not target.exists():
        err = (r.stderr or "").strip()[:200] or "unknown error"
        if IS_MAC and not target.exists():
            err += " (grant Screen Recording permission to the app in System Settings > Privacy & Security)"
        return False, err
    return True, ""


# ---- desktop notification / popup -------------------------------------------------------------

def notify(message: str, title: str) -> tuple[bool, str]:
    """Show a popup/notification. Returns (ok, error)."""
    message, title = str(message), str(title)
    try:
        if IS_WINDOWS:
            import re
            q = lambda t: re.sub(r"\s*\n\s*", " ", t).replace("'", "''")
            script = ("Add-Type -AssemblyName System.Windows.Forms;"
                      f"[System.Windows.Forms.MessageBox]::Show('{q(message)}','{q(title)}','OK','Information') | Out-Null")
            _popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
        elif IS_MAC:
            def q(t):
                return t.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
            script = f'display notification "{q(message)}" with title "{q(title)}"'
            _popen(["osascript", "-e", script])
        else:
            _popen(["notify-send", title, message])
    except OSError as exc:
        return False, str(exc)
    return True, ""


# ---- mouse-wheel scrolling (hand-gesture control) ---------------------------------------------

def scroll(amount: int, up: bool) -> None:
    """Scroll the window under the pointer by `amount` wheel notches, up or down. Best-effort."""
    delta = int(amount if up else -amount)
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta, 0)  # MOUSEEVENTF_WHEEL
        except Exception:
            pass
    elif IS_MAC:
        try:
            import Quartz  # pyobjc-framework-Quartz
            lines = max(1, abs(amount) // 100) * (1 if up else -1)
            ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        except Exception:
            # fallback to the `cliclick` tool if it's installed (brew install cliclick)
            try:
                subprocess.run(["cliclick", "w:" + ("u" if up else "d")], stderr=subprocess.DEVNULL)
            except OSError:
                pass


def scroll_pixels(pixels: int, up: bool) -> None:
    """Smooth scroll by roughly `pixels` screen pixels (hand control calls this every frame while a
    scroll gesture is held). macOS posts a pixel-unit wheel event; Windows a proportional wheel delta
    (120 = one notch = ~3 lines ~= 100px)."""
    pixels = max(1, int(abs(pixels)))
    if IS_MAC:
        try:
            import Quartz
            ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 1,
                                                      pixels if up else -pixels)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            return
        except Exception:
            pass
        scroll(100, up)
    elif IS_WINDOWS:
        scroll(max(1, int(pixels * 1.2)), up)
    else:
        scroll(100, up)


# ---- keyboard (shortcuts, typing) ---------------------------------------------------------------

# macOS virtual key codes for the keys we send.
_MAC_VK = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
           "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
           "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
           "u": 32, "[": 33, "i": 34, "p": 35, "return": 36, "enter": 36, "l": 37, "j": 38, "'": 39,
           "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48,
           "space": 49, "`": 50, "backspace": 51, "delete": 51, "escape": 53, "esc": 53,
           "left": 123, "right": 124, "down": 125, "up": 126, "f1": 122, "f2": 120, "f3": 99, "f4": 118}
_MAC_FLAG = {"cmd": 1 << 20, "shift": 1 << 17, "alt": 1 << 19, "ctrl": 1 << 18, "fn": 1 << 23}
# Windows virtual key codes.
_WIN_VK = {"tab": 0x09, "return": 0x0D, "enter": 0x0D, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
           "escape": 0x1B, "esc": 0x1B, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
           "cmd": 0x5B, "ctrl": 0x11, "shift": 0x10, "alt": 0x12}


def key_press(key: str, modifiers=()) -> bool:
    """Press a key with modifiers, e.g. key_press('t', ['cmd']). Best-effort; macOS needs Accessibility."""
    key = str(key).lower()
    mods = [str(m).lower() for m in modifiers]
    try:
        if IS_MAC:
            import Quartz
            code = _MAC_VK.get(key)
            if code is None:
                return False
            flags = 0
            for m in mods:
                flags |= _MAC_FLAG.get(m, 0)
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
                Quartz.CGEventSetFlags(ev, flags)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                time.sleep(0.005)
            return True
        if IS_WINDOWS:
            import ctypes
            u = ctypes.windll.user32
            codes = [_WIN_VK.get(m, 0) for m in mods]
            main = _WIN_VK.get(key) or (ord(key.upper()) if len(key) == 1 else None)
            if not main:
                return False
            for c in codes:
                u.keybd_event(c, 0, 0, 0)
            u.keybd_event(main, 0, 0, 0)
            u.keybd_event(main, 0, 2, 0)
            for c in reversed(codes):
                u.keybd_event(c, 0, 2, 0)
            return True
    except Exception:
        pass
    return False


def type_text(text: str) -> bool:
    """Type text into whatever app is focused (as if you typed it)."""
    text = str(text)
    if not text:
        return True
    try:
        if IS_MAC:
            import Quartz
            for chunk in (text[i:i + 20] for i in range(0, len(text), 20)):
                for down in (True, False):
                    ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                    Quartz.CGEventKeyboardSetUnicodeString(ev, len(chunk), chunk)
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                    time.sleep(0.004)
            return True
        if IS_WINDOWS:
            import ctypes
            u = ctypes.windll.user32
            for ch in text:                       # KEYEVENTF_UNICODE
                u.keybd_event(0, 0, 0x4, ord(ch))
                u.keybd_event(0, 0, 0x4 | 0x2, ord(ch))
            return True
    except Exception:
        pass
    return False


# ---- clipboard ---------------------------------------------------------------------------------

def clipboard_get() -> str:
    try:
        if IS_MAC:
            return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
        if IS_WINDOWS:
            return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                                  capture_output=True, text=True, timeout=10,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def clipboard_set(text: str) -> bool:
    try:
        if IS_MAC:
            subprocess.run(["pbcopy"], input=text, text=True, timeout=5, check=True)
        elif IS_WINDOWS:
            subprocess.run(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"], input=text,
                           text=True, timeout=10, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, timeout=5, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# ---- file search ------------------------------------------------------------------------------

_SKIP_DIRS = {"Library", ".Trash", "node_modules", ".git", "__pycache__", "AppData", ".cache", "venv", ".venv",
              "site-packages", "chrome-debug", "chrome_user_data"}


def find_files(names=(), content=(), exts=(), days: int | None = None, limit: int = 10) -> list[Path]:
    """Files in your home folder matching ALL of: name words, content words (macOS Spotlight indexes
    text inside documents), extensions and 'modified in the last N days'. Newest first."""
    names = [n for n in names if n]
    content = [c for c in content if c]
    exts = [e.lower().lstrip(".") for e in exts if e]
    home = Path.home()
    found: list[Path] = []
    if IS_MAC and shutil.which("mdfind"):
        def esc(t):
            return str(t).replace("\\", "").replace('"', "")
        clauses = [f'kMDItemFSName == "*{esc(n)}*"cd' for n in names]
        clauses += [f'kMDItemTextContent == "*{esc(c)}*"cd' for c in content]
        if exts:
            clauses.append("(" + " || ".join(f'kMDItemFSName == "*.{esc(e)}"c' for e in exts) + ")")
        if days:
            clauses.append(f"kMDItemFSContentChangeDate >= $time.today(-{int(days)})")
        if not clauses:
            return []
        try:
            out = subprocess.run(["mdfind", "-onlyin", str(home), " && ".join(clauses)], capture_output=True,
                                 text=True, timeout=20).stdout
            found = [Path(line) for line in out.splitlines() if line.strip()]
        except (OSError, subprocess.TimeoutExpired):
            found = []
        found = [p for p in found if p.is_file() and not (set(p.relative_to(home).parts[:-1]) & _SKIP_DIRS)
                 and not any(part.startswith(".") for part in p.relative_to(home).parts)]
    else:
        cutoff = time.time() - days * 86400 if days else 0
        for base in ("Desktop", "Documents", "Downloads", "Pictures", "OneDrive"):
            root = home / base
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                for fn in filenames:
                    low = fn.lower()
                    if names and not all(n.lower() in low for n in names):
                        continue
                    if exts and low.rsplit(".", 1)[-1] not in exts:
                        continue
                    p = Path(dirpath) / fn
                    try:
                        if cutoff and p.stat().st_mtime < cutoff:
                            continue
                    except OSError:
                        continue
                    found.append(p)
                if len(found) > 500:
                    break
    try:
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass
    return found[:limit]


def reveal(path) -> None:
    """Show a file selected in Finder / Explorer."""
    try:
        if IS_MAC:
            _popen(["open", "-R", str(path)])
        elif IS_WINDOWS:
            _popen(["explorer", "/select,", str(path)])
        else:
            _popen(["xdg-open", str(Path(path).parent)])
    except OSError:
        pass


# ---- processes (performance doctor) ------------------------------------------------------------

def top_apps(limit: int = 6) -> list[dict]:
    """Running apps grouped by name (Chrome's dozens of helpers count as 'Google Chrome'), with total
    CPU % and memory MB, heaviest first."""
    rows = []
    try:
        if IS_WINDOWS:
            import csv
            import io
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=15,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            for r in csv.reader(io.StringIO(out)):
                if len(r) >= 5:
                    mem = float(re.sub(r"[^\d]", "", r[4]) or 0) / 1024
                    rows.append((r[0].rsplit(".", 1)[0], 0.0, mem))
        else:
            out = subprocess.run(["ps", "-Ao", "pcpu=,rss=,comm="], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                parts = line.split(None, 2)
                if len(parts) < 3:
                    continue
                cpu, rss, comm = float(parts[0]), float(parts[1]) / 1024, parts[2]
                m = re.search(r"/([^/]+)\.app/", comm)
                name = m.group(1) if m else Path(comm).name
                name = re.sub(r"\s+Helper.*$", "", name)
                rows.append((name, cpu, rss))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    apps: dict = {}
    for name, cpu, mem in rows:
        a = apps.setdefault(name, {"name": name, "cpu": 0.0, "mem_mb": 0.0, "procs": 0})
        a["cpu"] += cpu
        a["mem_mb"] += mem
        a["procs"] += 1
    return sorted(apps.values(), key=lambda a: (a["cpu"], a["mem_mb"]), reverse=True)[:limit] if rows else []


def quit_app(name: str) -> tuple[bool, str]:
    """Ask an app to quit normally (it can still prompt you to save)."""
    try:
        if IS_MAC:
            safe = name.replace('"', "")
            r = subprocess.run(["osascript", "-e", f'tell application "{safe}" to quit'], capture_output=True,
                               text=True, timeout=20)
            return r.returncode == 0, (r.stderr or "").strip()
        if IS_WINDOWS:
            r = subprocess.run(["taskkill", "/IM", f"{name}.exe"], capture_output=True, text=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return r.returncode == 0, (r.stderr or "").strip()
        r = subprocess.run(["pkill", "-f", name], capture_output=True, text=True, timeout=10)
        return r.returncode == 0, ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


# ---- battery -----------------------------------------------------------------------------------

def battery() -> tuple[int, bool] | None:
    """(percent, plugged_in) or None when there's no battery / it can't be read."""
    try:
        if IS_MAC:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"(\d+)%", out)
            if not m:
                return None
            plugged = "AC Power" in out or bool(re.search(r"\bcharging\b|\bcharged\b", out, re.I)) \
                and "discharging" not in out.lower()
            return int(m.group(1)), plugged
        if IS_WINDOWS:
            import ctypes
            from ctypes import wintypes

            class _PS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
            st = _PS()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)) or st.BatteryLifePercent == 255:
                return None
            return int(st.BatteryLifePercent), st.ACLineStatus == 1
        base = Path("/sys/class/power_supply/BAT0")
        if base.exists():
            return int((base / "capacity").read_text().strip()), \
                (base / "status").read_text().strip().lower() != "discharging"
    except Exception:
        pass
    return None


# ---- mouse pointer (hand control) --------------------------------------------------------------

def screen_size() -> tuple[int, int]:
    """Main display size in the units mouse events use (points on macOS, pixels on Windows)."""
    try:
        if IS_MAC:
            import Quartz
            b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
            return int(b.size.width), int(b.size.height)
        if IS_WINDOWS:
            import ctypes
            u = ctypes.windll.user32
            return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:
        pass
    return 1440, 900


def mouse_position() -> tuple[int, int] | None:
    try:
        if IS_MAC:
            import Quartz
            p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            return int(p.x), int(p.y)
        if IS_WINDOWS:
            import ctypes

            class _P(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = _P()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y
    except Exception:
        pass
    return None


def can_control_mouse(request: bool = False) -> bool | None:
    """macOS: may this process post mouse/scroll events (Accessibility)? request=True shows the system
    prompt. None when unknown/not applicable."""
    if not IS_MAC:
        return True if IS_WINDOWS else None
    try:
        import Quartz
        if Quartz.CGPreflightPostEventAccess():
            return True
        if request:
            Quartz.CGRequestPostEventAccess()
        return False
    except Exception:
        return None


def mouse_event(kind: str, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
    """Post a real OS mouse event at screen (x, y). kind: move | down | up | drag. Best-effort (macOS
    needs Accessibility permission for the process posting events)."""
    x, y = int(x), int(y)
    try:
        if IS_MAC:
            import Quartz
            right = button == "right"
            types = {"move": Quartz.kCGEventMouseMoved,
                     "down": Quartz.kCGEventRightMouseDown if right else Quartz.kCGEventLeftMouseDown,
                     "up": Quartz.kCGEventRightMouseUp if right else Quartz.kCGEventLeftMouseUp,
                     "drag": Quartz.kCGEventRightMouseDragged if right else Quartz.kCGEventLeftMouseDragged}
            ev = Quartz.CGEventCreateMouseEvent(None, types[kind], (x, y),
                                                Quartz.kCGMouseButtonRight if right else Quartz.kCGMouseButtonLeft)
            if kind in ("down", "up"):
                Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, max(1, int(clicks)))
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        elif IS_WINDOWS:
            import ctypes
            u = ctypes.windll.user32
            u.SetCursorPos(x, y)
            flags = {("down", "left"): 0x2, ("up", "left"): 0x4, ("down", "right"): 0x8, ("up", "right"): 0x10}
            if (kind, button) in flags:
                u.mouse_event(flags[(kind, button)], 0, 0, 0, 0)
    except Exception:
        pass


# ---- camera (macOS TCC permission + the right OpenCV backend) ---------------------------------

def camera_backend(cv2):
    """The OpenCV capture backend to use per platform - never Windows DirectShow on macOS."""
    if IS_WINDOWS:
        return getattr(cv2, "CAP_DSHOW", 0)
    if IS_MAC:
        return getattr(cv2, "CAP_AVFOUNDATION", 0)
    return 0


def request_camera_access():
    """macOS only: make sure camera permission is granted, triggering the system prompt when it's still
    undetermined (OpenCV never triggers it, so the capture just fails silently otherwise). Returns True
    if authorized, False if denied/restricted, None if it couldn't check (caller should let OpenCV try)."""
    if not IS_MAC:
        return True
    try:
        import AVFoundation
    except Exception:
        return None  # the AVFoundation pyobjc framework isn't installed; let OpenCV attempt anyway
    try:
        import threading
        media = AVFoundation.AVMediaTypeVideo
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(media)
        # AVAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied, 3 authorized
        if status == 3:
            return True
        if status in (1, 2):
            return False
        done = threading.Event()
        result = {"granted": False}

        def handler(granted):
            result["granted"] = bool(granted)
            done.set()

        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(media, handler)
        done.wait(120)  # wait for the user to click Allow / Deny
        return result["granted"]
    except Exception:
        return None


# ---- Chrome (for DevTools browser control) ----------------------------------------------------

def chrome_path() -> str | None:
    """The Chrome executable for DevTools control, or None if not found."""
    if IS_MAC:
        for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")):
            if Path(p).exists():
                return p
        return None
    if IS_LINUX:
        return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    # Windows
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if found:
        return found
    try:
        import winreg
        subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, subkey) as handle:
                    value = winreg.QueryValueEx(handle, "")[0]
                    if value and Path(value).exists():
                        return value
            except OSError:
                continue
    except ImportError:
        pass
    for guess in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if Path(guess).exists():
            return guess
    return None


# ---- running a Python program the user wrote, in a visible window -----------------------------

_HOLD_CONSOLE = (
    "import runpy,sys,traceback\n"
    "f=sys.argv[1]\n"
    "rc=0\n"
    "try:\n"
    "    runpy.run_path(f, run_name='__main__')\n"
    "except SystemExit as e:\n"
    "    rc=e.code if isinstance(e.code,int) else (0 if e.code is None else 1)\n"
    "except BaseException:\n"
    "    traceback.print_exc(); rc=1\n"
    "    print('\\n--- the program crashed (the error is above) ---')\n"
    "print('\\n[Finished with exit code %s. Press Enter to close this window...]' % rc)\n"
    "try:\n"
    "    input()\n"
    "except EOFError:\n"
    "    pass\n"
)


def is_gui_python(target: Path) -> bool:
    """A .pyw is always GUI. Elsewhere the caller decides; this is just the extension rule."""
    return Path(target).suffix.lower() == ".pyw"


def launch_python(target: Path, gui: bool) -> tuple[bool, str]:
    """Run a Python file: a GUI app windowless/detached, or a console script in a terminal window that
    STAYS OPEN (shows output or the full traceback, then waits for Enter). Returns (ok, error)."""
    target = Path(target)
    if not target.is_file():
        return False, f"There's nothing at {target}."
    exe = sys.executable
    try:
        if gui:
            if IS_WINDOWS:
                pyw = Path(exe).with_name("pythonw.exe")
                exe = str(pyw) if pyw.exists() else exe
                subprocess.Popen([exe, str(target)], cwd=str(target.parent),
                                 creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True)
            else:  # macOS / Linux: a GUI Tk/Qt app just needs a detached process; the window appears
                subprocess.Popen([exe, str(target)], cwd=str(target.parent),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True, start_new_session=not IS_WINDOWS)
            return True, ""
        # console script in a window that stays open
        if IS_WINDOWS:
            subprocess.Popen([exe, "-c", _HOLD_CONSOLE, str(target)], cwd=str(target.parent),
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), close_fds=True)
        elif IS_MAC:
            # Ask Terminal.app to run it; the wrapper's input() keeps the window open afterwards.
            inner = f'cd {_sh_quote(str(target.parent))} && {_sh_quote(exe)} -c {_sh_quote(_HOLD_CONSOLE)} {_sh_quote(str(target))}'
            script = f'tell application "Terminal" to do script "{inner.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"\ntell application "Terminal" to activate'
            subprocess.Popen(["osascript", "-e", script], close_fds=True)
        else:
            term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("xterm")
            if not term:
                return False, "no terminal emulator found to run the program in a window"
            subprocess.Popen([term, "-e", exe, "-c", _HOLD_CONSOLE, str(target)],
                             cwd=str(target.parent), close_fds=True)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def _sh_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


# ---- memory status (for `jarvis doctor`) ------------------------------------------------------

def memory_gb() -> tuple[float, float, int] | None:
    """(total_gb, available_gb, load_percent) or None if unavailable."""
    try:
        if IS_WINDOWS:
            import ctypes
            from ctypes import wintypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MS()
            st.dwLength = ctypes.sizeof(st)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            gb = 1024 ** 3
            return st.ullTotalPhys / gb, st.ullAvailPhys / gb, int(st.dwMemoryLoad)
        if IS_MAC:
            gb = 1024 ** 3
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip())
            page = int(subprocess.run(["sysctl", "-n", "hw.pagesize"], capture_output=True, text=True).stdout.strip())
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
            free_pages = 0
            for key in ("Pages free", "Pages inactive", "Pages speculative"):
                for line in vm.splitlines():
                    if line.startswith(key + ":"):
                        free_pages += int(line.split(":")[1].strip().rstrip("."))
            avail = free_pages * page
            load = int(round((1 - avail / total) * 100)) if total else 0
            return total / gb, avail / gb, load
        # Linux
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            info[k] = int(v.strip().split()[0]) * 1024
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        gb = 1024 ** 3
        load = int(round((1 - avail / total) * 100)) if total else 0
        return total / gb, avail / gb, load
    except Exception:
        return None
