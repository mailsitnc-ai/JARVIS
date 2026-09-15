"""The skill contract, dependency-free so the sandbox harness can load it by path.

A skill is a .py file in /skills that defines:

    SKILL = {
        "name": "temperature_converter",          # unique, snake_case
        "description": "One sentence.",
        "triggers": [r"\\bcelsius\\b", ...],       # case-insensitive regexes
        "version": 1,
        "origin": "builtin" | "evolved",
    }

    def run(request: str, context: dict) -> str | None

run() returns the answer as text. A built-in skill may return None to decline a request
its triggers caught by accident; JARVIS then treats the request as unhandled.
"""
from __future__ import annotations

import inspect
import re

REQUIRED_KEYS = ("name", "description", "triggers")


def contract_problems(module) -> list[str]:
    problems: list[str] = []
    meta = getattr(module, "SKILL", None)
    if not isinstance(meta, dict):
        return ["module must define a SKILL dict"]

    for key in REQUIRED_KEYS:
        if key not in meta:
            problems.append(f"SKILL is missing '{key}'")

    name = meta.get("name")
    if "name" in meta and (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,59}", name)):
        problems.append("SKILL['name'] must be a snake_case string")

    triggers = meta.get("triggers")
    if not isinstance(triggers, (list, tuple)) or not triggers or not all(isinstance(t, str) for t in triggers):
        problems.append("SKILL['triggers'] must be a non-empty list of regex strings")
    else:
        for trigger in triggers:
            try:
                re.compile(trigger, re.IGNORECASE)
            except re.error as exc:
                problems.append(f"invalid trigger {trigger!r}: {exc}")

    requires = meta.get("requires")
    if requires is not None and (not isinstance(requires, (list, tuple)) or not all(isinstance(r, str) for r in requires)):
        problems.append("SKILL['requires'] must be a list of pip package names")

    run = getattr(module, "run", None)
    if not callable(run):
        problems.append("module must define run(request, context)")
    else:
        try:
            params = list(inspect.signature(run).parameters.values())
        except (TypeError, ValueError):
            params = None
        if params is not None:
            positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            if len(positional) < 2 and not any(p.kind == p.VAR_POSITIONAL for p in params):
                problems.append("run must accept two arguments: (request, context)")
    return problems
