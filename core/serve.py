"""JARVIS with no screen.

`jarvis serve` runs this: the engine, the channels your phone talks to, and nothing at all that needs
a desktop - no window, no hotkey, no microphone on this machine, no camera. It is what runs on the
always-on machine (a small cloud box, or a spare laptop in a corner), so your phone is answered
whether or not your Mac is awake.

What it keeps from the panel: the same `Jarvis` engine and the same skills, the control port so
`jarvis ask`/`jarvis stop` still work, and the phone channels with the same supervisor that puts them
back when they fall over. What it cannot do is anything that lives on your Mac - those requests are
kept and handed to the Mac when it next checks in.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

log = logging.getLogger("jarvis.serve")

STOP = threading.Event()


class Headless:
    """The engine, wired for a machine with nobody sitting at it."""

    def __init__(self, settings=None):
        from core.config import load_settings

        self.settings = settings or load_settings()
        self.jarvis = None
        self.viewer = None          # the interface window, when one is open
        self.listeners: list = []   # the global shortcuts
        self.started = time.time()

    # ---- the engine ------------------------------------------------------------------------------
    def boot(self) -> None:
        from core.orchestrator import Jarvis

        def engine_event(stage, message):
            log.info("%s: %s", stage, message)
            try:
                from core import webui
                webui.step(stage, message)     # shown live under whatever task is running
            except Exception:
                pass

        self.jarvis = Jarvis(on_event=engine_event, confirm=self.confirm)
        for filename, error in self.jarvis.registry.errors.items():
            log.warning("skill %s failed to load: %s", filename, error)
        log.info("engine up with %d skills", len(self.jarvis.registry.skills))

    def ask(self, text: str) -> str:
        """Run one request and give back the words to send back. Used by every channel."""
        if self.jarvis is None:
            return "I'm still starting up, sir - try again in a moment."
        try:
            reply, job = self.jarvis.submit(text)
            if job is not None:          # a new skill is being built; the caller can wait for it
                reply = job()
            answer = str(getattr(reply, "text", reply))   # a Reply carries more than the words
        except Exception as exc:
            log.exception("request failed: %s", text)
            return f"That went wrong, sir: {exc}"
        log.info("asked %r -> %s", text, answer[:200])
        return answer

    def confirm(self, req) -> str:
        """Ask whoever can answer: the phone that sent the request, or the person at the screen.
        If neither is there it is refused - silently doing risky things unattended is worse."""
        try:
            from core import remote
            channel = remote.current()
            if channel is not None:
                return channel.confirm(req)
        except Exception:
            pass
        try:
            from core import webui
            if webui.running():
                return webui.confirm(req)
        except Exception:
            pass
        log.info("refused %s - no one here to approve it", getattr(req, "summary", req))
        return "deny"

    # ---- the control port ------------------------------------------------------------------------
    def on_command(self, command: str) -> dict:
        """`jarvis ping`, `jarvis stop`, `jarvis do "..."` - the same control channel the panel has,
        so the keeper and the command line work here exactly as they do on the Mac."""
        command = (command or "").strip()
        head, _, rest = command.partition(" ")
        head = head.lower()
        if head in ("run", "ask", "do"):
            request = rest.strip()
            if not request:
                return {"ok": False, "error": "nothing to run"}
            threading.Thread(target=self.ask, args=(request,), daemon=True).start()
            return {"ok": True, "queued": request}
        if command.startswith("desk open"):
            self.summon()
            return {"ok": True, "opened": True}
        if head == "desk":
            from core import webui
            return {"ok": True, "url": webui.url() or webui.start()}
        if head == "ping":
            return {"ok": True, "pid": os.getpid(), "headless": True,
                    "up_for": int(time.time() - self.started)}
        if head in ("quit", "exit", "stop"):
            STOP.set()
            return {"ok": True}
        if head in ("show", "toggle"):
            self.summon()
            return {"ok": True, "opened": True}
        if head == "hide":
            return {"ok": True, "note": "close the window yourself, sir - it is a window"}
        if head == "interrupt":
            self.interrupt()
            return {"ok": True}
        if head in ("clear", "reset", "refresh"):
            self.forget()
            return {"ok": True}
        return {"ok": False, "error": f"unknown command {command!r}"}

    # ---- the channels ----------------------------------------------------------------------------
    def channels(self) -> None:
        """Start whatever this machine is meant to answer on, and keep it up."""
        from core import keeper, remote, talk

        def note(text: str) -> None:
            log.info("phone: %s", text)

        try:
            from core import webui
            webui.configure(self.ask, settings=self.settings)
            note(f"interface at {webui.start()}")
        except Exception as exc:
            note(f"interface unavailable: {exc}")
        started = keeper.cloud(self.settings, self.ask)
        if started:
            note(started)
        remote.configure(self.ask, emit=note, settings=self.settings)
        talk.configure(self.ask, emit=note, settings=self.settings)
        if self.settings.get("whatsapp.enabled", False):
            from core import wacloud
            channel = wacloud.configure(self.ask, self.settings, emit=note)
            if channel is None:
                log.warning("WhatsApp is switched on but has no token or phone id - "
                            "`jarvis setkey whatsapp` and set whatsapp.phone_id")
            else:
                talk.hook("/wa", on_get=channel.challenge, on_post=channel.deliver)
                log.info("WhatsApp Cloud channel ready on /wa")
        threading.Thread(target=keeper.watch_services, name="jarvis-phone",
                         args=(self.settings,), kwargs={"log": note}, daemon=True).start()


    # ---- the screen -------------------------------------------------------------------------
    def desk_open(self, fullscreen: bool = True) -> bool:
        """Put the interface on the screen. A child of this process inherits the window session."""
        import subprocess

        argv = [sys.executable, str(ROOT / "jarvis.py"), "desk", "--viewer"]
        if fullscreen:
            argv.append("--fullscreen")
        try:
            self.viewer = subprocess.Popen(argv, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            log.exception("couldn't open the interface")
            return False

    def summon(self) -> None:
        """Ctrl+Shift+J. Bring the interface up - or back, if it was closed."""
        alive = self.viewer is not None and self.viewer.poll() is None
        if alive:
            import subprocess
            subprocess.run(["osascript", "-e",
                            'tell application "System Events" to set frontmost of '
                            '(first process whose unix id is %d) to true' % self.viewer.pid],
                           capture_output=True)
            return
        self.desk_open()

    def interrupt(self) -> None:
        """Ctrl+Shift+C. Stop whatever is being worked on."""
        if self.jarvis is not None:
            try:
                self.jarvis.interrupt()
            except Exception:
                log.exception("interrupt failed")
        from core import webui
        webui.push("event", text="Stopped.")

    def forget(self) -> None:
        """Ctrl+Shift+R. A clean slate: the screen and the short conversation memory both."""
        if self.jarvis is not None:
            try:
                self.jarvis.history.clear()
            except Exception:
                pass
        from core import webui
        webui.forget()

    def hotkeys(self) -> None:
        from window_manager.hotkey import HotkeyListener

        wanted = [(str(self.settings.get("window.hotkey", "ctrl+shift+j")), self.summon,
                   0x4A41, "summon"),
                  (str(self.settings.get("window.interrupt_hotkey", "ctrl+shift+c")),
                   self.interrupt, 0x4A42, "interrupt"),
                  (str(self.settings.get("window.reset_hotkey", "ctrl+shift+r")),
                   self.forget, 0x4A43, "reset")]
        for spec, action, ident, what in wanted:
            try:
                listener = HotkeyListener(spec, action, hotkey_id=ident, name=f"jarvis-{what}")
                listener.start()
                listener.ready.wait(3)
                if listener.error:
                    log.warning("%s shortcut (%s): %s", what, spec, listener.error)
                else:
                    log.info("%s on %s", what, spec)
                self.listeners.append(listener)
            except Exception as exc:
                log.warning("%s shortcut unavailable: %s", what, exc)

    def voice(self) -> None:
        """Say "Jarvis, ..." out loud, as before - the ear is the engine's, not the window's."""
        try:
            from core import voice

            voice.configure(on_command=lambda text: self.ask(text),
                            on_state=lambda state, detail=None: _push("voice", state=state),
                            settings=self.settings)
            if self.settings.get("voice.enabled", False):
                log.info("voice: %s", voice.start())
        except Exception as exc:
            log.warning("voice unavailable: %s", exc)

    def reminders(self) -> None:
        try:
            from core import oslayer, reminders

            reminders.start_loop(emit=lambda text: _push("event", text=f"\u23f0 {text}"),
                                 speak=self.say,
                                 notify=lambda text: oslayer.notify(text, "JARVIS"))
        except Exception as exc:
            log.warning("reminders unavailable: %s", exc)
        try:
            from core import sentinel

            sentinel.start(self.settings, emit=lambda text: _push("event", text=f"\u26a0 {text}"),
                           speak=self.say)
        except Exception as exc:
            log.warning("alerts unavailable: %s", exc)

    def say(self, text: str) -> None:
        try:
            from core import voice
            speaker = voice.speaker()
            if speaker is not None:
                speaker.say(text)
        except Exception:
            pass


def _push(kind: str, **fields) -> None:
    try:
        from core import webui
        webui.push(kind, **fields)
    except Exception:
        pass


def run(settings=None, desktop: bool = False) -> int:
    """Start a headless JARVIS and stay up until something asks it to stop."""
    from core.config import load_settings, user_dir
    from window_manager.ipc import ControlServer

    settings = settings or load_settings()
    logging.basicConfig(filename=str(user_dir() / ("daemon.log" if desktop else "serve.log")),
                        level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    engine = Headless(settings)
    control = ControlServer(engine.on_command, int(settings.get("window.ipc_port", 47821)))
    control.start()
    log.info("JARVIS serving headless (pid %s, control port %s)", os.getpid(), control.port)
    print(("JARVIS is up (pid %d, control port %d)." % (os.getpid(), control.port)) if desktop else
          ("JARVIS is serving with no screen (pid %d, control port %d)." % (os.getpid(), control.port)),
          flush=True)
    try:
        engine.boot()
    except Exception:
        log.exception("engine failed to start")
        print("JARVIS failed to start - see the log.", flush=True)
        return 1
    engine.channels()
    if desktop:
        engine.voice()
        engine.reminders()
        engine.hotkeys()
        if settings.get("desk.open_at_start", True):
            engine.desk_open(fullscreen=bool(settings.get("desk.fullscreen", False)))
            log.info("interface opened on the screen")
    try:
        while not STOP.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    log.info("JARVIS serving stopped")
    try:
        control.close()
    except Exception:
        pass
    return 0
