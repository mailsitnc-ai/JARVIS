"""Small system queries used by doctor. Cross-platform via core.oslayer (Windows/macOS/Linux)."""
from __future__ import annotations

from dataclasses import dataclass

from .oslayer import memory_gb


@dataclass
class MemoryStatus:
    total_gb: float
    available_gb: float
    load_percent: int


def memory_status() -> MemoryStatus | None:
    values = memory_gb()
    if values is None:
        return None
    total, available, load = values
    return MemoryStatus(total, available, load)
