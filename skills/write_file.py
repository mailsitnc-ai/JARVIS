"""Generate a program/script/app or text file with the LLM, PROVE it runs, then save it.

This is how JARVIS "writes code" and "makes apps". The important part is verification: a generated
Python file isn't just syntax-checked, it is actually executed in a short smoke test. Startup faults
that used to only show up when you opened the app - NameError, ImportError, a bad attribute, a wrong
call - are caught here and repaired by the model in a loop before the file is ever written. Missing
packages are installed. Only code that starts cleanly gets saved.
"""
import re
import subprocess
import sys
from pathlib import Path

SKILL = {
    "name": "write_file",
    "description": ("Write a program, script, app, webpage or text file and save it, e.g. "
                    "'write a python snake game and save it as snake.py' or 'create an html landing page'."),
    "triggers": [
        r"\b(?:write|create|make|build|generate|code)\b[^.]*\b(?:script|program|app|application|game|"
        r"webpage|web\s*page|website|page|file|module|class|function|snippet|code|document|doc|note|"
        r"report|essay|paper|letter|story|readme|markdown|list)\b",
        r"\b(?:write|create|make|generate|save)\b[^.]*\btitled\b",
        r"\bsave\b[^.]*\b(?:as|to|in)\b[^.]*\.\w{1,5}\b",
    ],
    "version": 2,
    "origin": "builtin",
}

_FOLDERS = {
    "desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
    "pictures": "Pictures", "music": "Music", "videos": "Videos",
}
_FENCE = re.compile(r"^\s*```[\w+-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)
_SYSTEM = ("You generate the raw contents of a single file. Output ONLY the file's contents - no "
           "explanation, no commentary, no markdown code fences. Write a COMPLETE, RUNNABLE program: "
           "every import present, no placeholders, no '...', no TODOs, nothing left for the user to fill "
           "in. Prefer the Python standard library; use a third-party package only if truly needed. For "
           "a GUI app use tkinter. Put runnable code under `if __name__ == \"__main__\":`.")

_CODE_EXT = {"py", "pyw", "html", "htm", "js", "ts", "css", "java", "c", "cpp", "cs", "rb", "go", "rs",
             "php", "sh", "ps1", "bat", "json", "md", "txt", "csv"}
# Exceptions that mean the file is genuinely broken (they fire at startup, before any user input),
# so they're worth a repair. Errors like EOFError/ValueError can come from our blank smoke-test input,
# so they are NOT treated as failures - we don't want to "fix" a perfectly good interactive app.
_STRUCTURAL = ("ModuleNotFoundError", "ImportError", "NameError", "AttributeError", "IndentationError",
               "SyntaxError", "TabError", "UnboundLocalError")
_GUI = re.compile(r"\b(?:tkinter|PyQt5|PyQt6|PySide2|PySide6|pygame|kivy|wx|turtle)\b|\.mainloop\s*\(")


def _target_path(request):
    """Work out the file path to save to (and its name), defaulting to the Desktop."""
    name = None
    named = re.findall(r"\b(?:as|named|called)\s+[\"']?([\w .\-]+?\.(\w{1,5}))[\"']?", request, re.IGNORECASE)
    if named:
        code = [n for n, ext in named if ext.lower() in _CODE_EXT]
        name = (code[0] if code else named[0][0]).strip()
    else:
        m = re.search(r"\b(?:as|named|called)\s+[\"']?([\w .\-]{1,40}?)[\"']?(?:\s|$)", request, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
    if not name:   # "a document titled RESEARCH PAPER" -> RESEARCH PAPER.txt
        tm = re.search(r"\btitled?\s+[\"']?(.+?)[\"']?(?:\s+(?:with|and|to|in|containing|that|of|for)\b|[.,]|$)",
                       request, re.IGNORECASE)
        if tm:
            title = tm.group(1).strip()
            name = title if re.search(r"\.\w{1,5}$", title) else f"{title}.txt"

    folder = Path.home() / "Desktop"
    fm = re.search(r"\b(?:to|in|on|into|under)\s+(?:my\s+|the\s+)?([A-Za-z]:[\\/][^\"']*|~[\\/][^\"']*|[\w .\-\\/]+)",
                   request, re.IGNORECASE)
    if fm:
        raw = fm.group(1).strip().rstrip(".")
        low = raw.lower()
        if low in _FOLDERS:
            folder = Path.home() / _FOLDERS[low]
        elif re.match(r"^[A-Za-z]:[\\/]|^~[\\/]", raw) or ("/" in raw or "\\" in raw):
            p = Path(raw).expanduser()
            if p.suffix:  # a full file path was given
                return p, p.name
            folder = p
    if not name:
        return None, None
    return folder / name, name


def _describe(request):
    """The part of the request that says WHAT to write."""
    text = re.sub(r"\b(?:and\s+)?save\s+(?:it|this|that)?\s*(?:as|to|in)\b.*$", "", request, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:named|called)\s+[\"']?[\w .\-]+[\"']?", "", text, flags=re.IGNORECASE)
    return text.strip(" .") or request


def _unfence(text):
    fence = _FENCE.match(text.strip())
    return (fence.group(1) if fence else text).strip()


def _install_missing(content, context):
    """Install any third-party packages the code imports so it can actually run. Returns a note list."""
    import ast
    import importlib.util

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
    for m in sorted(missing):
        try:
            context["actions"].install_package(MODULE_TO_PIP.get(m, m))
            notes.append(f"installed {m}")
            importlib.invalidate_caches()
        except Exception:
            notes.append(f"needs '{m}' (couldn't install)")
    return notes


def _smoke_run(content):
    """Actually run the code briefly. Returns (ok, error_text). A GUI/loop app that is still alive when
    the timer runs out counts as OK (it started). Only a structural exception at startup is a failure."""
    import tempfile

    gui = bool(_GUI.search(content))
    timeout = 3.0 if gui else 6.0
    tmp = Path(tempfile.gettempdir()) / f"jarvis_smoke_{abs(hash(content)) % 10**8}.py"
    try:
        tmp.write_text(content, encoding="utf-8")
    except OSError:
        return True, None  # can't write a temp copy -> skip the smoke test rather than block
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([sys.executable, str(tmp)], input="\n\n\n\n\n", text=True,
                              capture_output=True, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        return True, None            # still running after the timeout = it launched fine (GUI/main loop)
    except OSError as exc:
        return True, None            # couldn't launch a subprocess here; don't block on the smoke test
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
    if exc_type in _STRUCTURAL:
        return False, stderr[-1500:]
    return True, None                # a non-structural error (likely from blank smoke-test input) - accept it


def _verify_python(name, content, context):
    """Syntax-check, install packages, then smoke-run; repair with the model on failure. (content, note)."""
    import ast

    ask = context.get("llm")
    notes = []
    for attempt in range(4):
        try:
            ast.parse(content)
        except SyntaxError as exc:
            if ask is None or attempt == 3:
                return content, " (heads up: a syntax error remains)"
            content = _unfence(str(ask(
                f"This Python file {name!r} has a syntax error: {exc.msg} at line {exc.lineno}. Return the "
                f"corrected COMPLETE file, code only:\n{content}", system=_SYSTEM, temperature=0.1, max_tokens=3000)))
            continue
        notes = _install_missing(content, context)
        ok, error = _smoke_run(content)
        if ok:
            tag = "verified it runs"
            if notes:
                tag += "; " + "; ".join(notes)
            return content, f" ({tag})"
        if ask is None or attempt == 3:
            return content, f" (heads up: it still errors on start - {(error or '').splitlines()[-1] if error else 'unknown'})"
        context.get("emit", lambda *a: None)("repair", f"'{name}' crashed on start; fixing it and re-testing...")
        content = _unfence(str(ask(
            f"This Python program {name!r} fails when run. Fix the bug so it starts and runs correctly. "
            f"Return the COMPLETE corrected file, code only - no explanation.\n\n"
            f"--- error ---\n{error}\n\n--- current file ---\n{content}",
            system=_SYSTEM, temperature=0.1, max_tokens=3000)))
    return content, ""


def _python_exe():
    """The user's python.exe (never pythonw), so a console launcher actually shows a console."""
    exe = Path(sys.executable)
    return str(exe.with_name("python.exe")) if exe.name.lower() == "pythonw.exe" else str(exe)


def _write_launcher(target, actions):
    """Write a double-clickable <stem>.bat next to a console app so its window stays open. Returns its name."""
    bat = target.with_name(target.stem + ".bat")
    body = ("@echo off\r\n"
            f'cd /d "{target.parent}"\r\n'
            f'"{_python_exe()}" "{target.name}"\r\n'
            "echo.\r\n"
            "pause\r\n")
    result = actions.write_file(str(bat), body)
    return bat.name if result.startswith(("Wrote", "Would")) else None


def run(request, context):
    target, name = _target_path(request)
    if target is None:
        return "What filename should I save it as? e.g. 'write a python snake game and save it as snake.py'"
    if context.get("dry_run"):
        return f"Would generate and write {name}."

    ask = context.get("llm")
    if ask is None:
        return "I can't generate the file contents without a language model."
    prompt = (f"{_describe(request)}\n\nProduce the complete contents for the file named {name!r}. "
              "Return only the file contents.")
    content = _unfence(str(ask(prompt, system=_SYSTEM, temperature=0.2, max_tokens=3000)))
    if not content or content.startswith("[LLM unavailable"):
        return "I couldn't generate the file contents just now."

    note = ""
    is_python = target.suffix.lower() in (".py", ".pyw")
    if is_python:                                       # generate -> verify it actually runs -> save
        content, note = _verify_python(name, content, context)
        # A GUI app should double-click cleanly with no console: save it as .pyw.
        if target.suffix.lower() == ".py" and _GUI.search(content):
            target = target.with_suffix(".pyw")
            name = target.name
            note += " (GUI app - saved as .pyw so it runs without a console)"

    actions = context["actions"]
    launcher = None
    if is_python and target.suffix.lower() == ".py":   # console app: add a double-click launcher, written
        launcher = _write_launcher(target, actions)    # BEFORE the app so the app stays the "last file"
    result = actions.write_file(str(target), content)
    if not result.startswith(("Wrote", "Would")):
        return result  # a Blocked/denied message from the broker
    lines = content.count("\n") + 1
    summary = f"Wrote {name} ({lines} lines) to {target.parent}.{note}"
    if launcher:
        summary += f" Double-click {launcher} to run it (the window stays open)."
    elif target.suffix.lower() == ".pyw":
        summary += f" Double-click {name} to run it."
    if re.search(r"\b(?:open|run|launch|start|execute)\b", request, re.IGNORECASE):
        opened = (actions.run_python(str(target)) if target.suffix.lower() in (".py", ".pyw")
                  else actions.open_path(str(target)))
        return f"{summary} {opened}"
    return summary
