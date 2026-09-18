"""Compose and send an email from the user's Gmail.

Sending is a SENSITIVE action: it always asks for confirmation first (showing the recipient, subject and
full body), even when autonomy is on - JARVIS never fires off mail unattended. The body can come from a
draft file JARVIS just wrote ("send that draft to ..."), or be written from your instruction ("email
myself a reminder to ...").
"""
import re
from pathlib import Path

from core.actions import ActionBroker

SKILL = {
    "name": "send_email",
    "description": "Compose and send an email from your Gmail, e.g. 'email shiv@x.org the draft', "
                   "'send myself a reminder to call the bank'. Always asks before sending.",
    "triggers": [
        r"\b(?:send|write|compose|draft|shoot|fire\s+off)\b[^.\n]*\bemail\b",
        r"\bemail\b[^.\n]*\b(?:to|myself|me)\b",
        r"\bemail\b[^.\n]*@[\w.-]+\.\w+",
        r"\bsend\b[^.\n]*\bto\b[^.\n]*@[\w.-]+\.\w+",
    ],
    "version": 1,
    "origin": "builtin",
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BODY_SYSTEM = ("You write the body of an email. Output ONLY the email body text - no subject line, no "
                "'Subject:', no greetings meta, no markdown. Keep it natural and appropriately brief.")


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _recipient(request, actions):
    m = _EMAIL.search(request)
    if m:
        return m.group(0)
    if re.search(r"\bto\s+(?:myself|me|my\s+own|my\s+self)\b|\bmyself\b", request, re.IGNORECASE):
        return actions.my_gmail_address() if hasattr(actions, "my_gmail_address") else None
    return None


def _subject(request):
    m = re.search(r"\b(?:subject|titled|title|about|regarding|re)\s*[:\-]?\s+(.+?)(?:\s+(?:saying|that\s+says|"
                  r"with\s+the|containing|body|and\s+send|to\s+\S+@)|[.\n]|$)", request, re.IGNORECASE)
    return m.group(1).strip().strip("\"'") if m else None


def _wants_draft(request):
    return bool(re.search(r"\b(?:that|the|this)\s+(?:draft|file|document|note|contents?)\b|contents?\s+of\b|"
                          r"\bwhat\s+you\s+(?:wrote|made|created)\b", request, re.IGNORECASE))


def _clean_instruction(request):
    """Strip the send/recipient/subject scaffolding so what's left describes the message to write."""
    text = _EMAIL.sub("", request)
    text = re.sub(r"\b(?:and\s+)?send(?:\s+(?:it|this|that|the\s+email))?\s+to\b.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:send|write|compose|draft|shoot|fire\s+off)\b\s+(?:an?\s+)?(?:email|message|mail)\s+"
                  r"(?:to\s+(?:myself|me)\s+)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bto\s+(?:myself|me)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:subject|titled|about|regarding)\s*[:\-]?\s+.+$", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.:-")


def run(request, context):
    actions = _actions(context)
    to = _recipient(request, actions)
    if not to:
        return "Who should I send it to? Give me an email address (or say 'myself')."
    if context.get("dry_run"):
        return f"Would compose and send an email to {to} (after your confirmation)."

    subject = _subject(request)
    body = None
    if _wants_draft(request):
        last = actions.last_written() if hasattr(actions, "last_written") else None
        focus = (context.get("focus") or {}).get("file")
        src = None
        for cand in (focus, str(last) if last else None):
            if cand and Path(cand).suffix.lower() in (".txt", ".md", ".eml") and Path(cand).exists():
                src = cand
                break
        if src:
            text = actions.read_file(src)
            if isinstance(text, str) and text.strip() and not text.startswith(("There's no", "[file")):
                body = text.strip()
    ask = context.get("llm")
    if body is None:                       # no draft to reuse -> write the body from the instruction
        instruction = _clean_instruction(request) or "a short friendly email"
        if ask is None:
            return "I need a language model to write the email body (or point me at a draft file)."
        body = str(ask(f"Write the body of an email. What it should say: {instruction}",
                       system=_BODY_SYSTEM, temperature=0.4, max_tokens=600)).strip()
        if not body or body.startswith("[LLM unavailable"):
            return "I couldn't draft the email just now - try again in a moment."
    if not subject:
        if ask is not None:
            subject = str(ask(f"Write a short (max 8 words) email subject line for this email body, "
                              f"plain text only:\n\n{body[:800]}", temperature=0.3, max_tokens=30)).strip().strip("\"'")
        subject = (subject or body.splitlines()[0][:60]).strip() or "(no subject)"

    return actions.gmail_send(to, subject, body)
