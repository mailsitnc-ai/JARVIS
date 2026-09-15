"""Search the user's Gmail (requires: jarvis google login)."""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "gmail_search",
    "description": "Search your Gmail for messages (needs Google connected).",
    "triggers": [
        r"\b(?:my\s+)?(?:gmail|inbox)\b",
        r"\bemails?\b.*\b(?:from|about|regarding|with|to)\b",
        r"\b(?:check|search|read)\b.*\b(?:my\s+)?(?:email|emails|mail)\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def _query(request):
    # Turn "any emails from alice about taxes" into a Gmail query "from alice about taxes".
    text = re.sub(r"^\s*(?:any|do\s+i\s+have|are\s+there|check|search|read|show\s+me|find|look\s+for|look\s+up)\b",
                  " ", request, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:my\s+)?(?:gmail|inbox|e-?mails?|mail|messages?|please)\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip(" .!?")


def run(request, context):
    query = _query(request)
    actions = context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))
    return actions.search_gmail(query or request)
