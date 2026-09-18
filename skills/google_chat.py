"""Send a Google Chat message on your behalf, via chat.google.com in JARVIS's Chrome.

Always asks before sending (SENSITIVE 'message_send'). Best-effort DOM automation - consumer Google Chat
has no send API - so you must be signed in to chat.google.com in JARVIS's Chrome, and the page layout
changing can break it. Messages go to a person or space by name.
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "google_chat",
    "description": "Send a Google Chat message to a person or space, e.g. 'google chat Priya saying "
                   "standup at 10' or 'message the Design space on chat: shipping today'. Asks first.",
    "triggers": [
        r"\bgoogle\s+chat\b",
        r"\b(?:message|chat|ping|dm)\b[^.\n]*\bon\s+(?:google\s+)?chat\b",
        r"\bchat\s+(?:message\b|to\b)",
    ],
    "version": 1,
    "origin": "builtin",
}
_MSG = re.compile(r"(?:\b(?:saying|that\s+says|tell(?:ing)?\s+(?:them|him|her)?)|:)\s*(.+)$", re.IGNORECASE)
_NAME = re.compile(r"\b(?:google\s+chat|chat|message|ping|dm|to)\s+(?:the\s+)?([A-Za-z][\w .'\-]{0,40}?)"
                   r"(?=\s+(?:space\b|on\s+(?:google\s+)?chat|saying|that\s+says|about|:)|\s*:|$)", re.IGNORECASE)


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    msg = _MSG.search(request)
    message = msg.group(1).strip().strip("\"'") if msg else None
    name = _NAME.search(request)
    to = name.group(1).strip() if name else None
    if to and to.lower() in ("message", "chat", "the"):
        to = None
    if not to:
        return "Who should I message on Google Chat? e.g. 'google chat Priya saying hi'."
    if not message:
        return f"What should I say to {to}? e.g. 'google chat {to} saying running late'."
    if context.get("dry_run"):
        return f"Would send a Google Chat message to {to} (after your confirmation)."
    return _actions(context).send_message("chat", to, message)
