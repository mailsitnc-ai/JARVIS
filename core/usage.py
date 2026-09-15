"""Per-provider usage tracking: how many calls and tokens each model (groq / gemini / ollama) has served.

Persisted in %APPDATA%\\JARVIS\\usage.json so it survives restarts. The router records one entry per
successful completion; the CLI (`jarvis usage`) and the panel (`/usage`) read it back.
"""
from __future__ import annotations

import json
import threading
import time

from .config import user_dir, write_json_atomic

FIELDS = ("calls", "prompt_tokens", "completion_tokens")


class UsageStore:
    def __init__(self, path=None):
        self.path = path or (user_dir() / "usage.json")
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def record(self, provider: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        if not provider:
            return
        with self._lock:
            data = self._read()
            row = data.setdefault(provider, {f: 0 for f in FIELDS})
            row["calls"] = int(row.get("calls", 0)) + 1
            row["prompt_tokens"] = int(row.get("prompt_tokens", 0)) + int(prompt_tokens or 0)
            row["completion_tokens"] = int(row.get("completion_tokens", 0)) + int(completion_tokens or 0)
            row["last_used"] = time.strftime("%Y-%m-%d %H:%M")
            try:
                write_json_atomic(self.path, data)
            except OSError:
                pass

    def all(self) -> dict:
        with self._lock:
            return self._read()

    def reset(self) -> None:
        with self._lock:
            try:
                write_json_atomic(self.path, {})
            except OSError:
                pass
