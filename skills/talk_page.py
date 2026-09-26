"""Talk to JARVIS from your phone: hold a button on a web page, speak, hear the answer.

  'let me talk to you from my phone'   'start the talk page'   'phone mic on'
  'close the talk page'                'where's the talk page'

The page is served by this Mac and locked to a secret link. It comes up by itself whenever JARVIS
does (core/keeper.py), including through a tunnel at your own permanent address when
`talk.outside` is on - no VPN, nothing to start by hand.
"""
import re

SKILL = {
    "name": "talk_page",
    "description": "Start (or stop) the hold-to-talk page so you can speak to JARVIS from your "
                   "phone and hear the answer, e.g. 'let me talk to you from my phone', 'start the "
                   "talk page', 'close the talk page', 'where's the talk page'.",
    "triggers": [
        r"\btalk\s+page\b|\bhold[\s-]to[\s-]talk\b",
        r"\b(?:share|tunnel|publish)\b[^.\n]*\btalk\s+page\b|"
        r"\bcall\s+(?:you|jarvis)\b[^.\n]*\b(?:outside|anywhere|away|mobile\s+data)\b",
        r"\b(?:start|open|launch|bring\s+up|close|stop)\b[^.\n]*\btalk\s+page\b",
        r"\b(?:talk|speak)\s+to\s+(?:you|jarvis)\b[^.\n]*\b(?:from|on)\s+(?:my\s+)?phone\b",
        r"\b(?:phone|mobile)\s+(?:mic|microphone|voice)\b",
        r"\b(?:call|ring)\s+(?:you|jarvis)\b[^.\n]*\bphone\b",
    ],
    "version": 1,
    "origin": "builtin",
}

_OFF = re.compile(r"\b(?:stop|close|disable|turn\s+off|switch\s+off|shut|end)\b", re.IGNORECASE)
_OUTSIDE = re.compile(r"\b(?:outside|anywhere|away\s+from\s+home|out\s+of\s+the\s+house|tunnel|"
                      r"not\s+at\s+home|mobile\s+data|publish|expose|shar(?:e|ing))\b",
                      re.IGNORECASE)
_STOP_SHARING = re.compile(r"\b(?:stop|close|end|no\s+longer)\b[^.\n]*\b(?:shar\w+|tunnel|publish\w*|"
                           r"outside)\b", re.IGNORECASE)
_WHERE = re.compile(r"\b(?:where|what'?s\s+the\s+(?:link|url|address)|link|address)\b", re.IGNORECASE)


def run(request, context):
    from core import talk
    if context.get("dry_run"):
        return "Would start the hold-to-talk page for your phone."
    if _OFF.search(request) and not _OUTSIDE.search(request):
        return talk.stop()
    if _OUTSIDE.search(request):          # reachable away from the house, through a tunnel
        if _OFF.search(request) or _STOP_SHARING.search(request):
            return talk.unexpose()
        if not talk.running():
            talk.start()
        return talk.expose(getattr(talk._ACTIVE, "port", talk.PORT),
                           getattr(talk._ACTIVE, "secret", None))
    if _WHERE.search(request) and talk.running():
        outside = talk.public_url()
        return (f"The talk page is at {talk.url()}"
                + (f"\nFrom outside: {outside}" if outside else ""))
    return talk.start()
