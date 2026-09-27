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


def open_window(url: str, fullscreen: bool = True) -> int:
    """Show the interface in a native WebKit window. Returns 0 once it has been closed."""
    try:
        import webview
    except ImportError:
        return in_browser(url, "install pywebview for the proper window: "
                               "python3 -m pip install --user pywebview")
    webview.create_window("JARVIS", url, fullscreen=fullscreen,
                          width=1440, height=900, background_color="#05070c",
                          frameless=fullscreen, easy_drag=False)
    began = time.monotonic()
    webview.start(gui="cocoa")
    # A window that "closes" in the first moment never opened: the process has no session with the
    # window server (a detached or remote shell). Better a browser than a blank screen.
    if time.monotonic() - began < 2.0:
        return in_browser(url, "this terminal has no window session")
    return 0


def run(fullscreen: bool = True) -> int:
    url = address()
    if not url:
        print("JARVIS isn't running, so there is nothing to show yet. Start it with `jarvis on`.")
        return 1
    return open_window(url, fullscreen=fullscreen)


if __name__ == "__main__":
    raise SystemExit(run("--window" not in sys.argv))
