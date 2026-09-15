"""Search the user's Google Drive (requires: jarvis google login)."""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "drive_search",
    "description": "Search your Google Drive for a file (needs Google connected).",
    "triggers": [
        r"\b(?:in|on|from|my)\s+(?:google\s+)?drive\b",
        r"\b(?:google\s+)?drive\b.*\b(?:for|file|document|doc)\b",
        r"\bsearch\s+(?:my\s+)?drive\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def _query(request):
    text = re.sub(r"\b(?:google\s+)?drive\b", " ", request, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:search|find|look\s+up|get|show\s+me|the)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:in|on|from|my|for|file|files|document|documents|doc|docs|please)\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip(" .!?")


def run(request, context):
    query = _query(request)
    if not query:
        return "What should I search your Drive for?"
    actions = context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))
    return actions.search_drive(query)
