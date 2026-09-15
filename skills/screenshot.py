"""Capture the screen to an image file, through the permission broker.

Triggers only on *capturing* a screenshot. "show me the screenshot" is handled by show_screenshot.
"""
from core.actions import ActionBroker

SKILL = {
    "name": "screenshot",
    "description": "Capture a screenshot of the screen and save it to your Pictures folder.",
    "triggers": [
        r"\btake\s+(?:a\s+)?screen\s*shot\b",
        r"\bcaptur\w*\s+(?:the\s+|my\s+)?screen\b",
        r"\bgrab\s+(?:a\s+|the\s+)?screen(?:\s*shot)?\b",
        r"\bscreen\s*grab\b",
        r"^\s*screen\s*shot\s*$",
    ],
    "version": 1,
    "origin": "builtin",
}


def run(request, context):
    actions = context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))
    return actions.screenshot()
