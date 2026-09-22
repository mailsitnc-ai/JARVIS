"""Turn JARVIS's ears on or off: 'start listening', 'stop listening', 'voice mode on/off'.

While listening, say "Jarvis, <request>" and JARVIS does it and answers aloud. Speech is transcribed on
this computer (faster-whisper); anything not addressed to JARVIS is discarded, and no audio is saved.
"""
import re

SKILL = {
    "name": "voice_control",
    "description": "Start or stop voice control - talk to JARVIS by saying 'Jarvis, ...', e.g. 'start "
                   "listening', 'stop listening', 'voice mode on', 'turn off voice'.",
    # anchored to the start: these are short commands, and "stop listening" inside a message body
    # ("whatsapp Inaya saying stop listening to him") must not toggle the mic
    "triggers": [
        r"^\W*(?:(?:hey\s+)?jarvis\W+)?(?:please\s+)?(?:start|stop|begin|end|resume|pause)\s+listening\b",
        r"^\W*(?:(?:hey\s+)?jarvis\W+)?(?:please\s+)?(?:turn|switch|enable|disable)\s+(?:on|off)?\s*"
        r"(?:the\s+)?(?:voice|mic|microphone)(?:\s+(?:mode|control|on|off))?\W*$",
        r"^\W*(?:(?:hey\s+)?jarvis\W+)?voice\s+(?:mode|control|commands?)(?:\s+(?:on|off))?\W*$",
        r"^\W*(?:(?:hey\s+)?jarvis\W+)?(?:voice|mic|microphone)\s+(?:on|off)\W*$",
        r"^\W*(?:(?:hey\s+)?jarvis\W+)?listen\s+to\s+me\W*$",
    ],
    "version": 1,
    "origin": "builtin",
}

_OFF = re.compile(r"\b(?:stop|end|pause|off|disable|mute)\b", re.IGNORECASE)


def run(request, context):
    off = bool(_OFF.search(request))
    if context.get("dry_run"):
        return "Would stop listening." if off else "Would start listening for 'Jarvis'."
    from core import voice
    return voice.stop() if off else voice.start()
