"""Open Windows apps, common folders, files and websites, through the permission broker."""
import re
from pathlib import Path

from core.actions import ActionBroker, has_local_file_ext, strip_browser_suffix

SKILL = {
    "name": "open_app",
    "description": "Open a Windows app (Notepad, Chrome, Settings...), a file, a common folder, web app or website.",
    "triggers": [r"^\s*(?:please\s+)?(?:open|launch|start)\s+\S+"],
    "version": 1,
    "origin": "builtin",
}
# Phrases that aren't an app/site name: let another skill (show_screenshot, browser_tab, evolution) handle them.
_DEFER = re.compile(r"\b(?:screenshot|screen\s*shot|folder|file|where|saved|stored|\btab\b)\b", re.IGNORECASE)
# Pronoun / "the thing I just made" targets belong to open_last, not here ("open it", "run that").
_PRONOUN = re.compile(
    r"^(?:it|that|this|them|the\s+(?:file|app|program|script|game|one|video|recording|clip|movie|"
    r"photo|picture|image|screenshot))\b"
    r"|you\s+(?:just\s+)?(?:made|created|wrote|saved|built|recorded|captured|took|taken|downloaded|generated)",
    re.IGNORECASE)


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _resolve_file(target, context):
    """Turn a filename/path into an existing file: as given, then the file JARVIS just made, then common folders."""
    text = target.strip().strip("\"'")
    given = Path(text).expanduser()
    if given.exists():
        return given
    actions = context.get("actions")
    last = actions.last_written() if actions is not None and hasattr(actions, "last_written") else None
    if last is not None and last.name.lower() == given.name.lower():
        return last  # "open frenchjokes.py" -> the frenchjokes.py it just wrote
    if not (("\\" in text) or ("/" in text)):  # a bare filename: look in the usual places
        home = Path.home()
        for folder in (home / "Desktop", home / "Documents", home / "Downloads", home, Path.cwd()):
            candidate = folder / given.name
            if candidate.exists():
                return candidate
    return None


def run(request, context):
    # An explicit absolute path anywhere in the request wins: "open the video in this file path: C:\...\x.mp4".
    explicit = re.search(r"[A-Za-z]:[\\/][^\"'<>|?*\n]+?\.[A-Za-z0-9]{1,5}", request)
    if explicit:
        target = Path(explicit.group(0).strip().strip("\"'")).expanduser()
        if target.exists():
            return _actions(context).open_path(str(target))
        return f"I couldn't find a file at {target}."

    match = re.match(r"^\s*(?:please\s+)?(?:open|launch|start)\s+(?:up\s+)?(?:the\s+|my\s+)?(.+?)[\s.!?]*$",
                     request, re.IGNORECASE)
    if not match:
        return None
    target = re.sub(r"\s+(?:app|application|website|site|please)$", "", match.group(1).strip())
    target = strip_browser_suffix(target)  # "google docs on my chrome" -> "google docs"
    if _PRONOUN.match(target):
        return None  # "open it" / "run that" -> open_last opens the file just created

    # A filename or path -> open the FILE, never turn it into a website ("open frenchjokes.py").
    # (A bare forward-slash isn't enough - that could be a URL path like example.com/page.)
    looks_like_file = (has_local_file_ext(target) or "\\" in target or re.match(r"^[A-Za-z]:", target)
                       or Path(target).expanduser().exists())
    if looks_like_file:
        resolved = _resolve_file(target, context)
        if resolved is not None:
            return _actions(context).open_path(str(resolved))
        return f"I couldn't find a file called '{Path(target).name}'."

    if _DEFER.search(target) or len(target.split()) > 4:
        return None  # not a simple app/site name -> defer to a more specific skill
    result = _actions(context).open_app(target)  # handles apps, folders, web apps and domains
    if result is None:  # genuinely unknown: say so, don't spawn a clone skill
        return f"I couldn't find an app or site called '{target}'."
    return result
