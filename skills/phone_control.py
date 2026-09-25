"""JARVIS on your phone: answer WhatsApp messages you send yourself, and do what they say.

  'watch my whatsapp'                       start (uses the chat you set last time)
  'watch my whatsapp +91 97700 94860'       start on that chat, and remember it
  'stop watching whatsapp'                  stop
  'is phone control on'                     status

Then, from your phone, message yourself: "Jarvis, what's my battery?", "jarvis summarise the physics
pdf", "/briefing". Voice notes work too - they're transcribed on the Mac. Risky actions ask you
first, in the chat.
"""
import re

SKILL = {
    "name": "phone_control",
    "description": "Let JARVIS answer WhatsApp messages you send from your phone and do what they "
                   "say, e.g. 'watch my whatsapp', 'watch my whatsapp +91 97700 94860', 'stop "
                   "watching whatsapp', 'is phone control on'.",
    "triggers": [
        r"\b(?:watch|monitor|read|check|answer|reply\s+to)\b[^.\n]*\b(?:my\s+)?whats\s?app\b",
        r"\b(?:phone|mobile|remote)\s+control\b|\bcontrol\b[^.\n]*\bfrom\s+my\s+phone\b",
        r"\bjarvis\s+on\s+(?:my\s+)?phone\b|\bwhats\s?app\s+(?:bot|assistant|channel)\b",
        r"\bstop\b[^.\n]*\bwhats\s?app\b[^.\n]*\b(?:watch|control|bot)\b|"
        r"\bstop\s+watching\s+(?:my\s+)?whats\s?app\b",
    ],
    "version": 1,
    "origin": "builtin",
}

_OFF = re.compile(r"\b(?:stop|disable|turn\s+off|switch\s+off|off|quit|end)\b", re.IGNORECASE)
_ASK = re.compile(r"\b(?:is|status|are\s+you|what'?s)\b", re.IGNORECASE)
_CHAT = re.compile(r"(\+?\d[\d\s()-]{7,}\d)|\bchat\s+(?:with\s+)?['\"]?([\w .&-]{2,40})['\"]?", re.IGNORECASE)


def _chat_in(request):
    match = _CHAT.search(request)
    if not match:
        return None
    number, name = match.group(1), match.group(2)
    return re.sub(r"[^\d+]", "", number) if number else (name or "").strip()


def run(request, context):
    from core import remote
    if context.get("dry_run"):
        return "Would start (or stop) watching WhatsApp for messages from your phone."
    if _OFF.search(request):
        return remote.stop()
    if _ASK.search(request) and not re.search(r"\b(?:start|watch|enable|turn\s+on)\b", request, re.I):
        return remote.status()
    return remote.start(_chat_in(request))
