"""Generate a program/script/app or text file with the LLM, PROVE it runs, then save it.

This is how JARVIS "writes code" and "makes apps". The important part is verification: a generated
Python file isn't just syntax-checked, it is actually executed in a short smoke test. Startup faults
that used to only show up when you opened the app - NameError, ImportError, a bad attribute, a wrong
call - are caught here and repaired by the model in a loop before the file is ever written. Missing
packages are installed. Only code that starts cleanly gets saved.
"""
import re
import sys
from pathlib import Path

from core.pyverify import GUI as _GUI
from core.pyverify import SYSTEM as _SYSTEM
from core.pyverify import install_missing as _install_missing
from core.pyverify import smoke_run as _smoke_run
from core.pyverify import unfence as _unfence
from core.pyverify import verify_python as _verify_python

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
_CODE_EXT = {"py", "pyw", "html", "htm", "js", "ts", "css", "java", "c", "cpp", "cs", "rb", "go", "rs",
             "php", "sh", "ps1", "bat", "json", "md", "txt", "csv"}


def _auto_name(request):
    """Invent a sensible filename when the user didn't give one ('make a calculator app' -> calculator.py)."""
    low = request.strip().lower()
    ext = "py"
    if re.search(r"\b(?:html|web\s*page|website|landing\s*page)\b", low):
        ext = "html"
    elif re.search(r"\b(?:document|note|essay|report|letter|story|readme|paper|list)\b", low):
        ext = "txt"
    core = re.sub(r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:make|create|build|write|generate|code|"
                  r"design|develop)\s+(?:me\s+)?(?:the|an|a)?\s+", "", low)
    core = re.split(r"\b(?:that|which|to|for|with|so|in|using)\b", core)[0]
    core = re.sub(r"\b(?:app|application|program|script|game|tool|python|simple|basic|little)\b", " ", core)
    words = re.findall(r"[a-z0-9]+", core)[:3]
    return f"{'_'.join(words) or 'app'}.{ext}"


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
        name = _auto_name(request)   # no filename given -> invent one instead of nagging
    return folder / name, name


def _describe(request):
    """The part of the request that says WHAT to write."""
    text = re.sub(r"\b(?:and\s+)?save\s+(?:it|this|that)?\s*(?:as|to|in)\b.*$", "", request, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:named|called)\s+[\"']?[\w .\-]+[\"']?", "", text, flags=re.IGNORECASE)
    return text.strip(" .") or request


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
    # A Google Doc / Sheet / Drive request belongs to the google_docs/google_sheets skills, not a local file.
    if re.search(r"\bgoogle\s+docs?\b|\bgoogle\s+sheets?\b|\bspreadsheet\b|"
                 r"\b(?:in|to|on|from)\s+(?:my\s+)?(?:google\s+)?drive\b", request, re.IGNORECASE):
        return None
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
