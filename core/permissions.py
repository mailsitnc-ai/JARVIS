"""Per-capability permission grants for JARVIS's controlled access to the laptop.

Each capability starts at "ask": JARVIS may only act after you approve, per action. You can
grant "allow" (act without asking) or "deny" (never) — from the panel's approval buttons, or
`jarvis permissions <capability> allow|deny|ask`. Grants persist in %APPDATA%\\JARVIS\\permissions.json.

This is the gate that makes "full but controlled access" real: the capabilities exist and work,
but nothing runs until you say so.

Autonomy mode is the single "unleash it" switch (`jarvis autonomy on`). While it is on, every
capability reads as "allow" regardless of the per-capability grants: JARVIS acts without stopping
to ask, plan review is skipped, and evolved skills may use risky calls. It is off by default and
persists in the same file, so it survives restarts until you turn it back off. Turning it off
restores whatever per-capability grants you had.
"""
from __future__ import annotations

import json
import threading

from .config import user_dir, write_json_atomic

CAPABILITIES = {
    "open": "Open apps, files, folders and websites",
    "screen": "Capture the screen",
    "read_files": "Read files and list folders",
    "write_files": "Create or change files",
    "run_command": "Run programs and commands",
    "network": "Access the internet (fetch web pages and APIs)",
    "packages": "Install Python packages (pip)",
    "google": "Read your Google Drive and Gmail",
    "notify": "Show popups and desktop notifications",
    "self_edit": "Rewrite JARVIS's own source code",
    "camera": "Use the webcam (only when you ask)",
    "browser": "Control the web browser - read pages, click and type (Chrome DevTools)",
}
STATES = ("ask", "allow", "deny")
DEFAULT_STATE = "ask"
# Privacy-sensitive: autonomy does NOT blanket-allow these - they stay at their own setting so the
# camera never fires from a background/scheduled task, only from an explicit command you approve.
SENSITIVE = {"camera"}


class PermissionRegistry:
    def __init__(self, path=None):
        self.path = path or (user_dir() / "permissions.json")
        self._lock = threading.RLock()
        self._mtime = -1.0
        self._autonomy = False
        self._grants = self._read()

    def _read(self) -> dict:
        self._autonomy = False
        try:
            self._mtime = self.path.stat().st_mtime
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        self._autonomy = bool(data.get("autonomy"))
        return {k: v for k, v in data.items() if k in CAPABILITIES and v in STATES}

    def _refresh(self) -> None:
        # Pick up changes made in another process (e.g. `jarvis permissions ...` while the panel runs).
        try:
            if self.path.stat().st_mtime != self._mtime:
                self._grants = self._read()
        except OSError:
            pass

    def state(self, capability: str) -> str:
        with self._lock:
            self._refresh()
            if self._autonomy and capability not in SENSITIVE:
                return "allow"  # unleashed: every non-sensitive capability is granted while autonomy is on
            return self._grants.get(capability, DEFAULT_STATE)

    def autonomy(self) -> bool:
        with self._lock:
            self._refresh()
            return self._autonomy

    def set_autonomy(self, on: bool) -> None:
        with self._lock:
            self._autonomy = bool(on)
            self._save()

    def set(self, capability: str, state: str) -> None:
        if capability not in CAPABILITIES:
            raise KeyError(capability)
        if state not in STATES:
            raise ValueError(state)
        with self._lock:
            if state == DEFAULT_STATE:
                self._grants.pop(capability, None)
            else:
                self._grants[capability] = state
            self._save()

    def reset(self, capability: str | None = None) -> None:
        with self._lock:
            if capability is None:
                self._grants = {}
            else:
                self._grants.pop(capability, None)
            self._save()

    def all(self) -> dict:
        return {cap: self.state(cap) for cap in CAPABILITIES}

    def _save(self) -> None:
        data = dict(self._grants)
        if self._autonomy:
            data["autonomy"] = True
        try:
            write_json_atomic(self.path, data)
        except OSError:
            pass
