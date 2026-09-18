"""Send a WhatsApp message on your behalf, via WhatsApp Web in JARVIS's Chrome.

Always asks before sending (SENSITIVE 'message_send'). Most reliable with a phone number (it uses
WhatsApp's send deep link); a contact name is best-effort. You must have scanned the WhatsApp Web QR
once in JARVIS's Chrome window.
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "whatsapp",
    "description": "Send a WhatsApp message, e.g. 'whatsapp mom saying I'll be late' or "
                   "'send a whatsapp to +14155551234 saying hi'. Asks before sending.",
    "triggers": [r"\bwhat'?s\s?app\b"],
    "version": 1,
    "origin": "builtin",
}
_PHONE = re.compile(r"(\+?\d[\d\s\-()]{6,}\d)")
_MSG = re.compile(r"(?:\b(?:saying|that\s+says|tell(?:ing)?\s+(?:them|him|her)?)|:)\s*(.+)$", re.IGNORECASE)
_NAME = re.compile(r"\b(?:whatsapp|message|text|to)\s+([A-Za-z][\w .'\-]{0,40}?)"
                   r"(?=\s+(?:on\s+whats?app|saying|that\s+says|about|:)|\s*:|$)", re.IGNORECASE)


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    msg = _MSG.search(request)
    message = msg.group(1).strip().strip("\"'") if msg else None
    phone = _PHONE.search(request)
    if phone:
        to = re.sub(r"[()\s\-]", "", phone.group(1))
    else:
        name = _NAME.search(request)
        to = name.group(1).strip() if name else None
    if not to:
        return "Who should I WhatsApp? Give a contact name or phone number, e.g. 'whatsapp Alex saying hi'."
    if not message:
        return f"What should I say to {to}? e.g. 'whatsapp {to} saying running late'."
    if context.get("dry_run"):
        return f"Would send a WhatsApp message to {to} (after your confirmation)."
    return _actions(context).send_message("whatsapp", to, message)
