"""Observe the screen / browser: take a screenshot and describe what's on it (vision).

'what's on my browser', 'what am I looking at', 'read my screen' -> a screenshot is captured (gated by
'screen') and described by a vision model.
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "observe_screen",
    "description": "Look at and describe what's on your screen or browser right now, e.g. "
                   "'what's on my screen', 'read my browser', 'what am I looking at'.",
    "triggers": [
        r"\bwhat(?:'?s| is| am i)\b[^.]*\b(?:on\s+(?:my\s+)?(?:screen|browser|display|tab)|looking at)\b",
        r"\b(?:read|describe|observe|check|look at)\b[^.]*\b(?:my\s+)?(?:screen|browser|display|tab|page)\b",
        r"\bwhat\s+does\s+(?:my\s+)?(?:screen|browser|page)\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    if context.get("dry_run"):
        return "Would capture the screen and describe it."
    shot = _actions(context).screenshot()
    if not shot.startswith("Screenshot saved to "):
        return shot   # denial or capture error
    path = shot.split("saved to ", 1)[1].strip()
    from core.vision import available, describe_image
    if not available():
        return "I captured your screen but need a vision model to read it - add a Gemini key (jarvis setkey gemini)."
    focus = "browser" if re.search(r"\bbrowser|tab|page\b", request, re.IGNORECASE) else "screen"
    q = (f"Describe what is shown on this {focus}. If it's a web page or app, say which page/site or app "
         "it is and summarise the main content on screen.")
    desc = describe_image(path, q)
    if desc and not desc.startswith("("):
        return desc
    return f"I captured your screen (saved to {path}) but couldn't read it {desc or ''}".rstrip()
