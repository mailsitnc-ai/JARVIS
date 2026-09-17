"""Shared machinery for generating and REPAIRING Python so it actually runs.

Used by the write_file skill (new code) and the fix_code skill (repairing existing code): syntax check,
install missing packages, then a real smoke run in a subprocess, with a bounded model-driven repair loop.
Only code that starts cleanly is accepted. Kept here (not in a skill) so both skills share one implementation.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SYSTEM = ("You generate the raw contents of a single file. Output ONLY the file's contents - no "
          "explanation, no commentary, no markdown code fences. Write a COMPLETE, RUNNABLE program: "
          "every import present, no placeholders, no '...', no TODOs, nothing left for the user to fill "
          "in. Prefer the Python standard library; use a third-party package only if truly needed. For "
          "a GUI app use tkinter. Put runnable code under `if __name__ == \"__main__\":`.")

# Exceptions that mean the file is genuinely broken (they fire at startup, before any user input), so
# they're worth a repair. Errors like EOFError/ValueError can come from our blank smoke-test input, so
# they're NOT treated as failures - we don't want to "fix" a perfectly good interactive app.
STRUCTURAL = ("ModuleNotFoundError", "ImportError", "NameError", "AttributeError", "IndentationError",
              "SyntaxError", "TabError", "UnboundLocalError")
GUI = re.compile(r"\b(?:tkinter|PyQt5|PyQt6|PySide2|PySide6|pygame|kivy|wx|turtle)\b|\.mainloop\s*\(")
_FENCE = re.compile(r"^\s*```[\w+-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def unfence(text: str) -> str:
    fence = _FENCE.match(str(text).strip())
    return (fence.group(1) if fence else str(text)).strip()


def install_missing(content: str, context) -> list:
    """Install any third-party packages the code imports so it can actually run. Returns a note list."""
    from evolution_engine.sandbox import MODULE_TO_PIP

    stdlib = getattr(sys, "stdlib_module_names", set())
    notes = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return notes
    missing = set()
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods = [node.module.split(".")[0]]
        for m in mods:
            if m and m not in stdlib and importlib.util.find_spec(m) is None:
                missing.add(m)
    # Windows has no system timezone database, so stdlib zoneinfo needs the data-only 'tzdata' package -
    # without it, ZoneInfo('Asia/Tokyo') fails and world-clock apps report "no timezone data".
    if re.search(r"\bzoneinfo\b", content) and importlib.util.find_spec("tzdata") is None:
        missing.add("tzdata")
    actions = context.get("actions")
    for m in sorted(missing):
        try:
            actions.install_package(MODULE_TO_PIP.get(m, m))
            notes.append(f"installed {m}")
            importlib.invalidate_caches()
        except Exception:
            notes.append(f"needs '{m}' (couldn't install)")
    return notes


def smoke_run(content: str):
    """Actually run the code briefly. Returns (ok, error_text). A GUI/loop app still alive at the timeout
    counts as OK (it started). Only a structural exception at startup is a failure."""
    gui = bool(GUI.search(content))
    timeout = 3.0 if gui else 6.0
    tmp = Path(tempfile.gettempdir()) / f"jarvis_smoke_{abs(hash(content)) % 10**8}.py"
    try:
        tmp.write_text(content, encoding="utf-8")
    except OSError:
        return True, None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([sys.executable, str(tmp)], input="\n\n\n\n\n", text=True,
                              capture_output=True, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        return True, None
    except OSError:
        return True, None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if proc.returncode == 0:
        return True, None
    stderr = (proc.stderr or "").strip()
    last = stderr.splitlines()[-1] if stderr else ""
    exc_type = last.split(":", 1)[0].strip()
    if exc_type in STRUCTURAL:
        return False, stderr[-1500:]
    return True, None


def verify_python(name: str, content: str, context):
    """Syntax-fix, install packages, smoke-run, repair with the model on failure. Returns (content, note)."""
    ask = context.get("llm")
    emit = context.get("emit") or (lambda *a: None)
    notes = []
    for attempt in range(4):
        try:
            ast.parse(content)
        except SyntaxError as exc:
            if ask is None or attempt == 3:
                return content, " (heads up: a syntax error remains)"
            content = unfence(str(ask(
                f"This Python file {name!r} has a syntax error: {exc.msg} at line {exc.lineno}. Return the "
                f"corrected COMPLETE file, code only:\n{content}", system=SYSTEM, temperature=0.1, max_tokens=3000)))
            continue
        notes = install_missing(content, context)
        ok, error = smoke_run(content)
        if ok:
            tag = "verified it runs"
            if notes:
                tag += "; " + "; ".join(notes)
            return content, f" ({tag})"
        if ask is None or attempt == 3:
            tail = (error or "").splitlines()[-1] if error else "unknown"
            return content, f" (heads up: it still errors on start - {tail})"
        emit("repair", f"'{name}' crashed on start; fixing it and re-testing...")
        content = unfence(str(ask(
            f"This Python program {name!r} fails when run. Fix the bug so it starts and runs correctly. "
            f"Return the COMPLETE corrected file, code only - no explanation.\n\n"
            f"--- error ---\n{error}\n\n--- current file ---\n{content}",
            system=SYSTEM, temperature=0.1, max_tokens=3000)))
    return content, ""
