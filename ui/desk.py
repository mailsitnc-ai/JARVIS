"""The window the new interface lives in.

Not Electron and not Chrome: this is the Mac's own WebKit through pywebview, which is a window and a
web view and nothing else - tens of megabytes rather than hundreds, which matters on 8 GB. The page
itself is served by core/webui.py from 127.0.0.1.

    jarvis desk            open it full screen
    jarvis desk --window   open it as an ordinary window (for side-by-side work)

If pywebview isn't installed it falls back to the default browser, so the interface is never
unreachable just because a package is missing.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def address(timeout: float = 20.0) -> str:
    """Where the interface is being served. Asks the running JARVIS to put it up if it isn't."""
    from window_manager import ipc

    reply = ipc.send("desk", timeout=timeout)
    if isinstance(reply, dict) and reply.get("url"):
        return str(reply["url"])
    return ""


def in_browser(url: str, why: str = "") -> int:
    subprocess.run(["open", url], check=False)
    print(("Opened in your browser" + (f" ({why})" if why else "")) + ".")
    return 0


def wear_the_face() -> None:
    """Give the window a name and a face in the Dock, instead of a bare python rocket."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSData

        from core.talk import icon_png

        from Foundation import NSProcessInfo

        NSProcessInfo.processInfo().setProcessName_("JARVIS")   # not "Python" in the Dock
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)            # a real app: Dock icon, cmd-tab, menu bar
        data = NSData.dataWithBytes_length_(icon_png(512), len(icon_png(512)))
        picture = NSImage.alloc().initWithData_(data)
        if picture is not None:
            app.setApplicationIconImage_(picture)
    except Exception:
        pass                                    # a missing icon is no reason not to open


def open_window(url: str, fullscreen: bool = False) -> int:
    """Show the interface in a native WebKit window. Returns 0 once it has been closed.

    Filling the screen is not the same as macOS fullscreen: a maximised window keeps the three
    traffic lights and can be put beside something else, which is the point of being a window."""
    try:
        import webview
    except ImportError:
        return in_browser(url, "install pywebview for the proper window: "
                               "python3 -m pip install --user pywebview")
    wear_the_face()
    webview.create_window("JARVIS", url, fullscreen=fullscreen, maximized=not fullscreen,
                          width=1440, height=900, background_color="#05070c",
                          frameless=False, easy_drag=False, text_select=True)
    began = time.monotonic()
    webview.start(gui="cocoa")
    # A window that "closes" in the first moment never opened: the process has no session with the
    # window server (a detached or remote shell). Better a browser than a blank screen.
    if time.monotonic() - began < 2.0:
        return in_browser(url, "this terminal has no window session")
    return 0


def ask_jarvis_to_open(fullscreen: bool) -> bool:
    """Have the running JARVIS open the window. Its children inherit the screen; ours may not."""
    from window_manager import ipc

    reply = ipc.send("desk open" if fullscreen else "desk open window", timeout=10)
    return bool(isinstance(reply, dict) and reply.get("opened"))


def run(fullscreen: bool = False, viewer: bool = False) -> int:
    if not viewer and ask_jarvis_to_open(fullscreen):
        print("Opening JARVIS on your screen.")
        return 0
    url = address()
    if not url:
        print("JARVIS isn't running, so there is nothing to show yet. Start it with `jarvis on`.")
        return 1
    return open_window(url, fullscreen=fullscreen)


if __name__ == "__main__":
    raise SystemExit(run("--fullscreen" in sys.argv, viewer="--viewer" in sys.argv))
