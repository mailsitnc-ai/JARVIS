"""JSON-lines memory with lightweight local similarity recall (TF-IDF cosine, no dependencies).

memory/data/interactions.jsonl   one line per request and reply
memory/data/evolution_log.jsonl  every analyze / draft / sandbox / install event
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the and or to of in on for is are was be it this that what how do does can could you "
    "me my i please jarvis with from at by as".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class MemoryStore:
    def __init__(self, directory: Path, max_interactions: int = 5000):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "interactions.jsonl"
        self.evolution_path = self.directory / "evolution_log.jsonl"
        self.max_interactions = max(100, int(max_interactions))
        self._lock = threading.Lock()
        self._count: int | None = None

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    def _append(self, path: Path, entry: dict) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record(self, request: str, response: str, skill: str | None = None, route: str | None = None,
               provider: str | None = None) -> None:
        entry = {"ts": self._now(), "request": request, "response": (response or "")[:4000],
                 "skill": skill, "route": route, "provider": provider}
        with self._lock:
            self._append(self.path, entry)
            if self._count is None:
                self._count = len(self._read(self.path))
            else:
                self._count += 1
            if self._count > self.max_interactions + 200:
                keep = self._read(self.path)[-self.max_interactions:]
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in keep), encoding="utf-8")
                tmp.replace(self.path)
                self._count = len(keep)

    def log_evolution(self, event: dict) -> None:
        with self._lock:
            self._append(self.evolution_path, {"ts": self._now(), **event})

    @staticmethod
    def _read(path: Path) -> list[dict]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        entries = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def interactions(self) -> list[dict]:
        return self._read(self.path)

    def evolution_events(self) -> list[dict]:
        return self._read(self.evolution_path)

    def recent(self, n: int = 5) -> list[dict]:
        return self.interactions()[-n:]

    def recall(self, query: str, k: int = 3) -> list[dict]:
        query_counts = Counter(tokenize(query))
        docs = self.interactions()
        if not query_counts or not docs or k <= 0:
            return []
        doc_counts = [Counter(tokenize(f"{d.get('request', '')} {str(d.get('response', ''))[:500]}")) for d in docs]
        doc_freq: Counter = Counter()
        for counts in doc_counts:
            doc_freq.update(counts.keys())
        n = len(docs)

        def weigh(counts: Counter) -> dict[str, float]:
            return {t: c * (math.log((n + 1) / (doc_freq[t] + 1)) + 1.0) for t, c in counts.items()}

        def norm(vec: dict[str, float]) -> float:
            return math.sqrt(sum(v * v for v in vec.values())) or 1.0

        qv = weigh(query_counts)
        qn = norm(qv)
        scored = []
        for doc, counts in zip(docs, doc_counts):
            dv = weigh(counts)
            dot = sum(w * dv[t] for t, w in qv.items() if t in dv)
            if dot > 0:
                scored.append((dot / (qn * norm(dv)), doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in scored[:k]]
