"""JARVIS keeping an eye on things: speaks up (and posts a notification) when something needs you.

  battery   20%, 10%, 5% while unplugged - each level once per discharge (resets when you plug in)
  disk      under 5 GB free on the main disk - at most once a day

Runs as a light background thread in the JARVIS panel (one check a minute). Turn off with
`jarvis config --set alerts.enabled=false`; `alerts.speak=false` keeps the notifications but stays quiet.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

BATTERY_LEVELS = (20, 10, 5)
DISK_MIN_GB = 5.0


class Sentinel:
    """Pure decision logic: feed readings, get the alerts that should fire now (unit-tested)."""

    def __init__(self):
        self.battery_warned: set[int] = set()
        self.disk_warned_at = 0.0

    def check(self, battery, disk_free_gb, now: float) -> list[str]:
        alerts = []
        if battery:
            pct, plugged = battery
            if plugged:
                self.battery_warned.clear()
            else:
                due = [lvl for lvl in BATTERY_LEVELS if pct <= lvl and lvl not in self.battery_warned]
                if due:
                    self.battery_warned.update(lvl for lvl in BATTERY_LEVELS if pct <= lvl)
                    if pct <= 5:
                        alerts.append(f"Sir, battery is critical at {pct} percent. Plug in now or I'll be "
                                      "going dark shortly.")
                    elif pct <= 10:
                        alerts.append(f"Sir, battery's down to {pct} percent. I'd find a charger.")
                    else:
                        alerts.append(f"Sir, battery's at {pct} percent. You may want to plug in.")
        if disk_free_gb is not None and disk_free_gb < DISK_MIN_GB and now - self.disk_warned_at > 86400:
            self.disk_warned_at = now
            alerts.append(f"Sir, the disk is nearly full - only {disk_free_gb:.1f} gigabytes left. Say "
                          "'organize my downloads' and I'll help tidy up.")
        return alerts


def disk_free_gb() -> float | None:
    try:
        return shutil.disk_usage(str(Path.home())).free / 1e9
    except OSError:
        return None


def start(settings, emit, speak) -> threading.Thread | None:
    """Background watch: emit(text) to the panel, speak(text) aloud (if alerts.speak)."""
    if not settings.get("alerts.enabled", True):
        return None
    from core import oslayer
    watcher = Sentinel()

    def loop():
        time.sleep(20)                       # let JARVIS finish starting
        while True:
            try:
                for text in watcher.check(oslayer.battery(), disk_free_gb(), time.time()):
                    emit(text)
                    oslayer.notify(text, "JARVIS")
                    if settings.get("alerts.speak", True):
                        speak(text)
            except Exception:
                pass
            time.sleep(60)

    thread = threading.Thread(target=loop, name="jarvis-sentinel", daemon=True)
    thread.start()
    return thread
