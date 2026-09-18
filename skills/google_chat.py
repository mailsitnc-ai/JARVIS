"""Send a Google Chat message on your behalf, via chat.google.com in JARVIS's Chrome.

Always asks before sending (SENSITIVE 'message_send', which shows you the recipient and full body).
Best-effort DOM automation - consumer Google Chat has no send API - so you must be signed in to
chat.google.com in JARVIS's Chrome, and a page-layout change can break it. Messages go to a person
or a space by name ('Priya', 'the Design space').
"""
from core.actions import ActionBroker
from core.messaging import parse_message_command

SKILL = {
    "name": "google_chat",
    "description": "Send a Google Chat message to a person or space, e.g. 'google chat Priya saying "
                   "standup at 10' or 'message the Design space on chat: shipping today'. Always asks first.",
    "triggers": [
        r"\bgoogle\s+chat\b",
        r"\b(?:message|chat|ping|dm)\b[^.\n]*\bon\s+(?:google\s+)?chat\b",
        r"\bchat\s+(?:message\b|to\b)",
    ],
    "version": 2,
    "origin": "builtin",
}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    to, is_group, message = parse_message_command(request, allow_phone=False)
    if to and to.lower() in ("message", "chat", "the", "google", "space", "group"):
        to = None
    if not to:
        return ("Who should I message on Google Chat? A person or space, "
                "e.g. 'google chat Priya saying hi' or 'message the Design space: shipping today'.")
    if not message:
        where = f"the {to} space" if is_group else to
        return f"What should I say to {where}? e.g. 'google chat {to} saying running late'."
    if context.get("dry_run"):
        where = f"the {to} space" if is_group else to
        return f"Would send a Google Chat message to {where} (after your confirmation)."
    return _actions(context).send_message("chat", to, message)
