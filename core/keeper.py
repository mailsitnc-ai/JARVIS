"""Keep JARVIS up, so the phone never waits for you to start anything.

Two jobs, one idea: nothing that matters should depend on you clicking something on the Mac.

* **Outside** the app - `jarvis keeper`, run by a launchd agent (`jarvis startup enable`). Every few
  seconds it asks the daemon "are you there?" and starts it if it isn't. That covers logging in after
  a shutdown, waking from sleep, and a crash while you were out. It starts JARVIS.app when that
  bundle exists, so macOS keeps camera/microphone permission attached to JARVIS rather than asking
  again for a faceless python process.
* **Inside** the app - `watch_services()`, a thread the panel starts. The phone channels (the call
  page, its address on the internet, the WhatsApp watcher) come up at boot and are put back whenever
  one of them has fallen over. Sleep drops tunnels and closes Chrome; this notices within half a
  minute instead of waiting for someone to restart JARVIS by hand.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GAP = 15.0                  # how often the keeper asks the daemon whether it is alive
STARTING = 90.0             # how long a starting JARVIS is given to answer before we judge it
PATIENCE = 5                # rounds a silent-but-present JARVIS gets before it's declared wedged
# Regexes for pgrep. The brackets are deliberate: they match the real process and NOT a shell
# command line that merely mentions it - which is how a careless pattern ends up killing the very
# script that went looking.
ENGINE = "jarvis[.]py daemon"
WRAPPER = "JARVIS[.]app/Contents/MacOS/applet"
SERVICE_GAP = 30.0          # how often the daemon looks over its own phone channels
SERVICE_MAX_GAP = 300.0     # and how far it backs off when something keeps refusing to come up

APP_NAME = "JARVIS.app"
APP_PLACES = (Path.home() / "Desktop", Path("/Applications"), Path.home() / "Applications")


# ---- the outside half: the daemon itself ---------------------------------------------------------

def answering(timeout: float = 2.0) -> bool:
    """Is a JARVIS daemon alive and listening on its control port?"""
    try:
        from window_manager import ipc
        return ipc.send("ping", timeout=timeout) is not None
    except Exception:
        return False


def app_bundle() -> Path | None:
    """The JARVIS.app wrapper, if it was built. Launching *that* is what keeps macOS's camera,
    microphone and screen-recording grants attached to JARVIS instead of prompting all over again."""
    for place in APP_PLACES:
        app = place / APP_NAME
        if app.exists():
            return app
    return None


def launch(direct: bool = False) -> str:
    """Start JARVIS in the background. Returns what was done, in words, for the log.

    `direct` skips the app bundle and starts the engine itself - what to do when launching the app
    has already been tried and JARVIS still isn't answering."""
    from core.oslayer import IS_MAC

    trouble = ""
    app = None if direct else (app_bundle() if IS_MAC else None)
    if app is not None:
        try:
            # -n: a new instance. The wrapper may still be sitting there with a dead engine inside
            # it, and then plain `open -a` would only bring that corpse to the front. A fresh one
            # stops whatever is left of the old daemon on the way up.
            subprocess.run(["open", "-g", "-n", "-a", str(app)], check=True, capture_output=True,
                           timeout=90)
            return f"started {app.name}"
        except (OSError, subprocess.SubprocessError) as exc:
            trouble = f"{app.name} wouldn't open ({exc}); "
    try:
        from core.cli import _spawn_daemon
        _spawn_daemon(show=False)
    except Exception as exc:
        return f"{trouble}I couldn't start JARVIS: {exc}"
    return f"{trouble}started the engine directly"


def processes() -> list[int]:
    """JARVIS processes that exist right now - the engine, or the app wrapper around it. Something
    here but not answering means it is either still waking up or wedged; either way, starting a
    second one on top of it would only make a mess."""
    found = []
    for pattern in (ENGINE, WRAPPER):
        try:
            done = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True,
                                  timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        found += [int(line) for line in done.stdout.split() if line.strip().isdigit()]
    return sorted(set(found))


def clear_out() -> str:
    """Put down a JARVIS that is there but has stopped answering, so a fresh one can start."""
    import os
    import signal

    gone = processes()
    for pid in gone:
        for attempt in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, attempt)
            except OSError:
                break
            time.sleep(1.0)
            if not _running(pid):
                break
    return f"cleared out {len(gone)} wedged process(es)" if gone else ""


def _running(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_until_answering(limit: float = STARTING, step: float = 3.0, sleep=time.sleep) -> bool:
    """Give a just-started JARVIS time to come up. It loads a speech model on the way, so this takes
    tens of seconds on a busy Mac - and asking again too early is what starts two of them."""
    waited = 0.0
    while waited < limit:
        sleep(step)
        waited += step
        if answering():
            return True
    return False


def guard(gap: float = GAP, rounds: int | None = None, log=print, sleep=time.sleep) -> int:
    """Never let JARVIS be off. Loops forever (or `rounds` times, for the tests); returns how many
    times it had to bring JARVIS back."""
    started = 0
    failures = 0        # launches in a row that didn't produce a JARVIS that answers
    silent = 0          # rounds in a row with a JARVIS process there but saying nothing
    turn = 0
    while rounds is None or turn < rounds:
        turn += 1
        if answering():
            failures = silent = 0
            sleep(gap)
            continue
        if processes() and silent < PATIENCE:
            silent += 1                  # still waking up: let it, don't start a rival
            if silent == 1:
                log(f"{time.strftime('%Y-%m-%d %H:%M:%S')} JARVIS is starting - waiting for it")
            sleep(gap)
            continue
        if silent >= PATIENCE:
            log(f"{time.strftime('%Y-%m-%d %H:%M:%S')} JARVIS is stuck - {clear_out()}")
        # After two goes through the app wrapper with nothing to show for it, stop being polite
        # about macOS permissions and start the engine itself - a JARVIS without camera access is
        # still worth far more than no JARVIS at all.
        done = launch(direct=failures >= 2)
        log(f"{time.strftime('%Y-%m-%d %H:%M:%S')} JARVIS isn't answering - {done}")
        started += 1
        silent = 0
        failures = 0 if wait_until_answering(sleep=sleep) else failures + 1
    return started


# ---- the inside half: the channels the phone talks to --------------------------------------------

def _told_path() -> Path:
    from core import oslayer
    return Path(oslayer.user_data_dir()) / "phone_link.json"


def told_link() -> str:
    """The outside address we last sent to your phone."""
    try:
        return str(json.loads(_told_path().read_text()).get("link") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def remember_told(link: str) -> None:
    try:
        _told_path().write_text(json.dumps({"link": link, "at": time.time()}))
    except OSError:
        pass


def services(settings, *, talk=None, remote=None, notify=None) -> list[str]:
    """One health pass over everything the phone needs. Returns a line for anything it had to fix,
    and nothing at all when all is well - so a quiet Mac stays quiet in the log."""
    if talk is None:
        from core import talk
    if remote is None:
        from core import remote

    notes: list[str] = []
    if settings.get("talk.enabled", False) and not talk.running():
        notes.append(_first_line(talk.start()))
    if settings.get("talk.outside", False) and talk.running() and not talk.tunnel_healthy():
        if talk.public_url():
            talk.unexpose()        # an address that no longer reaches us is worse than none at all
        notes.append(_first_line(talk.expose(getattr(talk._ACTIVE, "port", talk.PORT),
                                             getattr(talk._ACTIVE, "secret", None))))
        link = talk.public_url()
        if link and notify is not None and link != told_link():
            remember_told(link)
            notify(link)           # a new address: your phone needs to hear about it
    if settings.get("remote.enabled", False) and not remote.running():
        notes.append(_first_line(remote.start()))
    return [note for note in notes if note]


def _first_line(text) -> str:
    return str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""


def watch_services(settings, gap: float = SERVICE_GAP, rounds: int | None = None, log=print,
                   notify=None, sleep=time.sleep) -> None:
    """Keep the phone channels up for as long as JARVIS runs. Backs off when something refuses to
    start (Chrome may simply not be installed) so it never becomes a busy loop."""
    wait = gap
    turn = 0
    while rounds is None or turn < rounds:
        turn += 1
        try:
            notes = services(settings, notify=notify)
        except Exception as exc:
            notes = [f"phone channels: {exc}"]
        for note in notes:
            log(note)
        wait = min(wait * 2, SERVICE_MAX_GAP) if notes else gap
        sleep(wait)
