"""Conversation focus: what "it", "that", "the photo", "the file" refer to right now.

Every turn, the action broker records the concrete things it touched - the file it just wrote, the
photo it captured, the URL it opened, the app it launched, the folder it revealed - into a small
JSON store next to the other per-user state. The reference resolver (core/refs.py) then reads this
to turn a short follow-up like "open it" or "what's in the picture" into the concrete path or URL,
so JARVIS keeps context across messages instead of forgetting the subject the moment you stop naming
it. Persisted, so it also survives a restart.

Slots:
  file    - the last file created / opened (any kind)
  image   - the last image specifically (webcam photo, screenshot) - also mirrored into 'file'
  url     - the last web address opened
  app     - the last app launched
  folder  - the last folder opened / revealed
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SLOTS = ("file", "image", "url", "app", "folder")
_PATH_SLOTS = ("file", "image", "folder")  # slots whose value is a filesystem path we can existence-check


class Focus:
    def __init__(self, state_dir: Path | str | None):
        self.state_dir = Path(state_dir) if state_dir else None

    def _path(self) -> Path | None:
        return (self.state_dir / "focus.json") if self.state_dir else None

    def _load(self) -> dict:
        path = self._path()
        if not path or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        path = self._path()
        if not path:
            return
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    def remember(self, slot: str, value) -> None:
        """Record that `slot` now points at `value` (an image also becomes the current file)."""
        if slot not in SLOTS or not value:
            return
        data = self._load()
        now = time.time()
        data[slot] = {"value": str(value), "ts": now}
        if slot == "image":
            data["file"] = {"value": str(value), "ts": now}  # "open it" after a photo should open the photo
        self._save(data)

    def get(self, slot: str) -> str | None:
        """The current value for a slot, or None. Path slots must still exist on disk."""
        entry = self._load().get(slot)
        value = entry.get("value") if isinstance(entry, dict) else None
        if not value:
            return None
        if slot in _PATH_SLOTS and not Path(value).exists():
            return None
        return value

    def recent(self, slots=SLOTS) -> list[tuple[str, str]]:
        """(slot, value) for the wanted slots that still resolve, newest first."""
        data = self._load()
        rows = []
        for slot in slots:
            entry = data.get(slot)
            if not isinstance(entry, dict) or not entry.get("value"):
                continue
            value = entry["value"]
            if slot in _PATH_SLOTS and not Path(value).exists():
                continue
            rows.append((entry.get("ts", 0.0), slot, value))
        rows.sort(reverse=True)
        return [(slot, value) for _ts, slot, value in rows]

    def most_recent(self, slots=SLOTS) -> str | None:
        """The single newest still-valid referent among `slots`, or None."""
        rows = self.recent(slots)
        return rows[0][1] if rows else None

    def snapshot(self) -> dict:
        """A plain {slot: value} of everything that still resolves, for handing to skills."""
        return {slot: value for slot, value in self.recent()}
