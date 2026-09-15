"""Discovers skills in /skills and hot-reloads them when files are added, changed or removed."""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .config import SKILLS_DIR
from .contract import contract_problems


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[re.Pattern]
    module: ModuleType
    path: Path
    origin: str = "builtin"

    @property
    def trigger_sources(self) -> list[str]:
        return [t.pattern for t in self.triggers]

    def score(self, text: str) -> tuple[int, int]:
        """(number of triggers that match, total matched characters)."""
        hits = chars = 0
        for pattern in self.triggers:
            match = pattern.search(text)
            if match:
                hits += 1
                chars += len(match.group(0))
        return hits, chars

    def run(self, request: str, context: dict):
        return self.module.run(request, context)


class SkillRegistry:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or SKILLS_DIR)
        self.skills: dict[str, Skill] = {}
        self.errors: dict[str, str] = {}
        self._stamps: dict[Path, tuple[int, int]] = {}
        self._names: dict[Path, str] = {}
        self._lock = threading.RLock()
        self._generation = 0

    def reload(self) -> list[str]:
        """Load new or changed skill files and drop deleted ones. Returns the names that changed."""
        with self._lock:
            importlib.invalidate_caches()
            self.directory.mkdir(parents=True, exist_ok=True)
            current = {}
            for path in sorted(self.directory.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                stat = path.stat()
                current[path.resolve()] = (stat.st_mtime_ns, stat.st_size)

            changed: list[str] = []
            for path in list(self._stamps):
                if path not in current:
                    self._stamps.pop(path)
                    self.errors.pop(path.name, None)
                    name = self._forget(path)
                    if name:
                        changed.append(name)

            for path, stamp in current.items():
                if self._stamps.get(path) == stamp:
                    continue
                self._stamps[path] = stamp
                self._forget(path)
                try:
                    skill = self._load(path)
                except Exception as exc:  # a broken skill must never take JARVIS down
                    self.errors[path.name] = f"{type(exc).__name__}: {exc}"
                    continue
                existing = self.skills.get(skill.name)
                if existing is not None and existing.path != path:
                    self.errors[path.name] = f"duplicate skill name '{skill.name}' (already defined in {existing.path.name})"
                    sys.modules.pop(skill.module.__name__, None)
                    continue
                self.errors.pop(path.name, None)
                self.skills[skill.name] = skill
                self._names[path] = skill.name
                changed.append(skill.name)
            return changed

    load_all = reload

    def _forget(self, path: Path) -> str | None:
        name = self._names.pop(path, None)
        if name and name in self.skills:
            sys.modules.pop(self.skills.pop(name).module.__name__, None)
        return name

    def _load(self, path: Path) -> Skill:
        self._generation += 1
        module_name = f"jarvis_skill_{path.stem}_{self._generation}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            problems = contract_problems(module)
            if problems:
                raise ValueError("; ".join(problems))
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        meta = module.SKILL
        return Skill(
            name=meta["name"],
            description=str(meta["description"]),
            triggers=[re.compile(t, re.IGNORECASE) for t in meta["triggers"]],
            module=module,
            path=path,
            origin=str(meta.get("origin", "builtin")),
        )

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def match(self, text: str) -> list[tuple[Skill, tuple[int, int]]]:
        with self._lock:
            scored = [(skill, skill.score(text)) for skill in self.skills.values()]
        hits = [(skill, score) for skill, score in scored if score[0] > 0]
        return sorted(hits, key=lambda item: item[1], reverse=True)

    def summaries(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in sorted(self.skills.values(), key=lambda s: s.name))
