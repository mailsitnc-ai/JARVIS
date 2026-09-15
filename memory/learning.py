"""The learning loop: JARVIS improves from use instead of repeating mistakes.

Two things are learned and persisted:

  route hints  keyword-set -> skill. Learned when a paraphrase with no trigger still resolved to an
               existing skill (a reuse), or when you teach one explicitly. Used as extra routing
               candidates so the same phrasing routes straight there next time, no rebuild.

  lessons      short notes from failures (a build that wouldn't verify, a skill that crashed) and
               from what you teach. Recent lessons are fed into the skill-drafting prompt so the
               model stops repeating the same mistake.

Everything is JSON on disk under memory/data, so it survives restarts. Kept deliberately simple and
deterministic: a small model can't be trusted to learn safely, but "this phrase means that skill" and
"avoid this past mistake" are safe, useful signals.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the and or to of in on for is are was be it this that what whats how do does can could you "
    "me my i please jarvis with from at by as your his her their our do can could would will just "
    "on off up down some any all it's its".split()
)


def keywords(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(str(text).lower()) if t not in _STOP and len(t) > 2]


class LearningStore:
    def __init__(self, directory: Path, min_overlap: float = 0.6):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lessons_path = self.directory / "lessons.jsonl"
        self.hints_path = self.directory / "route_hints.json"
        self.min_overlap = min_overlap
        self._lock = threading.RLock()

    # ---- lessons -------------------------------------------------------------------------------

    def add_lesson(self, kind: str, text: str, request: str | None = None) -> None:
        text = " ".join(str(text).split())[:300]
        if not text:
            return
        with self._lock:
            try:
                with self.lessons_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind,
                                         "text": text, "request": request}, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def _read_lessons(self) -> list[dict]:
        try:
            lines = self.lessons_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def recent_lessons(self, limit: int = 5, kinds: tuple[str, ...] | None = None) -> list[str]:
        lessons = self._read_lessons()
        if kinds:
            lessons = [e for e in lessons if e.get("kind") in kinds]
        seen, texts = set(), []
        for entry in reversed(lessons):  # newest first, de-duplicated
            text = entry.get("text", "")
            if text and text not in seen:
                seen.add(text)
                texts.append(text)
            if len(texts) >= limit:
                break
        return list(reversed(texts))

    def lesson_count(self) -> int:
        return len(self._read_lessons())

    # ---- route hints ---------------------------------------------------------------------------

    def _read_hints(self) -> list[dict]:
        try:
            data = json.loads(self.hints_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write_hints(self, hints: list[dict]) -> None:
        tmp = self.hints_path.with_name(self.hints_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(hints, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.hints_path)
        except OSError:
            pass

    def add_route_hint(self, phrase: str, skill: str, source: str = "reuse") -> bool:
        words = keywords(phrase)
        if not words or not skill:
            return False
        with self._lock:
            hints = self._read_hints()
            for hint in hints:  # already known for this skill?
                if hint.get("skill") == skill and set(hint.get("keywords", [])) == set(words):
                    return False
            hints.append({"skill": skill, "keywords": words, "phrase": " ".join(str(phrase).split())[:120],
                          "source": source, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self._write_hints(hints)
        return True

    def hinted_skills(self, request: str) -> list[str]:
        """Skills learned to match this phrasing, best overlap first."""
        want = set(keywords(request))
        if not want:
            return []
        scored: list[tuple[float, str]] = []
        for hint in self._read_hints():
            have = set(hint.get("keywords", []))
            if not have:
                continue
            overlap = len(want & have) / len(have)  # fraction of the learned phrase's words present
            if overlap >= self.min_overlap:
                scored.append((overlap, hint["skill"]))
        scored.sort(reverse=True)
        ordered: list[str] = []
        for _score, skill in scored:
            if skill not in ordered:
                ordered.append(skill)
        return ordered

    def teach(self, phrase: str, skill: str) -> bool:
        added = self.add_route_hint(phrase, skill, source="taught")
        self.add_lesson("taught", f"When the user says something like '{phrase}', use the '{skill}' skill.")
        return added

    def hints(self) -> list[dict]:
        return self._read_hints()

    def forget_skill(self, skill: str) -> int:
        """Drop hints pointing at a skill (e.g. when it's deleted). Returns how many were removed."""
        with self._lock:
            hints = self._read_hints()
            kept = [h for h in hints if h.get("skill") != skill]
            removed = len(hints) - len(kept)
            if removed:
                self._write_hints(kept)
        return removed
