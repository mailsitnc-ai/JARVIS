"""Control JARVIS with hand gestures through the webcam ("watch my hands", "enable hand control").

Starts the gesture engine (core/gestures.py) which watches the webcam and fires a mapped command on a
stable finger count (1 = screenshot, 2 = time, 3 = read screen; open palm = stop). Privacy-gated on the
'camera' capability, so it only runs from an explicit command you approve.
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "hand_control",
    "description": "Control JARVIS with hand gestures via the webcam, e.g. 'watch my hands', 'enable "
                   "hand control', 'stop watching my hands'.",
    "triggers": [
        r"\b(?:hand|gesture)\s+control\b",
        r"\bcontrol\b[^.\n]*\b(?:hand|hands|gesture|gestures)\b",
        r"\b(?:watch|read|track|use|follow)\b[^.\n]*\bmy\s+hands?\b",
        r"\b(?:enable|start|turn\s+on|use)\b[^.\n]*\bgestures?\b",
        r"\bstop\b[^.\n]*\b(?:hand|hands|gesture)\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    actions = _actions(context)
    stop = re.search(r"\b(?:stop|turn\s+off|disable|end|quit|off)\b", request, re.IGNORECASE)
    if stop:
        return "Would stop hand control." if context.get("dry_run") else actions.stop_gesture_control()
    if context.get("dry_run"):
        return "Would start hand-gesture control."
    runner = context.get("run") or (lambda cmd: None)      # runs a fired gesture command via existing skills
    return actions.start_gesture_control(runner, context.get("emit"))
