"""Talk to JARVIS from your phone: hold a button on a web page, speak, hear the answer.

  'let me talk to you from my phone'   'start the talk page'   'phone mic on'
  'close the talk page'                'where's the talk page'

The page is served by this Mac and locked to a secret link. To reach it from outside the house run
`tailscale serve --bg 8765` once - phones only allow the microphone on an https address, which
Tailscale provides.
"""
import re

SKILL = {
    "name": "talk_page",
    "description": "Start (or stop) the hold-to-talk page so you can speak to JARVIS from your "
                   "phone and hear the answer, e.g. 'let me talk to you from my phone', 'start the "
                   "talk page', 'close the talk page', 'where's the talk page'.",
    "triggers": [
        r"\btalk\s+page\b|\bhold[\s-]to[\s-]talk\b",
        r"\b(?:talk|speak)\s+to\s+(?:you|jarvis)\b[^.\n]*\b(?:from|on)\s+(?:my\s+)?phone\b",
        r"\b(?:phone|mobile)\s+(?:mic|microphone|voice)\b",
        r"\b(?:call|ring)\s+(?:you|jarvis)\b[^.\n]*\bphone\b",
    ],
    "version": 1,
    "origin": "builtin",
}

_OFF = re.compile(r"\b(?:stop|close|disable|turn\s+off|switch\s+off|shut|end)\b", re.IGNORECASE)
_WHERE = re.compile(r"\b(?:where|what'?s\s+the\s+(?:link|url|address)|link|address)\b", re.IGNORECASE)


def run(request, context):
    from core import talk
    if context.get("dry_run"):
        return "Would start the hold-to-talk page for your phone."
    if _OFF.search(request):
        return talk.stop()
    if _WHERE.search(request) and talk.running():
        return f"The talk page is at {talk.url()}"
    return talk.start()
