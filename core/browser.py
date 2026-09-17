"""Real control of Chrome, over the Chrome DevTools Protocol (CDP).

This is JARVIS's browser DOM access: it launches its own Chrome window with remote debugging enabled
(a dedicated profile, so it never disturbs your normal Chrome) and drives it over a websocket - navigate,
read the page's text, click things, type into fields, run JS, screenshot. Everything the higher layers
do goes through the permission broker's 'browser' capability, so nothing happens unless you allow it.

Light on purpose: no Selenium/Playwright browser downloads. The only third-party piece is
'websocket-client' (pure Python), auto-installed on first use like opencv.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Native value setters, so typing works on React/Vue-controlled inputs (which ignore a plain .value=).
_TYPE_JS = r"""
(function(text, sel){
  var el = sel ? document.querySelector(sel)
              : (document.querySelector('input[type=search],input[name=q],input[type=text],input:not([type]),textarea')
                 || document.activeElement);
  if(!el) return false;
  el.focus();
  var proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  var setter = Object.getOwnPropertyDescriptor(proto, 'value');
  if(setter && setter.set){ setter.set.call(el, text); } else { el.value = text; }
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return true;
})(%s, %s)
"""
_CLICK_JS = r"""
(function(q){
  var els = Array.prototype.slice.call(
    document.querySelectorAll('a,button,input[type=submit],input[type=button],[role=button],[onclick],summary'));
  var t = q.toLowerCase();
  var el = els.filter(function(e){ return e.offsetParent !== null; })
             .find(function(e){ return ((e.innerText||e.value||'').trim().toLowerCase()).indexOf(t) !== -1; });
  if(!el){ try { el = document.querySelector(q); } catch(e){} }
  if(el){ el.scrollIntoView({block:'center'}); el.click(); return true; }
  return false;
})(%s)
"""
_SUBMIT_JS = r"""
(function(sel){
  var el = sel ? document.querySelector(sel)
              : (document.querySelector('input[type=search],input[name=q],input[type=text],textarea')
                 || document.activeElement);
  if(!el) return false;
  if(el.form){ el.form.submit(); return true; }
  ['keydown','keypress','keyup'].forEach(function(type){
    el.dispatchEvent(new KeyboardEvent(type, {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
  });
  return true;
})(%s)
"""


class BrowserError(RuntimeError):
    pass


class ChromeController:
    def __init__(self, port: int = 9222, profile_dir: str | None = None, chrome_exe: str | None = None,
                 installer=None):
        self.port = int(port)
        self.profile_dir = profile_dir
        self.chrome_exe = chrome_exe
        self.installer = installer  # callable(pkg) -> str, the broker's gated pip install
        self._ws = None

    # ---- launching / connecting ---------------------------------------------------------------

    def _chrome_path(self) -> str | None:
        if self.chrome_exe and Path(self.chrome_exe).exists():
            return self.chrome_exe
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

    def _version(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError):
            return None

    def ensure(self) -> None:
        """Make sure a debuggable Chrome is running, launching JARVIS's own if needed. Raises BrowserError."""
        if self._version() is not None:
            return
        exe = self._chrome_path()
        if not exe:
            raise BrowserError("I couldn't find Chrome on this PC to control.")
        profile = self.profile_dir or str(Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS" / "chrome-debug")
        Path(profile).mkdir(parents=True, exist_ok=True)
        args = [exe, f"--remote-debugging-port={self.port}", f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check", "--start-maximized", "about:blank"]
        try:
            subprocess.Popen(args, creationflags=_NO_WINDOW, close_fds=True)
        except OSError as exc:
            raise BrowserError(f"I couldn't launch Chrome: {exc}") from exc
        for _ in range(40):  # up to ~10s for the debug endpoint to come up
            if self._version() is not None:
                return
            time.sleep(0.25)
        raise BrowserError("Chrome started but its debugging port didn't come up.")

    def _import_ws(self):
        try:
            import websocket  # websocket-client
            return websocket
        except ImportError:
            if self.installer:
                self.installer("websocket-client")  # gated by 'packages'
            try:
                import websocket
                return websocket
            except ImportError as exc:
                raise BrowserError("I need the 'websocket-client' package to control the browser.") from exc

    def _targets(self) -> list:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError):
            return []

    def _page_target(self) -> dict:
        pages = [t for t in self._targets() if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        if pages:
            return pages[0]
        # No page yet: ask Chrome to open a blank one.
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/new?about:blank", timeout=3) as resp:
                target = json.loads(resp.read().decode("utf-8"))
                if target.get("webSocketDebuggerUrl"):
                    return target
        except (OSError, ValueError):
            pass
        raise BrowserError("Chrome is running but I couldn't attach to a tab.")

    def _connect(self):
        if self._ws is not None:
            return self._ws
        websocket = self._import_ws()
        target = self._page_target()
        self._ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20,
                                                max_size=None, enable_multithread=True)
        self._msg_id = 0
        return self._ws

    def _cmd(self, method: str, params: dict | None = None, timeout: float = 20):
        ws = self._connect()
        self._msg_id += 1
        mid = self._msg_id
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = ws.recv()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise BrowserError(msg["error"].get("message", "CDP error"))
                return msg.get("result", {})
            # else: it's an event or another id - ignore and keep reading
        raise BrowserError(f"Timed out waiting for {method}.")

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ---- actions ------------------------------------------------------------------------------

    def evaluate(self, expression: str, await_promise: bool = False):
        result = self._cmd("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                "awaitPromise": await_promise})
        details = result.get("result", {})
        if result.get("exceptionDetails"):
            raise BrowserError(str(result["exceptionDetails"].get("text", "JS error")))
        return details.get("value")

    def navigate(self, url: str, wait: float = 15) -> None:
        self._cmd("Page.enable")
        self._cmd("Page.navigate", {"url": url})
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:  # wait for the document to finish loading
            time.sleep(0.3)
            try:
                if self.evaluate("document.readyState") == "complete":
                    return
            except BrowserError:
                continue

    def current_url(self) -> str:
        return self.evaluate("location.href") or ""

    def title(self) -> str:
        return self.evaluate("document.title") or ""

    def text(self, max_chars: int = 6000) -> str:
        body = self.evaluate("document.body ? document.body.innerText : ''") or ""
        body = " ".join(body.split())
        return body[:max_chars]

    def click(self, query: str) -> bool:
        return bool(self.evaluate(_CLICK_JS % json.dumps(query)))

    def type_text(self, text: str, selector: str | None = None, submit: bool = False) -> bool:
        ok = bool(self.evaluate(_TYPE_JS % (json.dumps(text), json.dumps(selector))))
        if ok and submit:
            self.evaluate(_SUBMIT_JS % json.dumps(selector))
        return ok

    def screenshot(self, path: str) -> str:
        self._cmd("Page.enable")
        result = self._cmd("Page.captureScreenshot", {"format": "png"})
        data = result.get("data")
        if not data:
            raise BrowserError("Chrome returned no screenshot.")
        import base64
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(data))
        return path
