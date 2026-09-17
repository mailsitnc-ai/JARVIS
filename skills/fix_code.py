"""Fix or change an existing code file: read it, repair it against the reported problem, verify, save.

This is how JARVIS fixes an app that "doesn't work". It figures out which file you mean (an explicit
path, a bare name like time.py, or the file the conversation is about), reads the current code, and asks
the model to return a corrected complete file using your description AND any output/error you pasted as
the symptom. The fix is then verified (packages installed, smoke-run, startup crashes repaired) and the
old version is backed up as <name>.bak before saving - so a fix can't lose your file.

Examples:
  "fix time.py - it says 'no timezone data for Tokyo'"
  "the app still isn't working <pasted output>"
  "modify snake.py so the snake wraps around the edges"
"""
import re
from pathlib import Path

from core.actions import ActionBroker

SKILL = {
    "name": "fix_code",
    "description": "Fix a bug in, or change, an existing code file (a .py app/script and similar): reads "
                   "it, repairs it against the problem you describe, checks it runs, and saves a backup.",
    "triggers": [
        r"\b(?:fix|repair|debug)\b",
        r"\bthere'?s?\s+(?:a\s+)?(?:bug|error|issue|problem)\b",
        r"\b(?:isn'?t|is\s+not|still\s+not|not|doesn'?t|does\s+not|won'?t|will\s+not|can'?t|cannot)\s+"
        r"(?:work|working|run|running|open|opening|start|starting|give|giving|show|showing)\b",
        r"\b(?:modify|edit|update|change|rewrite|improve|make)\b[^.\n]*\.(?:py|pyw|js|ts|html|htm|css)\b",
        r"\bbroken\b",
    ],
    "version": 1,
    "origin": "builtin",
}

_CODE_EXTS = {".py", ".pyw", ".js", ".ts", ".html", ".htm", ".css", ".json", ".md", ".txt", ".bat",
              ".c", ".cpp", ".h", ".java", ".cs", ".rb", ".go", ".rs", ".php", ".sh", ".ps1"}
_EXT_RE = "py|pyw|js|ts|html|htm|css|json|md|txt|bat|c|cpp|h|java|cs|rb|go|rs|php|sh|ps1"
# Marks a request as really being about code/an app (not e.g. "the printer isn't working").
_CODE_CTX = re.compile(r"\b(?:fix|repair|debug|bug|error|code|app|application|script|program|game|"
                       r"module|function|py|python|file)\b|\.(?:py|pyw|js|ts|html|css)\b", re.IGNORECASE)


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _search_folders():
    home = Path.home()
    return [home / "Desktop", home / "Documents", home / "Downloads", home, Path.cwd()]


def _resolve_target(request, context):
    """Find the code file the request is about: explicit path, bare filename, or the conversation focus."""
    # 1. An explicit absolute path.
    m = re.search(rf"[A-Za-z]:[\\/][^\"'<>|?*\n]+?\.(?:{_EXT_RE})\b", request, re.IGNORECASE)
    if m:
        p = Path(m.group(0)).expanduser()
        if p.exists():
            return p
    actions = context.get("actions")
    focus = context.get("focus") or {}
    last = None
    if actions is not None and hasattr(actions, "last_written"):
        last = actions.last_written()
    # 2. A bare filename mentioned (time.py): match the focus/last file, else look in the usual folders.
    m = re.search(rf"\b([\w\-]+\.(?:{_EXT_RE}))\b", request, re.IGNORECASE)
    if m:
        fname = m.group(1)
        for candidate in (focus.get("file"), str(last) if last else None):
            if candidate and Path(candidate).name.lower() == fname.lower() and Path(candidate).exists():
                return Path(candidate)
        for folder in _search_folders():
            c = folder / fname
            if c.exists():
                return c
        return None  # a filename was named but not found -> ask, don't silently fix the wrong file
    # 3. No filename: fall back to the file the conversation is about.
    for candidate in (focus.get("file"), str(last) if last else None):
        if candidate and Path(candidate).suffix.lower() in _CODE_EXTS and Path(candidate).exists():
            return Path(candidate)
    return None


def _names_a_file(request):
    return bool(re.search(rf"[A-Za-z]:[\\/].+\.(?:{_EXT_RE})\b|\b[\w\-]+\.(?:{_EXT_RE})\b", request, re.IGNORECASE))


def run(request, context):
    # Only act when this is really about code (a file named, or clear code words) - otherwise defer.
    if not (_names_a_file(request) or _CODE_CTX.search(request)):
        return None

    target = _resolve_target(request, context)
    if target is None:
        if _names_a_file(request) or re.search(r"\b(?:fix|repair|debug|modify|edit)\b", request, re.IGNORECASE):
            return "Which file should I fix? Tell me its name (e.g. time.py) or full path, and open it once so I know which one."
        return None

    if context.get("dry_run"):
        return f"Would fix {target.name}."

    actions = _actions(context)
    ask = context.get("llm")
    if ask is None:
        return "I need a language model to fix code."

    original = actions.read_file(str(target))
    if not isinstance(original, str) or original.startswith(("There's no file", "[file")) or "blocked by your settings" in original:
        return original if isinstance(original, str) else f"I couldn't read {target}."
    if not original.strip():
        return f"{target.name} is empty - there's nothing to fix. Tell me what it should do and I'll write it."

    symptom = request.strip()[:3000]
    from core.pyverify import SYSTEM, unfence, verify_python

    prompt = (f"The file {target.name!r} has a problem. Here is exactly what the user reported, including "
              f"any program output or error they pasted:\n\n{symptom}\n\n"
              f"--- current contents of {target.name} ---\n{original[:12000]}\n\n"
              "Return the COMPLETE corrected file that fixes the problem. Keep everything that already "
              "works; change only what's needed. Code only, no explanation.")
    fixed = unfence(str(ask(prompt, system=SYSTEM, temperature=0.2, max_tokens=4000)))
    if not fixed or fixed.startswith("[LLM unavailable"):
        return "I couldn't get a fix from the model just now - try again in a moment."
    if fixed.strip() == original.strip():
        return f"I looked at {target.name} but the model returned it unchanged. Can you describe the problem in a bit more detail?"

    note = ""
    if target.suffix.lower() in (".py", ".pyw"):
        fixed, note = verify_python(target.name, fixed, context)

    backup = target.with_name(target.name + ".bak")
    actions.write_file(str(backup), original)          # never lose the old version
    result = actions.write_file(str(target), fixed)
    if not result.startswith(("Wrote", "Would")):
        return result
    summary = f"Fixed {target.name} (backed up the old version as {backup.name}).{note}"
    if re.search(r"\b(?:run|open|test|try|launch|start)\b", request, re.IGNORECASE) and target.suffix.lower() in (".py", ".pyw"):
        return f"{summary} {actions.run_python(str(target))}"
    return f"{summary} Say 'run it' to try it."
