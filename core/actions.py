"""The action broker: the single, controlled path skills use to affect the computer.

Skills never call subprocess, webbrowser or os.startfile themselves. They call the broker in
their context:

    context["actions"].open_app("notepad")
    context["actions"].open_url("https://example.com")
    context["actions"].screenshot()
    context["actions"].read_file(path) / write_file(path, text) / list_dir(path)
    context["actions"].run_command(["ipconfig"])

Two modes:
  dry_run   used during sandbox verification. Nothing touches the system; each call is recorded
            and a realistic placeholder is returned, so an action-skill can be verified safely.
  live      each call is gated. "allow" runs it, "deny" refuses, "ask" calls the confirmer
            (the panel's Allow/Always/Deny buttons, or a terminal prompt) and waits.

This module imports only the standard library and nothing else from JARVIS, so the sandbox
harness can load it by path and give verified skills the same (dry-run) broker.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_MAX_READ = 200_000

APPS = {
    "notepad": ("Notepad", ["notepad.exe"]),
    "calculator": ("Calculator", ["calc.exe"]),
    "calc": ("Calculator", ["calc.exe"]),
    "paint": ("Paint", ["mspaint.exe"]),
    "file explorer": ("File Explorer", ["explorer.exe"]),
    "explorer": ("File Explorer", ["explorer.exe"]),
    "task manager": ("Task Manager", ["taskmgr.exe"]),
    "command prompt": ("Command Prompt", ["cmd.exe"]),
    "cmd": ("Command Prompt", ["cmd.exe"]),
    "powershell": ("PowerShell", ["powershell.exe"]),
    "terminal": ("Terminal", ["wt.exe", "powershell.exe"]),
    "settings": ("Settings", ["ms-settings:"]),
    "control panel": ("Control Panel", ["control.exe"]),
    "snipping tool": ("Snipping Tool", ["snippingtool.exe"]),
    "chrome": ("Google Chrome", ["chrome.exe"]),
    "google chrome": ("Google Chrome", ["chrome.exe"]),
    "edge": ("Microsoft Edge", ["microsoft-edge:"]),
    "microsoft edge": ("Microsoft Edge", ["microsoft-edge:"]),
    "firefox": ("Firefox", ["firefox.exe"]),
    "word": ("Word", ["winword.exe"]),
    "excel": ("Excel", ["excel.exe"]),
    "powerpoint": ("PowerPoint", ["powerpnt.exe"]),
    "outlook": ("Outlook", ["outlook.exe"]),
    "spotify": ("Spotify", ["spotify.exe"]),
    "vs code": ("VS Code", ["code.cmd", "code"]),
    "vscode": ("VS Code", ["code.cmd", "code"]),
    "visual studio code": ("VS Code", ["code.cmd", "code"]),
}
FOLDERS = {name: name.capitalize() for name in ("downloads", "documents", "desktop", "pictures", "music", "videos")}
_URL = re.compile(r"(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?", re.IGNORECASE)
# A safe pip package spec: a name, optionally with extras/version. No URLs, paths, flags or shell chars.
_VALID_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}(?:\[[A-Za-z0-9,_-]+\])?(?:[=<>!~]=?[\w.*+-]+)?$")

# Named web apps: "open google docs" should open the site, not look for an installed program.
WEB_APPS = {
    "google": "https://www.google.com", "google docs": "https://docs.google.com",
    "docs": "https://docs.google.com", "google sheets": "https://sheets.google.com",
    "sheets": "https://sheets.google.com", "google slides": "https://slides.google.com",
    "slides": "https://slides.google.com", "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com", "gmail": "https://mail.google.com",
    "google mail": "https://mail.google.com", "google calendar": "https://calendar.google.com",
    "calendar": "https://calendar.google.com", "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com", "google translate": "https://translate.google.com",
    "translate": "https://translate.google.com", "google photos": "https://photos.google.com",
    "youtube": "https://www.youtube.com", "gemini": "https://gemini.google.com",
    "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai", "github": "https://github.com",
    "notion": "https://www.notion.so", "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com", "spotify web": "https://open.spotify.com",
    "reddit": "https://www.reddit.com", "twitter": "https://x.com", "x": "https://x.com",
    "linkedin": "https://www.linkedin.com", "outlook": "https://outlook.live.com",
    "amazon": "https://www.amazon.com", "netflix": "https://www.netflix.com",
    "stack overflow": "https://stackoverflow.com", "stackoverflow": "https://stackoverflow.com",
}
# "on/in (my) (existing) chrome/edge/firefox/browser" is noise for what to open. Strip just that phrase,
# not whatever follows it (so a later clause like "... and find X" is preserved for the planner).
_BROWSER_SUFFIX = re.compile(r"\s+(?:on|in|using|with|via)\s+(?:my\s+|the\s+|an?\s+|existing\s+)*"
                             r"(?:google\s+chrome|microsoft\s+edge|chrome|edge|firefox|browser)\b", re.IGNORECASE)


def strip_browser_suffix(text: str) -> str:
    return _BROWSER_SUFFIX.sub("", str(text)).strip()


def _make_installed_packages_importable() -> None:
    """Put the user site-packages on sys.path so a just-installed package imports without a restart.

    A long-running process fixes sys.path at startup; the first --user install can create the user-site
    folder afterwards, so it wouldn't otherwise be importable until the process restarts.
    """
    try:
        import importlib
        import site

        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path and os.path.isdir(user_site):
            site.addsitedir(user_site)
        importlib.invalidate_caches()
    except Exception:
        pass


# Extensions that mean "a local file", so "open frenchjokes.py" is a FILE, not the website frenchjokes.py.
LOCAL_FILE_EXT = {
    "py", "pyw", "txt", "md", "rst", "log", "csv", "tsv", "json", "xml", "yaml", "yml", "ini", "cfg", "toml",
    "html", "htm", "css", "js", "ts", "jsx", "tsx", "c", "cpp", "h", "hpp", "cs", "java", "rb", "go", "rs",
    "php", "sql", "sh", "bat", "cmd", "ps1", "png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "ico", "pdf",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "7z", "tar", "gz", "exe", "msi", "lnk",
    "mp3", "mp4", "wav", "avi", "mov", "mkv",
}


# Folders that organize_dir sorts files into, keyed by extension.
_FILE_CATEGORIES = {
    "Images": {"png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "ico", "tiff", "heic"},
    "Documents": {"pdf", "doc", "docx", "txt", "md", "rtf", "odt", "csv", "xls", "xlsx", "ppt", "pptx", "epub"},
    "Videos": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm"},
    "Audio": {"mp3", "wav", "flac", "aac", "ogg", "m4a"},
    "Archives": {"zip", "rar", "7z", "tar", "gz", "iso"},
    "Installers": {"exe", "msi"},
    "Code": {"py", "js", "ts", "html", "css", "json", "c", "cpp", "java", "sh", "ps1", "bat"},
}


def _category_for(suffix: str) -> str:
    ext = str(suffix).lower().lstrip(".")
    for name, exts in _FILE_CATEGORIES.items():
        if ext in exts:
            return name
    return "Other"


def has_local_file_ext(name: str) -> bool:
    stem = str(name).strip().strip("\"'").split("/")[-1].split("\\")[-1]
    ext = stem.rsplit(".", 1)[-1].lower() if "." in stem else ""
    return ext in LOCAL_FILE_EXT


def web_url_for(name: str) -> str | None:
    """A URL for a named web app or a bare domain, else None."""
    cleaned = strip_browser_suffix(name).strip().strip(".!?").lower()
    if cleaned in WEB_APPS:
        return WEB_APPS[cleaned]
    # A bare local filename ("frenchjokes.py") is a file, not a website - don't URL-ify it.
    if "/" not in cleaned and not cleaned.startswith("http") and has_local_file_ext(cleaned):
        return None
    if _URL.fullmatch(cleaned):
        return cleaned if cleaned.startswith("http") else f"https://{cleaned}"
    return None


@dataclass
class ActionRequest:
    capability: str
    summary: str       # human sentence: "Open Google Chrome"
    details: str = ""  # the path, url or command line


class Blocked(RuntimeError):
    """The action was refused: denied by settings, or the user declined. Not a bug."""


def _normalize_decision(value) -> str:
    if value is True:
        return "once"
    if value in (False, None):
        return "deny"
    text = str(value).strip().lower()
    if text in ("once", "yes", "y", "allow", "ok"):
        return "once"
    if text in ("always", "a", "all"):
        return "always"
    return "deny"


class ActionBroker:
    def __init__(self, *, dry_run=False, permissions=None, confirm=None, emit=None, pictures_dir=None,
                 state_dir=None, browser=None):
        self.dry_run = dry_run
        self.permissions = permissions
        self.confirm = confirm           # callable(ActionRequest) -> "once" | "always" | "deny" | bool
        self.emit = emit                 # callable(stage, message) for progress lines
        self.pictures_dir = Path(pictures_dir) if pictures_dir else (Path.home() / "Pictures")
        self.state_dir = Path(state_dir) if state_dir else None  # where "last screenshot" is remembered
        self.browser = browser           # which browser to open URLs in ("chrome"/"edge"/path/None=OS default)
        self.performed: list[ActionRequest] = []
        self.simulated: list[ActionRequest] = []
        self.blocked: list[ActionRequest] = []
        self._lnk_index: dict | None = None

    # ---- gating -------------------------------------------------------------------------------

    def _decide(self, req: ActionRequest) -> None:
        state = self.permissions.state(req.capability) if self.permissions else "ask"
        if state == "allow":
            return
        if state == "deny":
            self.blocked.append(req)
            raise Blocked(f"{req.summary}: blocked by your settings (allow it with: jarvis permissions {req.capability} allow)")
        decision = _normalize_decision(self.confirm(req) if self.confirm else None)
        if decision == "always":
            if self.permissions:
                self.permissions.set(req.capability, "allow")
            return
        if decision == "once":
            return
        self.blocked.append(req)
        raise Blocked(f"{req.summary}: you didn't approve it")

    def _gated(self, req: ActionRequest, produce, dry_value):
        if self.dry_run:
            self.simulated.append(req)
            return dry_value
        self._decide(req)
        result = produce()
        self.performed.append(req)
        if self.emit:
            self.emit("action", req.summary)
        return result

    # ---- open ---------------------------------------------------------------------------------

    def _launch(self, candidates) -> bool:
        for candidate in candidates:
            target = candidate if candidate.endswith(":") else (shutil.which(candidate) or candidate)
            try:
                os.startfile(target)  # noqa: S606 - the documented Windows way to open by association
                return True
            except OSError:
                continue
        return False

    def _app_paths_exe(self, key: str) -> str | None:
        """The registered path for <key>.exe, from the Windows App Paths registry."""
        try:
            import winreg
        except ImportError:
            return None
        subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{key}.exe"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, subkey) as handle:
                    value = winreg.QueryValueEx(handle, "")[0]
                    if value and Path(value).exists():
                        return value
            except OSError:
                continue
        return None

    def _start_menu_shortcut(self, key: str) -> str | None:
        """A Start Menu .lnk whose name matches, so installed apps (Antigravity, Spotify...) open."""
        if self._lnk_index is None:
            index: dict[str, str] = {}
            roots = [os.environ.get("APPDATA", ""), os.environ.get("PROGRAMDATA", "")]
            for base in roots:
                start = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" if base else None
                if start and start.is_dir():
                    for lnk in start.rglob("*.lnk"):
                        index.setdefault(lnk.stem.lower(), str(lnk))
            self._lnk_index = index
        if key in self._lnk_index:
            return self._lnk_index[key]
        matches = [path for stem, path in self._lnk_index.items() if key in stem]
        matches.sort(key=lambda p: len(Path(p).stem))  # prefer the closest name
        return matches[0] if matches else None

    def _resolve_app(self, name: str):
        key = re.sub(r"\s+(?:app|application|program|browser)$", "", str(name).strip().lower())
        if key in APPS:
            return APPS[key]
        if key in FOLDERS:
            return f"{FOLDERS[key]} folder", [str(Path.home() / FOLDERS[key])]
        executable = shutil.which(key) or shutil.which(f"{key}.exe")
        if executable:
            return key, [executable]
        if self.dry_run:
            return None  # keep verification cheap and off the real filesystem
        registered = self._app_paths_exe(key)
        if registered:
            return key, [registered]
        shortcut = self._start_menu_shortcut(key)
        if shortcut:
            return Path(shortcut).stem, [shortcut]
        return None

    def open_app(self, name: str):
        """Open a known app, common folder, PATH program, or a named web app/site. None if unknown."""
        name = strip_browser_suffix(name)  # "google docs on my chrome" -> "google docs"
        resolved = self._resolve_app(name)
        if not resolved:
            url = web_url_for(name)  # google docs, gmail, youtube, a bare domain...
            return self.open_url(url) if url else None
        label, candidates = resolved
        req = ActionRequest("open", f"Open {label}", details=candidates[0])
        return self._gated(
            req,
            lambda: f"Opening {label}." if self._launch(candidates) else f"I couldn't find {label} on this PC.",
            f"Would open {label}.",
        )

    def _browser_exe(self) -> str | None:
        """The executable for the preferred browser, so URLs open there (and reuse its window) instead of
        the OS default. None -> use the OS default (webbrowser)."""
        pref = str(self.browser or "").strip().lower()
        if pref in ("", "default", "system"):
            return None
        if pref.endswith(".exe") or "\\" in pref or "/" in pref:   # an explicit path
            return pref if Path(pref).exists() else None
        key = {"chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "msedge": "msedge",
               "microsoft edge": "msedge", "firefox": "firefox", "brave": "brave"}.get(pref, pref)
        return shutil.which(f"{key}.exe") or shutil.which(key) or self._app_paths_exe(key)

    def open_url(self, url: str):
        url = url.strip()
        if not _URL.fullmatch(url):
            return f"That doesn't look like a web address: {url!r}"
        full = url if url.lower().startswith("http") else f"https://{url}"
        req = ActionRequest("open", f"Open {full} in your browser", details=full)

        def do():
            exe = self._browser_exe()
            if exe:                       # launch the chosen browser; if it's already open, this is a new tab
                try:
                    subprocess.Popen([exe, full], creationflags=_NO_WINDOW, close_fds=True)
                    return f"Opening {full}"
                except OSError:
                    pass
            webbrowser.open(full, new=2)  # fall back to the OS default browser
            return f"Opening {full}"

        return self._gated(req, do, f"Would open {full} in your browser.")

    def open_browser(self, url: str = "https://www.google.com/?newtab"):
        """Open the default browser, in a new tab."""
        return self.open_url(url)

    def open_path(self, path: str):
        target = Path(path).expanduser()
        req = ActionRequest("open", f"Open {target}", details=str(target))

        def do():
            if not target.exists():
                return f"There's nothing at {target}."
            os.startfile(str(target))
            return f"Opening {target}."

        return self._gated(req, do, f"Would open {target}.")

    def reveal(self, path: str):
        """Open the folder containing a file, with the file highlighted (Explorer /select)."""
        target = Path(path).expanduser()
        req = ActionRequest("open", f"Show {target.name} in its folder", details=str(target))

        def do():
            if not target.exists():
                return f"There's nothing at {target}."
            subprocess.run(["explorer", f"/select,{target}"], creationflags=_NO_WINDOW)  # explorer returns non-zero on success
            return f"Showing {target.name} in {target.parent}."

        return self._gated(req, do, f"Would show {target} in its folder.")

    # ---- screen -------------------------------------------------------------------------------

    def _last_screenshot_file(self) -> Path | None:
        return (self.state_dir / "last_screenshot.txt") if self.state_dir else None

    def _remember_screenshot(self, path: Path) -> None:
        record = self._last_screenshot_file()
        if record is not None:
            try:
                record.write_text(str(path), encoding="utf-8")
            except OSError:
                pass

    def last_screenshot(self) -> Path | None:
        record = self._last_screenshot_file()
        try:
            saved = Path(record.read_text(encoding="utf-8").strip()) if record and record.is_file() else None
        except OSError:
            saved = None
        if saved and saved.is_file():
            return saved
        shots = sorted(self.pictures_dir.glob("JARVIS-*.png"), key=lambda p: p.stat().st_mtime) if self.pictures_dir.is_dir() else []
        return shots[-1] if shots else None

    def screenshot(self, path: str | None = None):
        target = Path(path).expanduser() if path else self.pictures_dir / f"JARVIS-{time.strftime('%Y%m%d-%H%M%S')}.png"
        req = ActionRequest("screen", "Capture the screen", details=str(target))

        def do():
            target.parent.mkdir(parents=True, exist_ok=True)
            script = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
                "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                "$g=[System.Drawing.Graphics]::FromImage($bmp);"
                "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
                f"$bmp.Save('{target}',[System.Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$bmp.Dispose()"
            )
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                    creationflags=_NO_WINDOW, timeout=30,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0 or not target.exists():
                return f"Screenshot failed: {(result.stderr or '').strip()[:200] or 'unknown error'}"
            self._remember_screenshot(target)
            return f"Screenshot saved to {target}"

        return self._gated(req, do, f"Would capture the screen to {target}.")

    # ---- camera -------------------------------------------------------------------------------

    def capture_camera(self, path: str | None = None) -> str:
        """Grab a single frame from the webcam and save it. Gated by 'camera' (never auto-allowed)."""
        target = Path(path).expanduser() if path else self.pictures_dir / f"JARVIS-cam-{time.strftime('%Y%m%d-%H%M%S')}.png"
        req = ActionRequest("camera", "Take a photo with the webcam", details=str(target))

        def do():
            _make_installed_packages_importable()
            try:
                import cv2  # noqa: F401
            except ImportError:
                self.install_package("opencv-python-headless")  # gated by 'packages'
                _make_installed_packages_importable()
                try:
                    import cv2
                except ImportError:
                    return "I need the 'opencv-python-headless' package for the camera and couldn't load it."
            cap = cv2.VideoCapture(0, getattr(cv2, "CAP_DSHOW", 0))  # DirectShow: opens fast on Windows
            if not cap or not cap.isOpened():
                if cap:
                    cap.release()
                return "I couldn't open the webcam (is one connected / not in use by another app?)."
            frame = None
            for _ in range(6):            # let auto-exposure settle before grabbing
                ok, frame = cap.read()
            cap.release()
            if frame is None:
                return "The webcam opened but returned no image."
            target.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(target), frame):
                return f"Couldn't save the photo to {target}."
            self._remember_written(target)
            return f"Photo saved to {target}"

        return self._gated(req, do, f"Would take a webcam photo to {target}.")

    # ---- files --------------------------------------------------------------------------------

    def read_file(self, path: str) -> str:
        target = Path(path).expanduser()
        req = ActionRequest("read_files", f"Read {target}", details=str(target))

        def do():
            if not target.is_file():
                return f"There's no file at {target}."
            return target.read_text(encoding="utf-8", errors="replace")[:_MAX_READ]

        return self._gated(req, do, "[file contents unavailable during verification]")

    def list_dir(self, path: str = ".") -> str:
        target = Path(path).expanduser()
        req = ActionRequest("read_files", f"List {target}", details=str(target))

        def do():
            if not target.is_dir():
                return f"There's no folder at {target}."
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            listing = [f"{'[dir] ' if p.is_dir() else '      '}{p.name}" for p in entries[:200]]
            return "\n".join(listing) or "(empty folder)"

        return self._gated(req, do, "[folder listing unavailable during verification]")

    def write_file(self, path: str, text: str) -> str:
        target = Path(path).expanduser()
        req = ActionRequest("write_files", f"Write {target}", details=f"{len(text)} characters")

        def do():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(text), encoding="utf-8")
            self._remember_written(target)  # so "open it" / "run it" can find the file just made
            return f"Wrote {len(text)} characters to {target}."

        return self._gated(req, do, f"Would write {len(text)} characters to {target}.")

    def organize_dir(self, path: str) -> str:
        """Sort the loose files in a folder into subfolders by type (Images, Documents, ...). Gated by write_files."""
        target = Path(path).expanduser()
        req = ActionRequest("write_files", f"Organize files in {target} by type", details=str(target))

        def do():
            if not target.is_dir():
                return f"There's no folder at {target}."
            categories = set(_FILE_CATEGORIES) | {"Other"}
            moved, used = 0, set()
            for entry in list(target.iterdir()):
                if entry.is_dir() or entry.name.startswith(".") or entry.name in categories:
                    continue
                if not entry.is_file():
                    continue
                cat = _category_for(entry.suffix)
                dest_dir = target / cat
                dest_dir.mkdir(exist_ok=True)
                dest = dest_dir / entry.name
                n = 1
                while dest.exists():                       # never overwrite: foo.txt -> foo (2).txt
                    dest = dest_dir / f"{entry.stem} ({n}){entry.suffix}"
                    n += 1
                try:
                    shutil.move(str(entry), str(dest))
                    moved += 1
                    used.add(cat)
                except OSError:
                    continue
            if not moved:
                return f"Nothing to organize in {target} (already tidy)."
            return f"Organized {moved} file(s) into {len(used)} folder(s) in {target}: {', '.join(sorted(used))}."

        return self._gated(req, do, f"Would sort the files in {target} into type folders.")

    def _last_written_file(self) -> Path | None:
        return (self.state_dir / "last_written.txt") if self.state_dir else None

    def _remember_written(self, path: Path) -> None:
        record = self._last_written_file()
        if record is not None:
            try:
                record.write_text(str(path), encoding="utf-8")
            except OSError:
                pass

    def last_written(self) -> Path | None:
        """The most recent file JARVIS wrote (that still exists), or None."""
        record = self._last_written_file()
        try:
            saved = Path(record.read_text(encoding="utf-8").strip()) if record and record.is_file() else None
        except OSError:
            saved = None
        return saved if (saved and saved.exists()) else None

    # ---- network ------------------------------------------------------------------------------

    def http_request(self, url: str, method: str = "GET", headers: dict | None = None,
                     data=None, timeout: float = 20) -> str:
        """Fetch a URL / call an API and return the response body as text. Gated by 'network'."""
        import urllib.error
        import urllib.request

        url = str(url).strip()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        req = ActionRequest("network", f"Fetch {url}", details=method.upper())

        def do():
            body, hdrs = data, dict(headers or {})
            if isinstance(data, (dict, list)):
                import json as _json
                body = _json.dumps(data).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            elif isinstance(data, str):
                body = data.encode("utf-8")
            request = urllib.request.Request(url, data=body, method=method.upper())
            request.add_header("User-Agent", "JARVIS/1.0")
            for key, value in hdrs.items():
                request.add_header(key, value)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as resp:
                    return resp.read(500_000).decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                return f"HTTP {exc.code} from {url}: {exc.read(500).decode('utf-8', 'replace')}"
            except (urllib.error.URLError, OSError) as exc:
                return f"Couldn't reach {url}: {getattr(exc, 'reason', exc)}"

        return self._gated(req, do, "{}")  # dry-run returns valid-but-empty JSON so parsers don't crash

    def fetch(self, url: str) -> str:
        return self.http_request(url)

    # ---- google (drive + gmail) ---------------------------------------------------------------

    def _google_read(self, capability_summary: str, method: str, query: str) -> str:
        req = ActionRequest("google", capability_summary, details=query)

        def do():
            from core.google import GoogleAuth, GoogleClient, GoogleError  # lazy: keeps this file standalone

            auth = GoogleAuth()
            if not auth.is_connected():
                return "Google isn't connected yet. Run:  jarvis google login"
            try:
                return getattr(GoogleClient(auth), method)(query)
            except GoogleError as exc:
                return f"Google request failed: {exc}"

        return self._gated(req, do, "[Google unavailable during verification]")

    def search_drive(self, query: str) -> str:
        return self._google_read(f"Search your Google Drive for '{query}'", "search_drive", query)

    def search_gmail(self, query: str) -> str:
        return self._google_read(f"Search your Gmail for '{query}'", "search_gmail", query)

    def gmail_organize(self, query: str, action: str, label: str | None = None) -> str:
        """Organize inbox mail (archive / mark read / trash / label). Gated by 'google'. Never sends or
        permanently deletes."""
        summary = f"Gmail: {action} emails matching '{query}'" + (f" as '{label}'" if label else "")
        req = ActionRequest("google", summary, details=query)

        def do():
            from core.google import GoogleAuth, GoogleClient, GoogleError

            auth = GoogleAuth()
            if not auth.is_connected():
                return "Google isn't connected yet. Run:  jarvis google login"
            try:
                return GoogleClient(auth).gmail_organize(query, action, label)
            except GoogleError as exc:
                return f"Google request failed: {exc}"

        return self._gated(req, do, f"Would {summary}.")

    # ---- packages -----------------------------------------------------------------------------

    def install_package(self, name: str) -> str:
        """pip-install a Python package so an evolved skill can use it. Gated by 'packages'."""
        name = str(name).strip()
        if not _VALID_PACKAGE.match(name):
            return f"Refusing to install a suspicious package name: {name!r}"
        req = ActionRequest("packages", f"Install the Python package '{name}'", details="pip install")

        def do():
            args = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
            if sys.prefix == sys.base_prefix:  # not in a venv -> install into the user site
                args.append("--user")
            args.append(name)
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=240, creationflags=_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"Couldn't install {name}: {exc}"
            if proc.returncode == 0:
                _make_installed_packages_importable()  # so the running process can import it right away
                return f"Installed {name}."
            return f"Couldn't install {name}: {(proc.stderr or proc.stdout).strip()[-300:]}"

        return self._gated(req, do, f"Would install {name}.")

    # ---- notify -------------------------------------------------------------------------------

    def notify(self, message: str, title: str = "JARVIS") -> str:
        """Pop up a message box on the user's screen. Gated by 'notify'.

        Runs the dialog in a detached PowerShell process so it appears immediately, doesn't block the
        caller, and survives even a one-shot CLI process exiting.
        """
        message, title = str(message), str(title)
        req = ActionRequest("notify", f"Show a popup: {title}", details=message[:200])

        def do():
            def q(text):  # single-quote for PowerShell; drop newlines the dialog can't take inline
                return re.sub(r"\s*\n\s*", " ", text).replace("'", "''")

            script = ("Add-Type -AssemblyName System.Windows.Forms;"
                      f"[System.Windows.Forms.MessageBox]::Show('{q(message)}','{q(title)}',"
                      "'OK','Information') | Out-Null")
            try:
                subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 creationflags=_NO_WINDOW)
            except OSError as exc:
                return f"Couldn't show the popup: {exc}"
            return f"Popup shown: {title}"

        return self._gated(req, do, f"Would show a popup titled {title!r}.")

    # ---- commands -----------------------------------------------------------------------------

    def run_python(self, path: str) -> str:
        """Actually RUN a Python file: a .pyw as a windowless GUI, a .py in its own console. Gated run_command."""
        target = Path(path).expanduser()
        req = ActionRequest("run_command", f"Run {target.name}", details=str(target))

        def do():
            if not target.is_file():
                return f"There's nothing at {target}."
            exe = sys.executable
            if target.suffix.lower() == ".pyw":                     # GUI: pythonw, no console
                pyw = Path(exe).with_name("pythonw.exe")
                exe = str(pyw) if pyw.exists() else exe
                flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                stdio = subprocess.DEVNULL
            else:                                                   # script: a fresh console shows its output
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                stdio = None
            try:
                subprocess.Popen([exe, str(target)], cwd=str(target.parent), creationflags=flags,
                                 stdin=stdio, stdout=stdio, stderr=stdio, close_fds=True)
            except OSError as exc:
                return f"Couldn't run {target.name}: {exc}"
            return f"Running {target.name}."

        return self._gated(req, do, f"Would run {target.name}.")

    def run_command(self, args, cwd: str | None = None, timeout: float = 60) -> str:
        if isinstance(args, str):
            args = [args]
        args = [str(a) for a in args]
        req = ActionRequest("run_command", f"Run: {' '.join(args)}", details=cwd or "")

        def do():
            try:
                proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                      timeout=timeout, creationflags=_NO_WINDOW)
            except FileNotFoundError:
                return f"Command not found: {args[0]}"
            except subprocess.TimeoutExpired:
                return f"Command timed out after {timeout:.0f}s."
            output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
            return output[:2000] or f"Command finished (exit code {proc.returncode})."

        return self._gated(req, do, "[command output unavailable during verification]")
