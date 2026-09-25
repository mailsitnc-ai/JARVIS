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
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
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


# --- best-effort DOM scripts for web apps that have no personal send API (they can break if the site
#     changes its markup; the fix_code / browser tools can be used to adjust the selectors). ---
# WhatsApp Web's search box is now an <input> (it used to be a contenteditable div) - try both.
_WA_FOCUS_SEARCH_JS = ("(function(){var el=document.querySelector('#side input[type=\"text\"]')"
                       "||document.querySelector('input[aria-label*=\"Search\" i]')"
                       "||document.querySelector('div[contenteditable=\"true\"][data-tab=\"3\"]');"
                       "if(!el)return false;el.focus();if(el.select)el.select();"
                       "else document.execCommand('selectAll',false,null);return true;})()")
# Chat titles in the (search-filtered) chat list, top to bottom. Section headers have no span[title].
_WA_RESULTS_JS = ("JSON.stringify(Array.prototype.slice.call(document.querySelectorAll('#pane-side div[role=\"row\"]'))"
                  ".map(function(r,i){var s=r.querySelector('span[title]');return s?[i,s.getAttribute('title')]:null})"
                  ".filter(Boolean))")
# Centre of row i, scrolled into view - WhatsApp ignores synthetic JS clicks, so we click it with a
# real CDP mouse event at these coordinates.
_WA_ROW_POINT_JS = ("JSON.stringify((function(i){var r=document.querySelectorAll('#pane-side div[role=\"row\"]')[i];"
                    "if(!r)return null;r.scrollIntoView({block:'center'});var b=r.getBoundingClientRect();"
                    "return [b.x+b.width/2,b.y+b.height/2];})(%s))")
# The open chat's name, from the conversation header.
_WA_HEADER_JS = ("(function(){var h=document.querySelector('#main [data-testid=\"conversation-info-header\"]')"
                 "||document.querySelector('#main header');if(!h)return '';"
                 "var s=h.querySelector('span[dir=\"auto\"]')||h.querySelector('span[title]');"
                 "return ((s&&(s.innerText||s.getAttribute('title')))||h.innerText.split('\\n')[0]||'').trim();})()")
_WA_FOCUS_COMPOSE_JS = ("(function(){var el=document.querySelector('#main footer div[contenteditable=\"true\"]')"
                        "||document.querySelector('footer div[contenteditable=\"true\"]')"
                        "||document.querySelector('div[contenteditable=\"true\"][data-tab=\"10\"]');"
                        "if(!el)return false;el.focus();return true;})()")
# "WhatsApp is open in another window" -> click "Use here" so this tab takes over.
_WA_USE_HERE_JS = ("(function(){var b=Array.prototype.slice.call(document.querySelectorAll('button,div[role=button]'))"
                   ".find(function(e){return /use here/i.test(e.innerText||'')});if(b){b.click();return true}return false})()")
# WhatsApp keeps renaming this: plain "Send" in a chat, "Send 2 selected" on a file preview, and
# the icon went from data-icon="send" to "wds-ic-send-filled".
# In a chat it's a <button aria-label="Send">; on a file preview it's a DIV labelled
# "Send 2 selected" wrapped round a wds-ic-send-filled icon, which can't take keyboard focus.
_WA_SEND_SELECTOR = ('button[aria-label^="Send"],div[role="button"][aria-label^="Send"],'
                     'span[data-icon="send"],[data-icon="wds-ic-send-filled"]')
_WA_SEND_READY_JS = f"!!document.querySelector({_WA_SEND_SELECTOR!r})"
_WA_CLICK_SEND_JS = ("(function(){var b=document.querySelector(%r);"
                     "if(!b)return false;(b.closest('button')||b).click();return true;})()"
                     % _WA_SEND_SELECTOR)

# The last messages of the open chat: id, direction, text, and whether it's a voice note.
_WA_MESSAGES_JS = ("JSON.stringify(Array.prototype.slice.call("
                   "document.querySelectorAll('#main div[role=\"row\"]')).slice(-%d).map(function(r){"
                   "var b=r.querySelector('[data-id]');var pre=r.querySelector('[data-pre-plain-text]');"
                   "var t=r.querySelector('span.selectable-text');"
                   "return {id:b?b.getAttribute('data-id'):'',out:!!r.querySelector('.message-out'),"
                   "audio:!!(r.querySelector('span[data-icon=\"audio-play\"]')"
                   "||r.querySelector('button[aria-label*=\"Play\" i]')),"
                   "meta:pre?pre.getAttribute('data-pre-plain-text'):'',"
                   "text:t?t.innerText:''};}))")
# Attaching a file: WhatsApp only creates the right <input> once you have really clicked the
# paperclip and then the menu item (it ignores synthetic clicks), so we need both their positions.
_WA_POINT_JS = ("JSON.stringify((function(sel){var e=document.querySelector(sel);if(!e)return null;"
                "var r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];})(%s))")
_WA_MENU_POINT_JS = ("JSON.stringify((function(label){var items=Array.prototype.slice.call("
                     "document.querySelectorAll('[role=\"menuitem\"],[role=\"menu\"] button,"
                     "[role=\"application\"] li,li,div[role=\"button\"]'));"
                     "var el=items.find(function(e){return ((e.innerText||'').trim().toLowerCase())"
                     ".indexOf(label)===0});if(!el)return null;var r=el.getBoundingClientRect();"
                     "return [r.x+r.width/2,r.y+r.height/2];})(%s))")
# Clicking at coordinates is fragile in WhatsApp's layout - another element often sits over the
# button. Tagging the element we want and then FOCUSING it lets us press it with the keyboard, which
# needs no hit-testing at all.
_WA_TAG_BUTTON_JS = ("(function(sel){var e=document.querySelector(sel);if(!e)return false;"
                     "e=e.closest('button,div[role=\"button\"]')||e;"   # an icon can't take focus
                     "e.setAttribute('data-jarvis-target','1');return true;})(%s)")
_WA_TAG_MENU_JS = ("(function(label){var items=Array.prototype.slice.call(document.querySelectorAll("
                   "'[role=\"menuitem\"],[role=\"menu\"] button,li,div[role=\"button\"]'));"
                   "var el=items.find(function(e){return ((e.innerText||'').trim().toLowerCase())"
                   ".indexOf(label)===0});if(!el)return false;"
                   "el.setAttribute('data-jarvis-target','1');return true;})(%s)")
_WA_UNTAG_JS = ("(function(){var e=document.querySelector('[data-jarvis-target]');"
                "if(e)e.removeAttribute('data-jarvis-target');return true;})()")
# Where the tagged element is, and whether a click there would actually reach it (WhatsApp often
# draws something over the footer, and then clicking those coordinates does nothing at all).
_WA_SCROLL_TO_TARGET_JS = ("(function(){var e=document.querySelector('[data-jarvis-target]');"
                           "if(!e)return false;e.scrollIntoView({block:'center'});return true;})()")
_WA_TARGET_POINT_JS = ("JSON.stringify((function(){var e=document.querySelector('[data-jarvis-target]');"
                       "if(!e)return null;var r=e.getBoundingClientRect();"
                       "var x=r.x+r.width/2,y=r.y+r.height/2;var at=document.elementFromPoint(x,y);"
                       "var onscreen=r.width>0&&r.height>0&&y>0&&y<window.innerHeight&&x>0&&"
                       "x<window.innerWidth;"
                       "return [x,y,!!(onscreen&&at&&(at===e||e.contains(at)||at.contains(e)||"
                       "(e.compareDocumentPosition(at)&16)||at.closest('[data-jarvis-target]')))];})())")
# The document input accepts anything; the one that is always there only takes images.
_WA_DOC_INPUT_JS = ("JSON.stringify(Array.prototype.slice.call(document.querySelectorAll("
                    "'input[type=\"file\"]')).map(function(i){return i.getAttribute('accept')||'*'}))")

_GC_SEARCH_JS = ("(function(q){var el=document.querySelector('input[aria-label*=\"Search\" i]')"
                 "||document.querySelector('[role=textbox]')||document.querySelector('input');"
                 "if(!el)return false;el.focus();"
                 "var set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
                 "if(set&&set.set&&el.tagName==='INPUT'){set.set.call(el,q);}else{el.textContent=q;}"
                 "el.dispatchEvent(new Event('input',{bubbles:true}));return true;})(%s)")
_GC_OPEN_FIRST_JS = ("(function(){var r=document.querySelector('[role=option]')"
                     "||document.querySelector('[role=listbox] [role=option]')"
                     "||document.querySelector('[data-member-id]');if(r){r.click();return true;}return false;})()")
_GC_TYPE_JS = ("(function(m){var el=document.querySelector('[contenteditable=true][role=textbox]')"
               "||document.querySelector('div[aria-label*=\"Type\" i] [contenteditable=true]')"
               "||document.querySelector('[contenteditable=true]');if(!el)return false;el.focus();"
               "document.execCommand&&document.execCommand('insertText',false,m);"
               "el.dispatchEvent(new InputEvent('input',{bubbles:true}));return true;})(%s)")
_GC_SEND_JS = ("(function(){var b=document.querySelector('button[aria-label*=\"Send\" i]');"
               "if(b){b.click();return true;}var el=document.querySelector('[contenteditable=true][role=textbox]')"
               "||document.querySelector('[contenteditable=true]');if(!el)return false;"
               "['keydown','keypress','keyup'].forEach(function(t){el.dispatchEvent(new KeyboardEvent(t,"
               "{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));});return true;})()")


class ChromeController:
    def __init__(self, port: int = 9222, profile_dir: str | None = None, chrome_exe: str | None = None,
                 installer=None):
        self.port = int(port)
        self.profile_dir = profile_dir
        self.chrome_exe = chrome_exe
        self.installer = installer  # callable(pkg) -> str, the broker's gated pip install
        self._ws = None
        # One websocket, possibly several threads (the phone channel watches the chat while a request
        # is answering in it). Two commands in flight on one socket read each other's replies and
        # both time out, so every command takes its turn.
        self._io = threading.RLock()

    # ---- launching / connecting ---------------------------------------------------------------

    def _chrome_path(self) -> str | None:
        if self.chrome_exe and Path(self.chrome_exe).exists():
            return self.chrome_exe
        from .oslayer import chrome_path
        return chrome_path()

    def _version(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError):
            return None

    def alive(self) -> bool:
        """Is a debuggable Chrome actually running right now? (A page that is merely slow to draw
        must not be mistaken for a browser that has been quit.)"""
        return self._version() is not None

    def ensure(self) -> None:
        """Make sure a debuggable Chrome is running, launching JARVIS's own if needed. Raises BrowserError."""
        if self._version() is not None:
            return
        exe = self._chrome_path()
        if not exe:
            raise BrowserError("I couldn't find Chrome on this PC to control.")
        if self.profile_dir:
            profile = self.profile_dir
        else:
            from .oslayer import user_data_dir
            profile = str(user_data_dir() / "chrome-debug")
        Path(profile).mkdir(parents=True, exist_ok=True)
        flags = [f"--remote-debugging-port={self.port}", f"--user-data-dir={profile}",
                 "--remote-allow-origins=*",  # newer Chrome blocks DevTools websockets otherwise
                 "--no-first-run", "--no-default-browser-check", "--start-maximized", "about:blank"]
        bundle = re.sub(r"(\.app)/Contents/MacOS/.*$", r"\1", exe)
        if sys.platform == "darwin" and bundle.endswith(".app"):
            # `open -g` starts it in the BACKGROUND: JARVIS keeps its own Chrome running for things
            # like the phone channel, and that must never jump in front of what you're doing.
            args = ["open", "-g", "-n", "-a", bundle, "--args"] + flags
        else:
            args = [exe] + flags
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

    def _page_target(self, prefer: str | None = None) -> dict:
        pages = [t for t in self._targets() if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        if prefer:
            for t in pages:
                if str(t.get("url", "")).startswith(prefer):
                    return t
        if pages:
            return pages[0]
        # No page tab (they were all closed): ask Chrome to open a blank one. Chrome >= 111 requires a
        # PUT for /json/new; older Chrome only allows GET - try PUT first, then fall back to GET.
        url = f"http://127.0.0.1:{self.port}/json/new?about:blank"
        for method in ("PUT", "GET"):
            try:
                req = urllib.request.Request(url, method=method)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    target = json.loads(resp.read().decode("utf-8"))
                    if target.get("webSocketDebuggerUrl"):
                        return target
            except (OSError, ValueError):
                continue
        raise BrowserError("Chrome is running but I couldn't open a tab to work in - close JARVIS's Chrome "
                           "window and try again so it relaunches cleanly.")

    def _use_tab(self, url_prefix: str) -> bool:
        """Attach to an already-open tab whose URL starts with url_prefix (e.g. the WhatsApp tab), so we
        reuse it instead of loading a second copy. Returns True if such a tab exists."""
        for t in self._targets():
            if t.get("type") == "page" and str(t.get("url", "")).startswith(url_prefix):
                if self._ws is not None and getattr(self, "_target_id", None) == t.get("id"):
                    return True
                self.close()
                self._connect(prefer=url_prefix)
                try:
                    self._cmd("Target.activateTarget", {"targetId": t.get("id")}, timeout=5)
                except BrowserError:
                    pass
                return True
        return False

    def _connect(self, prefer: str | None = None):
        if self._ws is not None:
            return self._ws
        websocket = self._import_ws()
        target = self._page_target(prefer)
        self._target_id = target.get("id")
        # suppress_origin: recent Chrome 403-rejects a DevTools websocket that carries an Origin header
        # ("Rejected an incoming WebSocket connection from the ... origin"). DevTools clients send none.
        self._ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20,
                                                max_size=None, enable_multithread=True, suppress_origin=True)
        self._msg_id = 0
        return self._ws

    def _cmd(self, method: str, params: dict | None = None, timeout: float = 20):
        """Send one CDP command. The connection is cached across calls, so if the tab/window was closed
        or Chrome restarted since, the socket is dead - drop it and retry once on a fresh tab."""
        with self._io:
            return self._cmd_locked(method, params, timeout)

    def _cmd_locked(self, method: str, params: dict | None = None, timeout: float = 20):
        try:
            return self._cmd_once(method, params, timeout)
        except (OSError, EOFError) as exc:  # ConnectionResetError, socket timeouts
            err = exc
        except Exception as exc:  # websocket-client's own WebSocket*Exception types
            if not type(exc).__name__.startswith("WebSocket"):
                raise
            err = exc
        self.close()
        try:
            self.ensure()
            return self._cmd_once(method, params, timeout)
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"lost the connection to Chrome ({type(err).__name__}) and couldn't "
                               f"reconnect: {exc}") from exc

    def _cmd_once(self, method: str, params: dict | None = None, timeout: float = 20):
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

    def _wait_for(self, expression: str, timeout: float = 25) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.evaluate(expression):
                    return True
            except BrowserError:
                pass
            time.sleep(0.5)
        return False

    # ---- messaging (best-effort DOM automation of web apps) -----------------------------------

    _WA_LOGGED_IN_JS = ("!!(document.querySelector('#pane-side')"
                        "||document.querySelector('div[contenteditable=\"true\"][data-tab=\"3\"]')"
                        "||document.querySelector('div[title=\"Search input textbox\"]'))")

    def _prepare_whatsapp(self) -> None:
        """WhatsApp Web refuses to boot (hangs on the splash) unless the browser grants it persistent
        storage - which Chrome denies on a fresh, low-engagement profile. Grant it up front via CDP, and
        bypass the service worker so a stale/broken SW can't hang the load. Best-effort; never raises."""
        for method, params in (
            ("Browser.grantPermissions",
             {"origin": "https://web.whatsapp.com", "permissions": ["durableStorage", "notifications"]}),
            ("Network.enable", {}),
            ("Network.setBypassServiceWorker", {"bypass": True}),
        ):
            try:
                self._cmd(method, params, timeout=6)
            except BrowserError:
                pass

    def _wa_logged_in(self) -> bool:
        try:
            return bool(self.evaluate(self._WA_LOGGED_IN_JS))
        except BrowserError:
            return False

    def whatsapp_login(self, wait: float = 150, emit=None) -> str:
        """Open WhatsApp Web in JARVIS's own Chrome and wait for a one-time QR scan. The dedicated Chrome
        profile persists the session, so this is needed only once - after linking, sends are quick and
        never need scanning again. Scanning links JARVIS as an extra device; it does NOT sign the user
        out on their phone."""
        say = emit or (lambda *a: None)
        self.ensure()
        self._prepare_whatsapp()
        if not str(self.current_url()).startswith("https://web.whatsapp.com"):
            self.navigate("https://web.whatsapp.com", wait=25)
        if self._wa_logged_in():
            return "WhatsApp is already linked in JARVIS's Chrome — you're set; sends are quick from here."
        say("whatsapp", "Opened WhatsApp Web in JARVIS's Chrome. On your phone: Settings ▸ Linked devices ▸ "
                        "Link a device, and scan the QR. (This adds JARVIS as an extra device — it won't log "
                        "you out on your phone.)")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(2)
            if self._wa_logged_in():
                return ("WhatsApp is linked now — I'll stay signed in, so from now on I just send, no window "
                        "to scan or babysit.")
        return ("Not linked yet. The WhatsApp Web window is open in JARVIS's Chrome — scan the QR (Linked "
                "devices) whenever you're ready; once linked it stays linked and future sends are instant.")

    def _key(self, key: str, code: int) -> None:
        """A real key press (CDP input event), which web apps can't tell apart from the keyboard."""
        for kind in ("keyDown", "keyUp"):
            self._cmd("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": key,
                                                 "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code})

    def click_at(self, x: float, y: float) -> None:
        """A real left click at viewport coordinates (CDP input event)."""
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            # A short timeout on purpose: when the page is busy these can hang, and a click that
            # never returns used to freeze the phone channel for half a minute.
            self._cmd("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left",
                                                   "clickCount": 0 if kind == "mouseMoved" else 1},
                      timeout=8)

    @staticmethod
    def _norm(name: str) -> str:
        return " ".join(re.sub(r"[^\w\s]", " ", (name or "").lower()).split())

    @classmethod
    def _match_score(cls, wanted: str, title: str) -> int:
        """How well a chat title matches the name asked for (0 = not a match)."""
        w, t = cls._norm(wanted), cls._norm(title)
        if not w or not t:
            return 0
        if t == w:
            return 100
        tw = t.split()
        if tw[:len(w.split())] == w.split():
            return 80  # "inaya" -> "Inaya Khan"
        if all(word in tw for word in w.split()):
            return 60  # every word of the name appears as a whole word
        if t.startswith(w):
            return 40
        # Spelling slips ("shashwat ma boi" vs "Shaswat My Boi"): close enough overall, word by word.
        import difflib
        ratio = difflib.SequenceMatcher(None, w, t).ratio()
        return 30 if ratio >= 0.75 else 0

    def _wa_search(self, to: str, query: str):
        """Type `query` into WhatsApp's search and return the best (score, -i, row, title) for `to`."""
        if not self.evaluate(_WA_FOCUS_SEARCH_JS):
            raise BrowserError("couldn't find WhatsApp's search box (the layout may have changed)")
        self._key("Backspace", 8)  # clear any previous search (the text is selected)
        self._cmd("Input.insertText", {"text": query})
        best, deadline = None, time.monotonic() + 4
        while time.monotonic() < deadline:
            time.sleep(0.4)
            rows = json.loads(self.evaluate(_WA_RESULTS_JS) or "[]")
            scored = sorted(((self._match_score(to, title), -i, i, title) for i, title in rows), reverse=True)
            if scored and scored[0][0] > 0:
                best = scored[0]
                if best[0] >= 80:
                    break
        return best

    def _wa_open_chat(self, to: str) -> str | None:
        """Search WhatsApp Web for `to` and open the chat whose name really matches. Returns the opened
        chat's title, or None if nothing matched - never falls back to 'whatever chat is on top'."""
        # WhatsApp's own search is a literal substring match, so a misspelt name finds nothing - fall
        # back to searching just the first word and fuzzy-matching the titles that come back.
        queries = [to] + ([to.split()[0]] if len(to.split()) > 1 else [])
        best = None
        for query in queries:
            best = self._wa_search(to, query)
            if best:
                break
        if not best:
            return None
        _, _, row, title = best
        point = json.loads(self.evaluate(_WA_ROW_POINT_JS % row) or "null")
        if not point:
            return None
        self.click_at(*point)
        if self._wait_for(f"({_WA_HEADER_JS}) === {json.dumps(title)}", 5):
            return title
        header = self.evaluate(_WA_HEADER_JS) or ""
        return header if self._match_score(to, header) else None

    def whatsapp_send(self, to: str, message: str) -> str:
        """Send a WhatsApp message via WhatsApp Web, as the user's linked device. `to` can be a contact
        name, a group name or a phone number. Checks the opened chat really is `to` before typing."""
        self.ensure()
        digits = re.sub(r"\D", "", to or "")
        by_phone = bool(digits) and (str(to).strip().startswith("+") or len(digits) >= 8)
        # Reuse an open WhatsApp tab (a second WA tab shows "open in another window" and blocks).
        on_wa = self._use_tab("https://web.whatsapp.com")
        self._prepare_whatsapp()
        if by_phone:
            self.navigate(f"https://web.whatsapp.com/send?phone={digits}", wait=30)
        elif not (on_wa and self._wa_logged_in()):
            self.navigate("https://web.whatsapp.com", wait=30)
        self.evaluate(_WA_USE_HERE_JS)
        if not self._wait_for(self._WA_LOGGED_IN_JS, 40):
            return ("WhatsApp Web isn't linked yet. Say 'log in to WhatsApp' once — I'll open it so you "
                    "can scan the QR, and it stays linked after that.")
        if by_phone:
            if not self._wait_for(_WA_FOCUS_COMPOSE_JS.replace("el.focus();return true", "return true"), 25):
                return f"WhatsApp couldn't open a chat with {to} - is that number on WhatsApp?"
            chat = to
        else:
            chat = self._wa_open_chat(to)
            if not chat:
                return (f"I couldn't find a WhatsApp chat matching '{to}'. Say the name as it's saved in "
                        f"WhatsApp, or give the phone number.")
        problem = self.whatsapp_type_send(message)
        return f"I opened the chat with {chat} but {problem}" if problem else \
            f"Sent the WhatsApp message to {chat}."

    def whatsapp_type_send(self, message: str) -> str | None:
        """Type a message into the chat that is ALREADY open and send it. Returns None, or what
        went wrong. (whatsapp_send opens the right chat first; the phone channel is already in it.)"""
        if not self._wait_for(_WA_FOCUS_COMPOSE_JS, 10):
            return "couldn't find the message box."
        for i, line in enumerate(str(message).split("\n")):  # Shift+Enter between lines; a bare
            if i:                                            # Enter would send half the message
                self._cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter",
                                                     "modifiers": 8, "windowsVirtualKeyCode": 13})
                self._cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
                                                     "modifiers": 8, "windowsVirtualKeyCode": 13})
            if line:
                self._cmd("Input.insertText", {"text": line})
        if not self._wait_for(_WA_SEND_READY_JS, 10):
            return "WhatsApp's Send button never appeared."
        time.sleep(0.3)
        if not self.evaluate(_WA_CLICK_SEND_JS):
            self._key("Enter", 13)
        return None

    def whatsapp_open(self, to: str) -> str:
        """Open a chat (contact name, group name or phone number) and leave it open. Returns the chat
        title, or a sentence explaining why not."""
        self.ensure()
        digits = re.sub(r"\D", "", to or "")
        by_phone = bool(digits) and (str(to).strip().startswith("+") or len(digits) >= 8)
        on_wa = self._use_tab("https://web.whatsapp.com")
        self._prepare_whatsapp()
        if by_phone:
            self.navigate(f"https://web.whatsapp.com/send?phone={digits}", wait=30)
        elif not (on_wa and self._wa_logged_in()):
            self.navigate("https://web.whatsapp.com", wait=30)
        self.evaluate(_WA_USE_HERE_JS)
        if not self._wait_for(self._WA_LOGGED_IN_JS, 40):
            return "WhatsApp Web isn't linked yet - say 'log in to WhatsApp' once and scan the QR."
        if by_phone:
            if not self._wait_for(_WA_FOCUS_COMPOSE_JS.replace("el.focus();return true", "return true"), 25):
                return f"WhatsApp couldn't open a chat with {to} - is that number on WhatsApp?"
            return self.evaluate(_WA_HEADER_JS) or to
        return self._wa_open_chat(to) or f"I couldn't find a WhatsApp chat matching '{to}'."

    def whatsapp_reset(self, chat: str | None = None) -> bool:
        """Put WhatsApp back to a plain chat you can type in: close any file preview, menu or media
        viewer left behind by a failed attachment. Without this, one bad attachment stops every
        later reply with "couldn't find the message box"."""
        try:
            if self.evaluate(_WA_SEND_READY_JS):        # a file preview is open
                self.press_element('[aria-label="Close"],[data-icon="x-alt"],[data-icon="x"]')
                time.sleep(0.6)
                for label in ("discard", "yes"):        # "Discard selection?" -> discard it
                    if self.evaluate(_WA_TAG_MENU_JS % json.dumps(label)):
                        self._press_tagged()
                        time.sleep(0.6)
                        break
            if self.evaluate("!!document.querySelector('[role=\"dialog\"]')"):
                self.press_element('[role="dialog"] [aria-label="Close"],[data-icon="x-viewer"]')
                time.sleep(0.4)
            ready = self.evaluate(_WA_FOCUS_COMPOSE_JS.replace("el.focus();return true", "return true"))
            if not ready and chat:
                self.whatsapp_open(chat)
                ready = self.evaluate(_WA_FOCUS_COMPOSE_JS.replace("el.focus();return true", "return true"))
            return bool(ready)
        except BrowserError:
            return False

    def whatsapp_open_chat_title(self) -> str:
        """Whose chat is open right now (empty when none is)."""
        try:
            return str(self.evaluate(_WA_HEADER_JS) or "")
        except BrowserError:
            return ""

    def whatsapp_messages(self, limit: int = 12) -> list:
        """The last messages of the open chat, oldest first: id, out (sent by this account), text,
        audio (a voice note), meta (WhatsApp's own '[time, date] sender:' string)."""
        raw = self.evaluate(_WA_MESSAGES_JS % int(limit))
        try:
            rows = json.loads(raw or "[]")
        except ValueError:
            return []
        return [r for r in rows if isinstance(r, dict) and (r.get("id") or r.get("text"))]

    def press_element(self, selector: str) -> bool:
        """Press the first element matching this CSS selector, the way a person would. WhatsApp
        ignores JS .click(), so this is a real click - or, where something is drawn over the spot,
        keyboard focus and Enter."""
        return self._wa_press(_WA_TAG_BUTTON_JS % json.dumps(selector))

    def _press_tagged(self) -> bool:
        """Press the tagged element: a real click when that spot really is clickable, otherwise
        focus + Enter, which needs no coordinates. WhatsApp needs both - its send button is a DIV
        that can't take focus, and its paperclip usually has something drawn over it."""
        # Scroll to it FIRST and let the scroll finish: measuring in the same breath gives the
        # position the element had before it moved, and the click then lands on empty chat.
        self.evaluate(_WA_SCROLL_TO_TARGET_JS)
        time.sleep(0.4)
        point = json.loads(self.evaluate(_WA_TARGET_POINT_JS) or "null")
        if point and point[2]:
            self.click_at(point[0], point[1])
            self.evaluate(_WA_UNTAG_JS)
            return True
        if point and 0 < point[1] < 2000:
            # On screen, but something else answers for that spot - click it anyway: in WhatsApp the
            # thing on top is usually a wrapper that passes the click on to the button underneath.
            self.click_at(point[0], point[1])
        try:
            self._cmd("DOM.enable")
            doc = self._cmd("DOM.getDocument", {"depth": -1, "pierce": True})
            node = self._cmd("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                                   "selector": "[data-jarvis-target]"})
            node_id = node.get("nodeId") if isinstance(node, dict) else 0
            if not node_id:
                return False
            self._cmd("DOM.focus", {"nodeId": node_id})
        except BrowserError:
            return False
        # Enter only presses a focused button when the event carries its TEXT - without "\r" Chrome
        # delivers the key but performs no default action, and nothing happens.
        self._cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter",
                                             "text": "\r", "windowsVirtualKeyCode": 13,
                                             "nativeVirtualKeyCode": 13})
        self._cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
                                             "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        self.evaluate(_WA_UNTAG_JS)
        return True

    def _wa_press(self, finder_js: str) -> bool:
        """Press the element that `finder_js` (a tagging script) points at."""
        if not self.evaluate(finder_js):
            return False
        return self._press_tagged()

    def _wa_menu_item(self, label: str, tries: int = 8, pause: float = 0.25):
        """Where the menu entry with this label is, once it appears (None if it never does)."""
        for attempt in range(max(1, tries)):
            if attempt:
                time.sleep(pause)
            point = json.loads(self.evaluate(_WA_MENU_POINT_JS % json.dumps(label)) or "null")
            if point:
                return point
        return None

    def whatsapp_attach(self, path: str, caption: str = "") -> str | None:
        """Send a file into the chat that's already open. Returns None, or what went wrong.

        WhatsApp builds the file input only after a REAL click on the paperclip and then on
        "Document" - a synthetic click does nothing at all, and the input that's always in the page
        only takes images."""
        from pathlib import Path as _Path
        full = str(_Path(path).expanduser().resolve())
        if not _Path(full).is_file():
            return f"there's no file at {path}."
        deadline = time.monotonic() + 30      # never let one attachment hold up the whole channel
        # The paperclip TOGGLES the menu, so a press can just as easily close one that was left
        # open: look first, and press again if the menu isn't there.
        open_menu = bool(self._wa_menu_item("document", tries=1))
        for _ in range(2):
            if open_menu:
                break
            if not self._wa_press(_WA_TAG_BUTTON_JS % json.dumps('#main footer button[aria-label="Attach"]')):
                return "I couldn't find WhatsApp's attach button."
            open_menu = bool(self._wa_menu_item("document", tries=8))
        if not open_menu:
            return "WhatsApp's attach menu didn't open."
        if not self._wa_press(_WA_TAG_MENU_JS % json.dumps("document")):
            return "WhatsApp's attach menu has no 'Document' entry."
        node_id, wait_until = 0, time.monotonic() + 6
        while time.monotonic() < min(wait_until, deadline) and not node_id:
            time.sleep(0.25)
            accepts = json.loads(self.evaluate(_WA_DOC_INPUT_JS) or "[]")
            index = next((i for i, a in enumerate(accepts) if "image" not in (a or "")), None)
            if index is None:
                continue
            self._cmd("DOM.enable")
            doc = self._cmd("DOM.getDocument", {"depth": -1, "pierce": True})
            nodes = self._cmd("DOM.querySelectorAll", {"nodeId": doc["root"]["nodeId"],
                                                       "selector": 'input[type="file"]'})
            ids = nodes.get("nodeIds") or []
            node_id = ids[index] if index < len(ids) else 0
        if not node_id:
            return "WhatsApp never offered a file box for that attachment."
        self._cmd("DOM.setFileInputFiles", {"files": [full], "nodeId": node_id})
        if not self._wait_for(_WA_SEND_READY_JS, max(4, deadline - time.monotonic())):
            # Escape is NOT the way out of here - it opens a "Discard selection?" box on top.
            return "WhatsApp never showed the Send button for that file."
        if caption:
            self.evaluate(_WA_FOCUS_COMPOSE_JS)
            self._cmd("Input.insertText", {"text": caption})
        time.sleep(0.5)
        while time.monotonic() < deadline:   # the preview closing is how we know it really went
            self._wa_press(_WA_TAG_BUTTON_JS % json.dumps(_WA_SEND_SELECTOR))
            for _ in range(8):
                time.sleep(0.4)
                if not self.evaluate(_WA_SEND_READY_JS):
                    return None
        return "WhatsApp kept the file in its preview instead of sending it."

    def chat_send(self, to: str, message: str) -> str:
        """Send a Google Chat message via chat.google.com. Best-effort DOM automation; needs Chat signed
        in in this Chrome. Consumer Chat has no send API, so this drives the web UI."""
        self.ensure()
        self.navigate("https://chat.google.com", wait=30)
        if not self._wait_for("!!document.querySelector('[role=textbox],[contenteditable=true],input')", 45):
            return "Google Chat isn't ready - open JARVIS's Chrome and sign in to chat.google.com once, then retry."
        self.evaluate(_GC_SEARCH_JS % json.dumps(to))
        time.sleep(1.8)
        self.evaluate(_GC_OPEN_FIRST_JS)
        time.sleep(1.8)
        if not self._wait_for("!!document.querySelector('[contenteditable=true][role=textbox],div[aria-label*=\"Type\"] [contenteditable=true]')", 20):
            return f"I couldn't open a Google Chat conversation for '{to}'."
        if not self.evaluate(_GC_TYPE_JS % json.dumps(message)):
            return "I opened Chat but couldn't find the message box (the layout may have changed)."
        time.sleep(0.4)
        self.evaluate(_GC_SEND_JS)
        return f"Sent the Google Chat message to {to}."

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
