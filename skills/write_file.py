"""Generate content (code, scripts, apps, text) with the LLM and save it to a file.

This is how JARVIS "writes code" and "makes apps": it works out the target path, asks the model to
produce the file's contents, strips any markdown fences, and writes it through the gated broker. Pair it
with open_app/open_path to then run what it just wrote.
"""
import re
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
    "version": 1,
    "origin": "builtin",
}

_FOLDERS = {
    "desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
    "pictures": "Pictures", "music": "Music", "videos": "Videos",
}
_FENCE = re.compile(r"^\s*```[\w+-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)
_SYSTEM = ("You generate the raw contents of a single file. Output ONLY the file's contents - no "
           "explanation, no commentary, and no markdown code fences. Write complete, working, idiomatic code.")


_CODE_EXT = {"py", "pyw", "html", "htm", "js", "ts", "css", "java", "c", "cpp", "cs", "rb", "go", "rs",
             "php", "sh", "ps1", "bat", "json", "md", "txt", "csv"}


def _target_path(request):
    """Work out the file path to save to (and its name), defaulting to the Desktop."""
    name = None
    # A request may name two files ("save the qr as hello.png ... save the script as makeqr.py"):
    # pick the code/script file to write, not a data/output file.
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

    # An explicit folder ("to/in my desktop", "in downloads") or a full path after to/in.
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


def _verify_python(name, content, context):
    """Syntax-check generated Python, fix it with the model if broken, and install any missing packages so
    the app actually works. Returns (content, note)."""
    import ast
    import importlib.util
    import sys

    from evolution_engine.sandbox import MODULE_TO_PIP

    ask = context.get("llm")
    stdlib = getattr(sys, "stdlib_module_names", set())
    notes = []
    for attempt in range(3):
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            if ask is None or attempt == 2:
                return content, f" (heads up: a syntax error remains near line {exc.lineno})"
            fixed = str(ask(f"This Python file {name!r} has a syntax error: {exc.msg} at line {exc.lineno}. "
                            f"Here is the file:\n{content}\n\nReturn the corrected COMPLETE file, code only.",
                            system=_SYSTEM, temperature=0.1, max_tokens=2000)).strip()
            fence = _FENCE.match(fixed)
            content = fence.group(1) if fence else fixed
            continue
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
            except Exception:
                notes.append(f"needs '{m}' (couldn't install)")
        return content, (f" ({'; '.join(notes)})" if notes else "")
    return content, ""


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
    content = str(ask(prompt, system=_SYSTEM, temperature=0.2, max_tokens=2000)).strip()
    fence = _FENCE.match(content)
    if fence:
        content = fence.group(1)
    if not content or content.startswith("[LLM unavailable"):
        return "I couldn't generate the file contents just now."

    note = ""
    if target.suffix.lower() in (".py", ".pyw"):        # verify it compiles + has its packages before shipping
        content, note = _verify_python(name, content, context)

    result = context["actions"].write_file(str(target), content)
    if not result.startswith(("Wrote", "Would")):
        return result  # a Blocked/denied message from the broker
    lines = content.count("\n") + 1
    summary = f"Wrote {name} ({lines} lines) to {target.parent}.{note}"
    if re.search(r"\b(?:open|run|launch|start|execute)\b", request, re.IGNORECASE):
        actions = context["actions"]
        # actually RUN a python app (open_path would just open an editor); open other files normally
        opened = (actions.run_python(str(target)) if target.suffix.lower() in (".py", ".pyw")
                  else actions.open_path(str(target)))
        return f"{summary} {opened}"
    return summary
