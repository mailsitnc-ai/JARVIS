"""Record a short webcam video on command ("record a 10 second video", "take a 5s clip of me").

Goes through the broker's gated camera capability (same privacy rule as photos - it never records from
a background/agenda task) and remembers the file, so "open it" / "open the video" works afterwards.
"""
import re
import time
from pathlib import Path

from core.actions import ActionBroker

SKILL = {
    "name": "record_video",
    "description": "Record a short webcam video, e.g. 'record a 10 second video of me', 'take a 5s clip'.",
    "triggers": [
        r"\brecord\b[^.]*\b(?:video|clip|webcam|footage)\b",
        r"\b(?:record|take|capture|grab|shoot)\b[^.]*\bvideo\b[^.]*\b(?:of\s+me|of\s+us|myself|webcam)\b",
        r"\b\d+\s*(?:second|sec|s)\b[^.]*\bvideo\b",
        r"\bvideo\b[^.]*\b(?:of\s+me|of\s+us|myself|webcam)\b",
    ],
    "version": 2,
    "origin": "builtin",
}
_FOLDERS = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
            "pictures": "Pictures", "videos": "Videos"}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _duration(request):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:second|sec|s)\b", request, re.IGNORECASE)
    return float(m.group(1)) if m else 5.0


def _destination(request):
    """A file path if the user named one ("...to C:\\...\\clip.mp4" or "...on my desktop"), else None."""
    path = re.search(r"[A-Za-z]:[\\/][^\"'<>|?*\n]+?\.(?:mp4|avi|mov|mkv)", request, re.IGNORECASE)
    if path:
        return Path(path.group(0).strip())
    for word, folder in _FOLDERS.items():
        if re.search(rf"\b(?:on|onto|to|in|into)\s+(?:my\s+|the\s+)?{word}\b", request, re.IGNORECASE):
            return Path.home() / folder / f"JARVIS-vid-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    return None


def run(request, context):
    if re.search(r"\bvideo\s+game\b", request, re.IGNORECASE):
        return None  # "make a video game" is a build task, not a webcam recording
    seconds = _duration(request)
    if context.get("dry_run"):
        return f"Would record a {int(seconds)}s webcam video."
    dest = _destination(request)
    return _actions(context).record_video(str(dest) if dest else None, seconds)
