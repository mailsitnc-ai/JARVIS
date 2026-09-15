"""Show / open the screenshot JARVIS last took, or the folder it's saved in."""
from core.actions import ActionBroker

SKILL = {
    "name": "show_screenshot",
    "description": "Open the last screenshot, or the folder it was saved in.",
    "triggers": [
        r"\b(?:show|see|view|reveal|display|find|open)\b[^.]*\bscreen\s*shot\b",
        r"\bwhere(?:'s|\s+is)?\b[^.]*\bscreen\s*shot\b",
        r"\bscreen\s*shot\b[^.]*\bfolder\b",
        r"\bfolder\b[^.]*\bscreen\s*shot\b",
        r"\b(?:last|latest|recent)\s+screen\s*shot\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def run(request, context):
    actions = context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))
    shot = actions.last_screenshot()
    if shot is None:
        return "I haven't taken a screenshot yet. Say 'take a screenshot' first."
    return actions.reveal(str(shot))  # opens the folder with the file highlighted
