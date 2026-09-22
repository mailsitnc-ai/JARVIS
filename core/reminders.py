"""Reminders and timers: 'remind me in 20 minutes to check the oven', 'remind me at 6pm to call Maa',
'set a timer for 25 minutes', 'remind me tomorrow at 9 to submit the essay'.

Stored in <data dir>/reminders.json, so they survive restarts. The JARVIS panel checks every few seconds
and, when one is due, speaks it, posts a notification and writes it in the panel. A reminder that came
due while JARVIS was off fires as soon as it's back (marked as late).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import threading
import time
import uuid

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
        "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "couple": 2,
        "few": 3}
_UNIT = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60,
         "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
         "day": 86400, "days": 86400}
_DUR = re.compile(r"(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                  r"fifteen|twenty|thirty|forty-five|forty|fifty|sixty|ninety|couple(?:\s+of)?|few)\s*"
                  r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?)\b", re.IGNORECASE)
_HALF = re.compile(r"\bhalf\s+an?\s+hour\b|\ban?\s+hour\s+and\s+a\s+half\b", re.IGNORECASE)
_CLOCK = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?!\s*(?:%|percent))", re.IGNORECASE)
_NOON = re.compile(r"\bat\s+(noon|midday|midnight)\b", re.IGNORECASE)


def _num(word: str) -> float:
    word = word.lower().replace(" of", "").strip()
    return float(word) if re.match(r"^\d", word) else float(_NUM.get(word, 1))


def parse_when(text: str, now: _dt.datetime) -> _dt.datetime | None:
    """When a reminder is for, from natural phrasing. None if no time was given."""
    low = text.lower()
    # relative: "in 20 minutes", "in an hour and a half", "for 25 min" (timers), "in 1h 30m"
    rel = re.search(r"\b(?:in|for|after)\s+(.+)$", low)
    if rel:
        seconds = 0.0
        tail = rel.group(1)
        if _HALF.search(tail):
            seconds += 5400 if "and a half" in tail else 1800
            tail = _HALF.sub(" ", tail)
        for m in _DUR.finditer(tail):
            seconds += _num(m.group(1)) * _UNIT[m.group(2).lower()]
        if seconds:
            return now + _dt.timedelta(seconds=seconds)
    # absolute: "at 6pm", "at 18:30", "tomorrow at 9", "at noon", "tonight at 8"
    day = now.date() + _dt.timedelta(days=1) if re.search(r"\btomorrow\b", low) else now.date()
    m = _NOON.search(low)
    if m:
        hh, mm = (0, 0) if m.group(1) == "midnight" else (12, 0)
        return _roll(_dt.datetime.combine(day, _dt.time(hh, mm)), now, explicit_day="tomorrow" in low)
    m = _CLOCK.search(low)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").replace(".", "")
        if hh > 23 or mm > 59:
            return None
        if ampm == "pm" and hh < 12:
            hh += 12
        elif ampm == "am" and hh == 12:
            hh = 0
        elif not ampm and hh <= 12 and re.search(r"\b(?:tonight|evening|afternoon)\b", low) and hh < 12:
            hh += 12
        target = _dt.datetime.combine(day, _dt.time(hh, mm))
        if not ampm and hh <= 12 and "tomorrow" not in low and target <= now and target + \
                _dt.timedelta(hours=12) > now:
            return target + _dt.timedelta(hours=12)           # "at 6" said at 4pm means 6pm
        return _roll(target, now, explicit_day="tomorrow" in low)
    if re.search(r"\btomorrow\b", low):
        return _dt.datetime.combine(day, _dt.time(9, 0))      # "tomorrow" alone -> 9am
    return None


def _roll(target: _dt.datetime, now: _dt.datetime, explicit_day: bool) -> _dt.datetime:
    return target if explicit_day or target > now else target + _dt.timedelta(days=1)


def parse_what(text: str) -> str:
    """The thing to be reminded of: 'remind me to X', 'remind me about X', 'reminder: X'."""
    t = text.strip()
    m = re.search(r"\b(?:remind\s+(?:me|us)|reminder|set\s+a\s+reminder|don'?t\s+let\s+me\s+forget)\b"
                  r"(?:\s+(?:to|about|that|of)\b)?\s*:?\s*(.*)$", t, re.IGNORECASE)
    what = m.group(1) if m else t
    # drop the time phrases wherever they are
    what = re.sub(r"\b(?:in|for|after)\s+(?:(?:\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|"
                  r"ten|eleven|twelve|fifteen|twenty|thirty|forty-five|forty|fifty|sixty|ninety|couple(?:\s+of)?|"
                  r"few|half\s+an?)\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|h|m|s)\b"
                  r"(?:\s*(?:and\s+)?(?:a\s+half|\d+\s*(?:minutes?|mins?|m)))?\s*)+", " ", what, flags=re.I)
    what = re.sub(r"\b(?:tomorrow|tonight|today)\b", " ", what, flags=re.I)
    what = re.sub(r"\bat\s+(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|noon|midday|midnight)\b", " ", what,
                  flags=re.I)
    what = re.sub(r"^\W*(?:to|about|that|of)\s+", "", what.strip(), flags=re.I)
    what = re.sub(r"\s+", " ", what).strip(" .,:;-")
    return what


def say_when(due: _dt.datetime, now: _dt.datetime) -> str:
    delta = (due - now).total_seconds()
    if delta < 90:
        return f"in {max(1, round(delta))} seconds"
    if delta < 3600:
        return f"in {round(delta / 60)} minutes"
    clock = due.strftime("%I:%M %p").lstrip("0")
    if due.date() == now.date():
        return f"at {clock}"
    if due.date() == now.date() + _dt.timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"on {due.strftime('%A %d %B')} at {clock}"


class ReminderStore:
    def __init__(self, path=None):
        if path is None:
            from core.oslayer import user_data_dir
            path = user_data_dir() / "reminders.json"
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> list:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _write(self, items: list) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, due: _dt.datetime, text: str, kind: str = "reminder") -> dict:
        item = {"id": uuid.uuid4().hex[:6], "due": due.timestamp(), "text": text, "kind": kind,
                "created": time.time()}
        with self._lock:
            items = self._read()
            items.append(item)
            self._write(items)
        return item

    def list(self) -> list:
        return sorted(self._read(), key=lambda i: i["due"])

    def cancel(self, match: str | None = None) -> list:
        """Remove reminders whose text contains `match` (all when None). Returns what was removed."""
        with self._lock:
            items = self._read()
            gone = [i for i in items if match is None or match.lower() in (i["text"] + " " + i["kind"]).lower()]
            self._write([i for i in items if i not in gone])
        return gone

    def pop_due(self, now_ts: float | None = None) -> list:
        now_ts = time.time() if now_ts is None else now_ts
        with self._lock:
            items = self._read()
            due = [i for i in items if i["due"] <= now_ts]
            if due:
                self._write([i for i in items if i["due"] > now_ts])
        return due


def announcement(item: dict, now_ts: float | None = None) -> str:
    late = (time.time() if now_ts is None else now_ts) - item["due"] > 120
    if item.get("kind") == "timer":
        label = f" for {item['text']}" if item.get("text") and item["text"] != "timer" else ""
        return f"Sir, your timer{label} is up." + (" (It finished while I was offline.)" if late else "")
    return f"Sir, a reminder: {item['text']}." + (" I'm afraid this one's late - I was offline." if late else "")


def start_loop(emit, speak, notify, store: ReminderStore | None = None) -> threading.Thread:
    store = store or ReminderStore()

    def loop():
        while True:
            try:
                for item in store.pop_due():
                    text = announcement(item)
                    emit(text)
                    notify(text)
                    speak(text)
            except Exception:
                pass
            time.sleep(5)

    thread = threading.Thread(target=loop, name="jarvis-reminders", daemon=True)
    thread.start()
    return thread
