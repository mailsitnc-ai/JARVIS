"""The new face of JARVIS: a full-screen interface, served locally, drawn in the Mac's own WebKit.

The old panel is a Tk window with a rendered arc reactor. This is the same engine behind glass: the
page lives in ui/web/, this module serves it to 127.0.0.1 and streams what JARVIS is doing as it
happens, so a task running in the background can be watched rather than waited for.

Nothing here is reachable from outside this Mac - the server binds to the loopback address and every
request carries a secret made at startup.
"""
from __future__ import annotations

import json
import queue
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "ui" / "web"
PORT = 8766
KEEP_TASKS = 25          # finished tasks kept, so you can look back at what it did
KEEP_STEPS = 60          # steps kept per task: enough to show the work, not a transcript


class Feed:
    """Everything JARVIS says, fanned out to whoever is watching."""

    def __init__(self):
        self.watchers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def join(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self.watchers.append(channel)
        return channel

    def leave(self, channel: queue.Queue) -> None:
        with self._lock:
            if channel in self.watchers:
                self.watchers.remove(channel)

    def push(self, kind: str, **fields) -> None:
        line = json.dumps({"kind": kind, "at": time.time(), **fields})
        with self._lock:
            watching = list(self.watchers)
        for channel in watching:
            try:
                channel.put_nowait(line)
            except queue.Full:
                pass          # a page that has stopped reading is not worth blocking the engine for


class Desk:
    """The state the interface draws: what's running, what was said, what JARVIS knows."""

    def __init__(self, ask, settings=None, speak=None):
        self.ask = ask
        self.settings = settings
        self.speak = speak
        self.feed = Feed()
        self.tasks: list[dict] = []
        self.waiting: dict[str, dict] = {}     # approvals the screen has not answered yet
        self.started = time.time()
        self._lock = threading.Lock()

    # ---- work ------------------------------------------------------------------------------------
    def submit(self, text: str, spoken: bool = False) -> dict:
        """Take a request and start it. Returns the task at once - the answer arrives on the feed."""
        request = str(text or "").strip()
        if not request:
            return {"error": "nothing to do"}
        task = {"id": uuid.uuid4().hex[:8], "text": request, "state": "running",
                "steps": [], "answer": "", "started": time.time(), "finished": 0.0}
        with self._lock:
            self.tasks.insert(0, task)
            del self.tasks[KEEP_TASKS:]
        self.feed.push("task", task=self.brief(task))
        threading.Thread(target=self._work, args=(task, spoken), name="jarvis-desk-task",
                         daemon=True).start()
        return self.brief(task)

    def _work(self, task: dict, spoken: bool) -> None:
        try:
            answer = str(self.ask(task["text"]) or "").strip() or "Done, sir."
            task["state"] = "done"
        except Exception as exc:
            answer = f"That went wrong, sir: {exc}"
            task["state"] = "failed"
        task["answer"] = answer
        task["finished"] = time.time()
        self.feed.push("task", task=self.brief(task))
        self.feed.push("answer", id=task["id"], text=answer)
        if spoken and self.speak:
            try:
                self.speak(answer)
            except Exception:
                pass

    def ask_permission(self, req, wait: float = 180.0) -> str:
        """Put an approval in front of whoever is at the screen and wait for the answer.

        The old panel had buttons; this is where they went. Nobody there, or nobody deciding,
        means no - an unattended machine should not quietly do risky things."""
        ident = uuid.uuid4().hex[:8]
        answered = threading.Event()
        pending = {"decision": "deny", "event": answered}
        with self._lock:
            self.waiting[ident] = pending
        self.feed.push("confirm", id=ident,
                       summary=str(getattr(req, "summary", req) or "")[:300],
                       details=str(getattr(req, "details", "") or "")[:600],
                       capability=str(getattr(req, "capability", "") or ""))
        answered.wait(timeout=wait)
        with self._lock:
            self.waiting.pop(ident, None)
        self.feed.push("confirmed", id=ident, decision=pending["decision"])
        return pending["decision"]

    def decide(self, ident: str, decision: str) -> bool:
        """The answer coming back from the interface."""
        choice = decision if decision in ("once", "always", "deny") else "deny"
        with self._lock:
            pending = self.waiting.get(ident)
        if pending is None:
            return False
        pending["decision"] = choice
        pending["event"].set()
        return True

    def forget(self) -> None:
        """Ctrl+Shift+R: wipe the screen and the short conversation memory both."""
        with self._lock:
            self.tasks.clear()
        self.feed.push("reset")

    def step(self, stage: str, message: str) -> None:
        """A line of working-out from the engine. It belongs to whatever is running now."""
        text = str(message or "").strip()
        if not text:
            return
        with self._lock:
            running = next((t for t in self.tasks if t["state"] == "running"), None)
            if running is not None:
                running["steps"].append({"stage": stage, "text": text, "at": time.time()})
                del running["steps"][:-KEEP_STEPS]
                ident = running["id"]
            else:
                ident = ""
        self.feed.push("step", id=ident, stage=stage, text=text)

    def brief(self, task: dict) -> dict:
        return {"id": task["id"], "text": task["text"], "state": task["state"],
                "answer": task["answer"], "steps": task["steps"][-KEEP_STEPS:],
                "started": task["started"], "finished": task["finished"]}

    # ---- what the panels show --------------------------------------------------------------------
    def state(self) -> dict:
        import platform

        with self._lock:
            tasks = [self.brief(t) for t in self.tasks]
        return {"systems": self.systems(), "awareness": self.awareness(platform),
                "memory": self.memory(), "tasks": tasks,
                "up_for": int(time.time() - self.started)}

    def systems(self) -> list[dict]:
        """The four lights: is the brain there, the voice, the memory, the ear."""
        out = []
        try:
            from core.llm_router import LLMRouter
            from core.config import load_settings
            settings = self.settings or load_settings()
            router = LLMRouter(settings)
            provider = getattr(router, "provider", None) or settings.get("llm.provider", "auto")
            out.append({"name": "AI", "state": "Ready", "ok": True, "detail": str(provider)})
        except Exception as exc:
            out.append({"name": "AI", "state": "Unavailable", "ok": False, "detail": str(exc)[:60]})
        try:
            from core import voice
            live = bool(voice.listening())
            out.append({"name": "Voice", "state": "Connected" if live else "Idle", "ok": live,
                        "detail": "listening for 'Jarvis'" if live else "say /voice on"})
        except Exception:
            out.append({"name": "Voice", "state": "Idle", "ok": False, "detail": ""})
        try:
            from core.orchestrator import MEMORY_DIR
            kept = len(list(Path(MEMORY_DIR).glob("*.json"))) if Path(MEMORY_DIR).exists() else 0
            out.append({"name": "Memory", "state": "Active", "ok": True,
                        "detail": f"{kept} file(s) of recall"})
        except Exception:
            out.append({"name": "Memory", "state": "Off", "ok": False, "detail": ""})
        try:
            from core import cloudlink
            linked = bool(cloudlink.address())
            out.append({"name": "Cloud", "state": "Linked" if linked else "Local only",
                        "ok": linked, "detail": cloudlink.address() or "no cloud address"})
        except Exception:
            out.append({"name": "Cloud", "state": "Local only", "ok": False, "detail": ""})
        return out

    def awareness(self, platform) -> list[dict]:
        import datetime as dt

        where = f"Desktop session - {platform.system().replace('Darwin', 'macOS')}"
        try:
            from core import talk
            phone = talk.public_url() or (talk.url() if talk.running() else "")
        except Exception:
            phone = ""
        return [
            {"label": "Environment", "value": where},
            {"label": "Phone", "value": "Reachable from anywhere" if phone else "Not published"},
            {"label": "Time", "value": dt.datetime.now().strftime("%H:%M")},
        ]

    def memory(self) -> list[dict]:
        """A glance at what JARVIS is carrying: the work in hand, and how you like it done."""
        out = []
        try:
            from core.orchestrator import MEMORY_DIR
            from memory.store import MemoryStore

            store = MemoryStore(MEMORY_DIR, 5000)
            kept = store.interactions()
            out.append({"label": "Conversations", "value": f"{len(kept)} remembered"})
            for item in reversed(kept[-2:]):
                said = str(item.get("request") or "")
                if said:
                    out.append({"label": "Lately", "value": said[:70]})
        except Exception:
            pass
        try:
            from core.agenda import AgendaStore
            items = list(AgendaStore().all()) if hasattr(AgendaStore(), "all") else []
            out.append({"label": "Agenda", "value": f"{len(items)} standing task(s)"})
        except Exception:
            pass
        if not out:
            out.append({"label": "Memory", "value": "nothing recalled yet"})
        return out


_ACTIVE: "Server | None" = None
_HANDLERS: dict = {}


class Server:
    def __init__(self, desk: Desk, port: int = PORT, secret: str | None = None):
        self.desk = desk
        self.port = int(port)
        self.secret = secret or secrets.token_urlsafe(16)
        self.httpd = None
        self._thread = None

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?k={self.secret}"

    def running(self) -> bool:
        return self.httpd is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        desk, secret = self.desk, self.secret

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _allowed(self) -> bool:
                asked = parse_qs(urlparse(self.path).query).get("k", [""])[0]
                return asked == secret or self.headers.get("X-Jarvis") == secret

            def _send(self, code: int, body, kind="text/plain; charset=utf-8"):
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                route = urlparse(self.path).path
                if not self._allowed():
                    return self._send(403, "Not for this device.")
                if route in ("/", "/index.html"):
                    return self._send(200, page(secret), "text/html; charset=utf-8")
                if route in ("/app.js", "/app.css"):
                    kind = "application/javascript" if route.endswith(".js") else "text/css"
                    return self._send(200, (WEB / route.lstrip("/")).read_text(), kind)
                if route == "/state":
                    return self._send(200, json.dumps(desk.state()), "application/json")
                if route == "/events":
                    return self._stream()
                return self._send(404, "no such page")

            def _stream(self):
                """Server-sent events: the page watches JARVIS work, line by line."""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                channel = desk.feed.join()
                try:
                    self.wfile.write(b": hello\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            line = channel.get(timeout=15)
                        except queue.Empty:
                            self.wfile.write(b": still here\n\n")   # keep the pipe warm
                            self.wfile.flush()
                            continue
                        self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    desk.feed.leave(channel)

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, "Not for this device.")
                route = urlparse(self.path).path
                length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
                raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
                try:
                    sent = json.loads(raw or "{}")
                except ValueError:
                    return self._send(400, "that isn't JSON")
                if route == "/ask":
                    out = desk.submit(str(sent.get("text") or ""), bool(sent.get("spoken")))
                    return self._send(200, json.dumps(out), "application/json")
                if route == "/decide":
                    ok = desk.decide(str(sent.get("id") or ""), str(sent.get("decision") or "deny"))
                    return self._send(200, json.dumps({"ok": ok}), "application/json")
                return self._send(404, "no such page")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = int(self.httpd.server_address[1])
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True,
                                        name="jarvis-desk")
        self._thread.start()
        return self.url()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def page(secret: str) -> str:
    """The interface, with its own secret baked in so it can talk back."""
    html = (WEB / "index.html").read_text()
    return html.replace("__SECRET__", secret)


# ---- what the rest of JARVIS calls ----------------------------------------------------------------

def configure(ask, settings=None, speak=None) -> None:
    _HANDLERS.update(ask=ask, settings=settings, speak=speak)


def start(port: int = PORT) -> str:
    """Serve the interface. Returns the address to point a window at."""
    global _ACTIVE
    if _ACTIVE is not None and _ACTIVE.running():
        return _ACTIVE.url()
    if not _HANDLERS.get("ask"):
        return ""
    desk = Desk(_HANDLERS["ask"], settings=_HANDLERS.get("settings"), speak=_HANDLERS.get("speak"))
    _ACTIVE = Server(desk, port=port)
    return _ACTIVE.start()


def stop() -> None:
    global _ACTIVE
    if _ACTIVE is not None:
        _ACTIVE.stop()
        _ACTIVE = None


def running() -> bool:
    return _ACTIVE is not None and _ACTIVE.running()


def url() -> str:
    return _ACTIVE.url() if running() else ""


def desk() -> Desk | None:
    return _ACTIVE.desk if running() else None


def push(kind: str, **fields) -> None:
    """Send something to the interface - an event, a reply, a change of voice state."""
    if running():
        _ACTIVE.desk.feed.push(kind, **fields)


def confirm(req) -> str:
    """The engine asking permission. Routed to the interface; refused if there is no interface."""
    if running():
        return _ACTIVE.desk.ask_permission(req)
    return "deny"


def forget() -> None:
    if running():
        _ACTIVE.desk.forget()


def step(stage: str, message: str) -> None:
    """A line of the engine's working-out, shown under whatever task is running."""
    if running():
        _ACTIVE.desk.step(stage, message)
