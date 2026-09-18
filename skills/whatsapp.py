"""Send a WhatsApp message on your behalf, via WhatsApp Web in JARVIS's Chrome.

Always asks before sending (SENSITIVE 'message_send', which shows you the recipient and the full body).
You can address it by contact name, group name, or phone number - JARVIS searches WhatsApp Web for
whatever you name, so 'the Maa Paa group' and 'mom' both work. A phone number uses WhatsApp's send
deep link (most reliable). You must have scanned the WhatsApp Web QR once in JARVIS's Chrome window.
"""
from core.actions import ActionBroker
from core.messaging import parse_message_command

SKILL = {
    "name": "whatsapp",
    "description": "Send a WhatsApp message to a contact, group or number, e.g. 'whatsapp mom saying "
                   "I'll be late', 'send a whatsapp in the Maa Paa group - standup at 10', or "
                   "'whatsapp +14155551234 saying hi'. Always asks before sending.",
    "triggers": [r"\bwhats?app\b", r"\bwhat'?s\s?app\b"],
    "version": 2,
    "origin": "builtin",
}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    to, is_group, message = parse_message_command(request, allow_phone=True)
    if not to:
        return ("Who should I WhatsApp? Give a contact name, group name or number, "
                "e.g. 'whatsapp Alex saying hi' or 'whatsapp the Maa Paa group - hello'.")
    if not message:
        where = f"the {to} group" if is_group else to
        return f"What should I send to {where}? e.g. 'whatsapp {to} saying running late'."
    if context.get("dry_run"):
        where = f"the {to} group" if is_group else to
        return f"Would send a WhatsApp message to {where} (after your confirmation)."
    return _actions(context).send_message("whatsapp", to, message)
