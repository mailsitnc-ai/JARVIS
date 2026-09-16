"""Tidy a folder by sorting its loose files into type subfolders (Downloads by default)."""
import re
from pathlib import Path

from core.actions import ActionBroker

SKILL = {
    "name": "organize_files",
    "description": "Organize a folder into subfolders by file type, e.g. 'organize my downloads' or 'tidy my desktop'.",
    "triggers": [
        r"\b(?:organi[sz]e|tidy|clean\s*up|sort)\b[^.]*\b(?:folder|files|downloads|desktop|documents|pictures|music|videos)\b",
        r"\b(?:organi[sz]e|tidy)\s+my\b",
    ],
    "version": 1,
    "origin": "builtin",
}
_FOLDERS = ("downloads", "desktop", "documents", "pictures", "music", "videos")


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _target(request):
    low = request.lower()
    for name in _FOLDERS:
        if name in low:
            return Path.home() / name.capitalize()
    m = re.search(r"\b(?:in|inside|the folder)\s+([A-Za-z]:[\\/][^\"']+|~[\\/][^\"']+)", request)
    if m:
        return Path(m.group(1).strip()).expanduser()
    return Path.home() / "Downloads"      # sensible default


def run(request, context):
    if re.search(r"\b(?:email|emails|inbox|gmail|mail|message|messages)\b", request, re.IGNORECASE):
        return None   # that's a Gmail request -> let gmail_organize handle it
    target = _target(request)
    if context.get("dry_run"):
        return f"Would organize the files in {target} by type."
    return _actions(context).organize_dir(str(target))
