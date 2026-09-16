"""The agenda: JARVIS's own to-do list, so it can act on its own initiative over time.

Each entry is a task with a trigger (once at a time / every N seconds / daily at HH:MM). A background
scheduler in the daemon runs due tasks by handing their prompt to the orchestrator - but only while
autonomy is on, because an unattended action can't stop to ask permission. Tasks persist across restarts
in %APPDATA%\\JARVIS\\agenda.json. A special kind "reflection" triggers JARVIS's self-review instead of a
plain request. This module is pure logic (scheduling is deterministic, wall-clock injected) so it's testable.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import uuid

from .config import user_dir, write_json_atomic

_FMT = "%Y-%m-%d %H:%M:%S"


def now() -> _dt.datetime:
    return _dt.datetime.now()


def to_iso(dt: _dt.datetime) -> str:
    return dt.strftime(_FMT)


def from_iso(s: str) -> _dt.datetime:
    return _dt.datetime.strptime(s, _FMT)


def next_run(trigger: dict, frm: _dt.datetime) -> _dt.datetime:
    """When a task with this trigger should next fire, given it's being scheduled from `frm`."""
    kind = str(trigger.get("type", "interval"))
    if kind == "once":
        at = trigger.get("at")
        return from_iso(at) if at else frm
    if kind == "daily":
        try:
            hh, mm = (int(x) for x in str(trigger.get("time", "08:00")).split(":"))
        except ValueError:
            hh, mm = 8, 0
        cand = frm.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= frm:
            cand += _dt.timedelta(days=1)
        return cand
    seconds = max(30, int(trigger.get("seconds", 3600)))   # interval (default hourly, floor 30s)
    return frm + _dt.timedelta(seconds=seconds)


def describe_trigger(trigger: dict) -> str:
    kind = str(trigger.get("type", "interval"))
    if kind == "once":
        return f"once at {trigger.get('at', '?')}"
    if kind == "daily":
        return f"daily at {trigger.get('time', '08:00')}"
    secs = int(trigger.get("seconds", 3600))
    return f"every {secs // 60} min" if secs >= 60 else f"every {secs}s"


class AgendaStore:
    def __init__(self, path=None):
        self.path = path or (user_dir() / "agenda.json")
        self._lock = threading.RLock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("tasks"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"enabled": True, "tasks": []}

    def _write(self, data: dict) -> None:
        try:
            write_json_atomic(self.path, data)
        except OSError:
            pass

    def enabled(self) -> bool:
        with self._lock:
            return bool(self._read().get("enabled", True))

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            data = self._read()
            data["enabled"] = bool(on)
            self._write(data)

    def list(self) -> list[dict]:
        with self._lock:
            return list(self._read().get("tasks", []))

    def get(self, task_id: str) -> dict | None:
        return next((t for t in self.list() if t["id"] == task_id), None)

    def add(self, title: str, prompt: str, trigger: dict, kind: str = "task", enabled: bool = True,
            at: _dt.datetime | None = None) -> dict:
        with self._lock:
            data = self._read()
            task = {
                "id": uuid.uuid4().hex[:8],
                "title": title.strip() or prompt.strip()[:40] or "task",
                "prompt": prompt.strip(),
                "kind": kind,
                "trigger": trigger,
                "enabled": bool(enabled),
                "created": to_iso(now()),
                "next_run": to_iso(next_run(trigger, at or now())),
                "last_run": None,
                "last_result": None,
                "runs": 0,
            }
            data.setdefault("tasks", []).append(task)
            self._write(data)
            return task

    def remove(self, task_id: str) -> bool:
        with self._lock:
            data = self._read()
            before = len(data.get("tasks", []))
            data["tasks"] = [t for t in data.get("tasks", []) if t["id"] != task_id]
            self._write(data)
            return len(data["tasks"]) < before

    def set_task_enabled(self, task_id: str, on: bool) -> bool:
        return self._update(task_id, enabled=bool(on))

    def _update(self, task_id: str, **fields) -> bool:
        with self._lock:
            data = self._read()
            found = False
            for t in data.get("tasks", []):
                if t["id"] == task_id:
                    t.update(fields)
                    found = True
            if found:
                self._write(data)
            return found

    def due(self, at: _dt.datetime | None = None) -> list[dict]:
        at = at or now()
        out = []
        for t in self.list():
            if not t.get("enabled"):
                continue
            try:
                if from_iso(t["next_run"]) <= at:
                    out.append(t)
            except (KeyError, ValueError):
                out.append(t)
        return out

    def mark_ran(self, task_id: str, result: str, at: _dt.datetime | None = None) -> None:
        at = at or now()
        with self._lock:
            data = self._read()
            for t in data.get("tasks", []):
                if t["id"] != task_id:
                    continue
                t["last_run"] = to_iso(at)
                t["last_result"] = str(result)[:300]
                t["runs"] = int(t.get("runs", 0)) + 1
                if str(t.get("trigger", {}).get("type")) == "once":
                    t["enabled"] = False                    # a one-shot won't fire again
                else:
                    t["next_run"] = to_iso(next_run(t["trigger"], at))
            self._write(data)
