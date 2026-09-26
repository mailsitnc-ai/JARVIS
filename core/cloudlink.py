"""The Mac's end of the cloud JARVIS.

JARVIS in the cloud (cloud/worker.js) answers your WhatsApp whether or not this Mac is awake, and
anything that genuinely needs the Mac - open this, read that, type here - it writes down instead of
refusing. This is the other half: whenever the Mac is up, it asks the cloud what was left for it,
carries it out, and sends the answer back to your phone through the same channel.

So the arrangement is: the cloud is JARVIS's voice and memory, always awake; the Mac is its hands,
whenever it happens to be awake. Nothing is lost in between.

Settings:  cloud.address  (https://jarvis.<you>.workers.dev)   cloud.enabled   cloud.poll_s
The shared secret is a credential and lives with the keys:  jarvis setkey cloud
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("jarvis.cloudlink")

POLL = 20.0          # how often the Mac asks whether anything was left for it
QUIET = 120.0        # how long to back off after the cloud fails to answer
TIMEOUT = 15.0


def address() -> str:
    from core.config import load_settings
    return str(load_settings().get("cloud.address", "") or "").strip().rstrip("/")


def secret() -> str:
    from core import keystore
    return str(keystore.get_key("cloud") or "")


def _call(path: str, data: dict | None = None, timeout: float = TIMEOUT):
    """One call to the cloud. Returns what came back, or None if it couldn't be reached."""
    base, key = address(), secret()
    if not base or not key:
        return None
    url = f"{base}{path}{'&' if '?' in path else '?'}s={urllib.parse.quote(key)}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            raw = answer.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw
    except urllib.error.HTTPError as exc:
        log.warning("the cloud refused %s: %s", path, exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.info("couldn't reach the cloud: %s", exc)
    return None


def waiting() -> list[dict]:
    """What was left for the Mac while it was away. Taking them empties the queue, so each one is
    carried out exactly once - a job read twice is a message sent twice."""
    answer = _call("/jobs")
    if not isinstance(answer, dict):
        return []
    jobs = answer.get("jobs")
    return [job for job in jobs if isinstance(job, dict) and job.get("text")] if jobs else []


def report(job: dict, answer: str) -> bool:
    """Send what the Mac did back to the phone, through the cloud."""
    done = _call("/jobs", {"id": job.get("id"), "to": job.get("from"), "text": answer})
    return isinstance(done, dict) and bool(done.get("ok"))


def once(ask, jobs=None) -> int:
    """Take whatever is waiting, do it, answer it. Returns how many were carried out."""
    done = 0
    for job in (waiting() if jobs is None else jobs):
        request = str(job.get("text") or "").strip()
        if not request:
            continue
        log.info("cloud left me: %s", request)
        try:
            answer = str(ask(request) or "").strip() or "Done, sir."
        except Exception as exc:
            log.exception("a job from the cloud failed")
            answer = f"That went wrong, sir: {exc}"
        report(job, answer)
        done += 1
    return done


def watch(ask, gap: float = POLL, rounds: int | None = None, sleep=time.sleep) -> None:
    """Keep asking the cloud for work for as long as this Mac is up."""
    turn = 0
    while rounds is None or turn < rounds:
        turn += 1
        try:
            if once(ask) == 0 and not reachable():
                sleep(QUIET)          # the cloud is down or not set up: stop hammering it
                continue
        except Exception:
            log.exception("the cloud link fell over")
            sleep(QUIET)
            continue
        sleep(gap)


def reachable() -> bool:
    return _call("/ping", timeout=8.0) is not None or bool(address() and secret())


def start(ask) -> str:
    """Begin picking up what the cloud left for this Mac."""
    if not address():
        return ("I don't know where the cloud JARVIS lives yet - set it with "
                "`jarvis config --set cloud.address=https://...workers.dev`.")
    if not secret():
        return "The cloud link needs its shared secret: `jarvis setkey cloud`."
    threading.Thread(target=watch, args=(ask,), name="jarvis-cloudlink", daemon=True).start()
    return f"Picking up whatever the cloud leaves for me ({address()})."
