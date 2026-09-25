"""Talk to JARVIS from your phone: hold a button, speak, hear the answer.

The second video calls the assistant on the phone. A real phone number means telephony (a Twilio
number, a public server, per-minute charges); this does the same thing over your own network: the
Mac serves one small page, your phone opens it, you hold the button and talk, and JARVIS answers in
its own voice. Your speech is transcribed by the Whisper on this machine - the audio never leaves it.

Reaching it from outside the house: `tailscale serve --bg 8765`, which puts it on your private
tailnet with a real HTTPS certificate. That matters for more than privacy - phones only allow
microphone access on a secure page, so plain http over the LAN won't be allowed to record.

The page is locked to a token that's generated once and kept in JARVIS's data folder, so only a
device you've given the link to can talk to it.
"""
from __future__ import annotations

import json
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8765
MAX_AUDIO = 8 * 1024 * 1024        # a held button, not a podcast

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>JARVIS</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; background:#07090d; color:#e8edf2; display:flex;
         flex-direction:column; align-items:center; font:16px/1.5 -apple-system,system-ui,sans-serif; }
  header { padding:22px 16px 6px; letter-spacing:.34em; font-size:13px; color:#7fd4ff; }
  #log { flex:1; width:100%; max-width:640px; padding:12px 16px 120px; box-sizing:border-box;
         overflow-y:auto; }
  .turn { margin:10px 0; padding:11px 14px; border-radius:16px; max-width:85%; white-space:pre-wrap; }
  .me { margin-left:auto; background:#16324a; border-bottom-right-radius:4px; }
  .jarvis { background:#141922; border:1px solid #23303f; border-bottom-left-radius:4px; }
  .state { text-align:center; color:#7c8899; font-size:14px; padding:2px 16px 8px; min-height:20px; }
  footer { position:fixed; bottom:0; left:0; right:0; padding:18px 0 34px; display:flex;
           justify-content:center; background:linear-gradient(transparent,#07090d 38%); }
  #talk { width:132px; height:132px; border-radius:50%; border:none; color:#04121c; font-size:17px;
          font-weight:600; background:radial-gradient(circle at 50% 35%, #8fe3ff, #29a8dd);
          box-shadow:0 0 40px rgba(41,168,221,.45); touch-action:none; user-select:none; }
  #talk.on { background:radial-gradient(circle at 50% 35%, #ffd39a, #f0803a);
             box-shadow:0 0 60px rgba(240,128,58,.6); transform:scale(1.06); }
  #talk:disabled { opacity:.5; box-shadow:none; }
</style></head>
<body>
  <header>J A R V I S</header>
  <div class="state" id="state">hold the button and speak</div>
  <div id="log"></div>
  <footer><button id="talk">hold<br>to talk</button></footer>
<script>
const token = new URLSearchParams(location.search).get('k') || '';
const log = document.getElementById('log'), state = document.getElementById('state');
const talk = document.getElementById('talk');
let recorder = null, chunks = [], busy = false;

function say(who, text) {
  const div = document.createElement('div');
  div.className = 'turn ' + who;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

async function begin() {
  if (busy || recorder) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = e => chunks.push(e.data);
    recorder.onstop = () => { stream.getTracks().forEach(t => t.stop()); send(new Blob(chunks)); };
    recorder.start();
    talk.classList.add('on');
    state.textContent = 'listening...';
  } catch (err) {
    state.textContent = 'the browser blocked the microphone (an https address is needed)';
  }
}

function end() {
  if (!recorder) return;
  talk.classList.remove('on');
  state.textContent = 'thinking...';
  recorder.stop();
  recorder = null;
}

async function send(blob) {
  if (blob.size < 2000) { state.textContent = 'hold the button and speak'; return; }
  busy = true; talk.disabled = true;
  try {
    const res = await fetch('/ask?k=' + encodeURIComponent(token), {method: 'POST', body: blob});
    const data = await res.json();
    if (data.heard) say('me', data.heard);
    say('jarvis', data.text || data.error || 'no answer');
    state.textContent = 'hold the button and speak';
    if (data.audio) new Audio(data.audio + '?k=' + encodeURIComponent(token)).play().catch(() => {});
  } catch (err) {
    state.textContent = 'lost the connection to the Mac';
  }
  busy = false; talk.disabled = false;
}

talk.addEventListener('pointerdown', e => { e.preventDefault(); begin(); });
talk.addEventListener('pointerup', e => { e.preventDefault(); end(); });
talk.addEventListener('pointercancel', end);
talk.addEventListener('pointerleave', end);
</script></body></html>
"""


def token_path():
    from core import oslayer
    return Path(oslayer.user_data_dir()) / "talk_token.txt"


def token() -> str:
    """The secret in the link. Made once and kept, so the same URL keeps working."""
    path = token_path()
    try:
        existing = path.read_text().strip()
        if len(existing) >= 16:
            return existing
    except OSError:
        pass
    fresh = secrets.token_urlsafe(18)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh)
        path.chmod(0o600)
    except OSError:
        pass
    return fresh


class TalkServer:
    """Serves the hold-to-talk page and answers what it sends. `ask(text) -> reply`."""

    def __init__(self, ask, port: int = PORT, host: str = "127.0.0.1", secret: str | None = None,
                 transcribe=None, voice=True, emit=None):
        self.ask = ask
        self.port = int(port)
        self.host = host
        self.secret = secret or token()
        self.transcribe = transcribe or self._whisper
        self.voice = bool(voice)
        self.emit = emit or (lambda _text: None)
        self.audio = {}                 # id -> path of a reply waiting to be fetched
        self.httpd = None
        self._thread = None

    # -- the work ---------------------------------------------------------------------------------
    @staticmethod
    def _whisper(raw: bytes) -> str:
        """What was said, transcribed on this machine."""
        import tempfile

        from core import voicenote
        folder = Path(tempfile.gettempdir()) / "jarvis-voice"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / f"talk-{uuid.uuid4().hex[:8]}.webm"
        try:
            path.write_bytes(raw)
            segments, _ = voicenote.model().transcribe(str(path), beam_size=1, language="en",
                                                       vad_filter=False, without_timestamps=True,
                                                       condition_on_previous_text=False)
            return " ".join(s.text for s in segments).strip()
        finally:
            try:
                path.unlink(missing_ok=True)      # your voice isn't kept after it's been read
            except OSError:
                pass

    def answer(self, raw: bytes) -> dict:
        """Audio in, answer out - the whole turn, with no HTTP in sight (so it can be tested)."""
        heard = ""
        try:
            heard = str(self.transcribe(raw) or "").strip()
        except Exception as exc:
            return {"error": f"I couldn't make out the audio: {exc}"}
        if not heard:
            return {"heard": "", "text": "I didn't catch that, sir."}
        from core.voice import parse_wake
        _, stripped = parse_wake(heard)           # "Jarvis, ..." is optional here - you pressed talk
        request = stripped or heard
        self.emit(f"phone: {request}")
        reply = str(self.ask(request) or "").strip() or "Done, sir."
        out = {"heard": heard, "text": reply}
        if self.voice:
            from core import speechfile
            path = speechfile.to_audio(reply)
            if path:
                ident = uuid.uuid4().hex[:12]
                self.audio[ident] = path
                out["audio"] = f"/audio/{ident}"
        return out

    def take_audio(self, ident: str):
        """Hand over a reply's audio once, then forget it."""
        path = self.audio.pop(str(ident), None)
        if not path or not Path(path).exists():
            return None, None
        data = Path(path).read_bytes()
        from core import speechfile
        speechfile.cleanup(path)
        return data, ("audio/mp4" if str(path).endswith(".m4a") else "audio/wav")

    # -- the server -------------------------------------------------------------------------------
    def start(self) -> str:
        if self.running():
            return self.url()
        server = self
        pageurl = self.url()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):        # no request spam in the panel
                pass

            def _allowed(self):
                query = parse_qs(urlparse(self.path).query)
                given = (query.get("k") or [""])[0] or self.headers.get("X-JARVIS-Token", "")
                return secrets.compare_digest(str(given), server.secret)

            def _send(self, code, body, kind="text/plain; charset=utf-8"):
                payload = body if isinstance(body, bytes) else str(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                route = urlparse(self.path).path
                if not self._allowed():
                    return self._send(403, "Not for this device.")
                if route in ("/", "/index.html"):
                    return self._send(200, PAGE, "text/html; charset=utf-8")
                if route.startswith("/audio/"):
                    data, kind = server.take_audio(route.rsplit("/", 1)[-1])
                    return self._send(200, data, kind) if data else self._send(404, "gone")
                return self._send(404, "no such page")

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, "Not for this device.")
                if urlparse(self.path).path != "/ask":
                    return self._send(404, "no such page")
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return self._send(400, "no audio")
                if length > 4 * MAX_AUDIO:
                    return self._send(413, "that's far too much audio")
                raw = self.rfile.read(length)   # read it all, then judge: cutting the phone off
                if len(raw) > MAX_AUDIO:        # mid-upload just looks like a broken connection
                    return self._send(413, "that's too much audio")
                try:
                    out = server.answer(raw)
                except Exception as exc:
                    out = {"error": str(exc)}
                self._send(200, json.dumps(out), "application/json")

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self.httpd.server_address[1])     # port 0 means "any free one" - report it
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True,
                                        name="jarvis-talk")
        self._thread.start()
        return self.url() if pageurl.endswith(":0/?k=" + self.secret) else pageurl

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def running(self) -> bool:
        return self.httpd is not None and self._thread is not None and self._thread.is_alive()

    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?k={self.secret}"


# ---- one server per JARVIS ----------------------------------------------------------------------

_ACTIVE: TalkServer | None = None
_HANDLERS: dict = {}


def configure(ask, emit=None, settings=None) -> None:
    _HANDLERS.update(ask=ask, emit=emit or (lambda _t: None), settings=settings)


def start() -> str:
    global _ACTIVE
    if not _HANDLERS:
        return "The talk page needs the JARVIS panel running."
    if _ACTIVE is not None and _ACTIVE.running():
        return f"Already listening on {_ACTIVE.url()}"
    settings = _HANDLERS.get("settings")
    _ACTIVE = TalkServer(_HANDLERS["ask"], port=int(settings.get("talk.port", PORT) if settings else PORT),
                         host=str(settings.get("talk.host", "127.0.0.1") if settings else "127.0.0.1"),
                         emit=_HANDLERS.get("emit"))
    url = _ACTIVE.start()
    return (f"Talk page up at {url}\nOn your phone: run `tailscale serve --bg {_ACTIVE.port}` here once, "
            f"then open the tailnet address with ?k={_ACTIVE.secret} - hold the button and talk.")


def stop() -> str:
    global _ACTIVE
    if _ACTIVE is not None and _ACTIVE.running():
        _ACTIVE.stop()
        _ACTIVE = None
        return "Talk page closed."
    return "The talk page wasn't running."


def running() -> bool:
    return _ACTIVE is not None and _ACTIVE.running()


def url() -> str:
    return _ACTIVE.url() if running() else ""
