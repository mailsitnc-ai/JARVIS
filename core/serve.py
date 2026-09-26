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
import threading
import time

log = logging.getLogger("jarvis.serve")

STOP = threading.Event()


class Headless:
    """The engine, wired for a machine with nobody sitting at it."""

    def __init__(self, settings=None):
        from core.config import load_settings

        self.settings = settings or load_settings()
        self.jarvis = None
        self.started = time.time()

    # ---- the engine ------------------------------------------------------------------------------
    def boot(self) -> None:
        from core.orchestrator import Jarvis

        self.jarvis = Jarvis(on_event=lambda stage, message: log.info("%s: %s", stage, message),
                             confirm=self.confirm)
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
        """Nobody is sitting here to approve anything, so ask whoever is on the other end. If the
        request didn't come from a channel that can ask, it is refused - silently doing risky things
        on an unattended machine is worse than refusing."""
        try:
            from core import remote
            channel = remote.current()
            if channel is not None:
                return channel.confirm(req)
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
        if head == "ping":
            return {"ok": True, "pid": os.getpid(), "headless": True,
                    "up_for": int(time.time() - self.started)}
        if head in ("quit", "exit", "stop"):
            STOP.set()
            return {"ok": True}
        if head in ("show", "hide", "toggle"):
            return {"ok": True, "note": "there is no window here - this JARVIS has no screen"}
        return {"ok": False, "error": f"unknown command {command!r}"}

    # ---- the channels ----------------------------------------------------------------------------
    def channels(self) -> None:
        """Start whatever this machine is meant to answer on, and keep it up."""
        from core import keeper, remote, talk

        def note(text: str) -> None:
            log.info("phone: %s", text)

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


def run(settings=None) -> int:
    """Start a headless JARVIS and stay up until something asks it to stop."""
    from core.config import load_settings, user_dir
    from window_manager.ipc import ControlServer

    settings = settings or load_settings()
    logging.basicConfig(filename=str(user_dir() / "serve.log"), level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    engine = Headless(settings)
    control = ControlServer(engine.on_command, int(settings.get("window.ipc_port", 47821)))
    control.start()
    log.info("JARVIS serving headless (pid %s, control port %s)", os.getpid(), control.port)
    print(f"JARVIS is serving with no screen (pid {os.getpid()}, control port {control.port}). "
          f"Log: {user_dir() / 'serve.log'}", flush=True)
    try:
        engine.boot()
    except Exception:
        log.exception("engine failed to start")
        print("JARVIS failed to start - see the log.", flush=True)
        return 1
    engine.channels()
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
