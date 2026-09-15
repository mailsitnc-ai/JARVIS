"""Tell the current time and date."""
import re
from datetime import datetime

SKILL = {
    "name": "current_time",
    "description": "Tell the current local time, date or day of the week.",
    "triggers": [
        r"\bwhat(?:'s|\s+is)?\s+(?:the\s+)?(?:time|date)\b",
        r"\bwhat\s+day\s+is\s+(?:it|today)\b",
        r"\b(?:current|today'?s)\s+(?:time|date)\b",
        r"\btime\s+is\s+it\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def run(request, context):
    now = datetime.now()
    text = request.lower()
    date_part = now.strftime("%A %d %B %Y")
    time_part = now.strftime("%H:%M")
    wants_date = re.search(r"\b(?:date|day|today)\b", text)
    wants_time = re.search(r"\btime\b", text)
    if wants_date and not wants_time:
        return f"Today is {date_part}."
    if wants_time and not wants_date:
        return f"It's {time_part}."
    return f"It's {time_part} on {date_part}."
