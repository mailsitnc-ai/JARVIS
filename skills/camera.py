"""Use the webcam on command: take a photo, or capture and describe what it sees (vision).

The camera only fires from an explicit request you approve - it's a privacy-sensitive capability that
autonomy never blanket-allows.
"""
import re
import time
from pathlib import Path

from core.actions import ActionBroker

SKILL = {
    "name": "camera",
    "description": "Take a webcam photo, or capture and describe what the camera sees, e.g. "
                   "'take a photo', 'what do you see', 'who is in front of the camera'.",
    "triggers": [
        r"\b(?:take|snap|capture|grab)\s+(?:a\s+)?(?:photo|picture|pic|selfie|shot|snapshot)\b",
        r"\b(?:web\s?cam|camera)\b",
        r"\bwhat\s+do\s+you\s+see\b",
        r"\bwhat\s+am\s+i\s+(?:holding|doing|wearing)\b",
        r"\b(?:identify|recogni[sz]e|tell\s+me)\b[^.\n]*\b(?:holding|in\s+my\s+hand|doing)\b",
        r"\blook\s+(?:through|with|at)\s+(?:the\s+)?(?:camera|webcam)\b",
    ],
    "version": 1,
    "origin": "builtin",
}
_DESCRIBE = re.compile(r"\b(?:see|describe|look|who|what'?s|recogni[sz]e|identify|am i|is there|holding|"
                       r"wearing|doing|in front|visible|reading)\b", re.IGNORECASE)


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    if context.get("dry_run"):
        return "Would take a webcam photo."
    target = Path.home() / "Pictures" / f"JARVIS-cam-{time.strftime('%Y%m%d-%H%M%S')}.png"
    result = _actions(context).capture_camera(str(target))
    if not result.startswith("Photo saved"):
        return result   # a broker error, a denial, or "needs opencv"
    if _DESCRIBE.search(request):
        from core.vision import available, describe_image
        if not available():
            return f"{result}. (I need a vision model to describe it - add a Gemini key: jarvis setkey gemini.)"
        desc = describe_image(str(target), "Describe what the webcam sees, concisely: who or what is visible.")
        if desc and not desc.startswith("("):     # "(...)" marks a vision error
            return f"I see: {desc}"
        return f"{result}. I couldn't describe it {desc or ''}".rstrip()
    if re.search(r"\b(?:show|open|display)\b", request, re.IGNORECASE):
        _actions(context).open_path(str(target))
    return result
