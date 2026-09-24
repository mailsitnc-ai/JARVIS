"""Control JARVIS with hand gestures through the webcam ("watch my hands", "enable hand control").

Starts the gesture engine (core/gestures.py): point to move the mouse pointer, pinch thumb+index to click
(twice = double-click, hold = drag), thumb+middle = right-click, two fingers = scroll, 3 = identify,
4 = screenshot, open palm = stop, thumbs-up = the on-screen keyboard (pinch and drag through the letters
to swipe a word, like a phone). Privacy-gated on the 'camera' capability, so it only runs from an
explicit command you approve.
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "hand_control",
    "description": "Control the mouse with your hand via the webcam (point to move, pinch to click, two "
                   "fingers to scroll), e.g. 'watch my hands', 'enable hand control', 'stop watching my hands'.",
    "triggers": [
        r"\b(?:hand|gesture)\s+control\b",
        r"\bcontrol\b[^.\n]*\b(?:hand|hands|gesture|gestures)\b",
        r"\b(?:watch|read|track|use|follow)\b[^.\n]*\bmy\s+hands?\b",
        r"\b(?:enable|start|turn\s+on|use)\b[^.\n]*\bgestures?\b",
        r"\bstop\b[^.\n]*\b(?:hand|hands|gesture)\b",
        r"^\W*(?:jarvis[,\s]+)?(?:show|hide|open|close|bring up|put away)\s+(?:the\s+|my\s+)?keyboard\b",
        r"^\W*keyboard\s*(?:on|off)?\W*$",
    ],
    "version": 3,
    "origin": "builtin",
}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


_KEYBOARD = re.compile(r"\bkeyboard\b", re.IGNORECASE)
_HIDE = re.compile(r"\b(?:hide|close|put\s+away|off|dismiss)\b", re.IGNORECASE)


def run(request, context):
    actions = _actions(context)
    if _KEYBOARD.search(request):
        on = not _HIDE.search(request)
        if context.get("dry_run"):
            return f"Would {'show' if on else 'hide'} the point-to-type keyboard."
        from core import gestures
        return gestures.command("keyboard " + ("on" if on else "off"))
    stop = re.search(r"\b(?:stop|turn\s+off|disable|end|quit|off)\b", request, re.IGNORECASE)
    if stop:
        return "Would stop hand control." if context.get("dry_run") else actions.stop_gesture_control()
    if context.get("dry_run"):
        return "Would start hand-gesture control."
    runner = context.get("run") or (lambda cmd: None)      # runs a fired gesture command via existing skills
    return actions.start_gesture_control(runner, context.get("emit"))
