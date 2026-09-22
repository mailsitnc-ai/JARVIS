"""A JARVIS-style briefing: 'good morning jarvis', 'brief me', 'status report', 'catch me up'.

Time and date, local weather, unread email (if Google is connected), battery, and anything on JARVIS's
own agenda for today - composed as a couple of spoken-length sentences (core/briefing.py).
"""
SKILL = {
    "name": "briefing",
    "description": "Give a quick briefing - time, weather, unread email, battery and today's agenda, e.g. "
                   "'good morning jarvis', 'brief me', 'status report', 'catch me up', 'what's the weather'.",
    "triggers": [
        r"^\W*(?:good\s+(?:morning|afternoon|evening))\b",
        r"\bbrief(?:ing)?\s+me\b|\b(?:daily|morning|evening)\s+(?:brief|briefing|update)\b|^\W*briefing\W*$",
        r"\bstatus\s+report\b|\bsitrep\b",
        r"\bcatch\s+me\s+up\b|\bwhat\s+did\s+i\s+miss\b",
        r"\b(?:what'?s|how'?s|what\s+is)\s+the\s+weather\b|\bweather\s+(?:today|now|like)\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def run(request, context):
    if context.get("dry_run"):
        return "Would give you a briefing (time, weather, email, battery, agenda)."
    from core import briefing
    from core.config import load_settings
    settings = load_settings()
    low = request.lower()
    if "weather" in low and not any(w in low for w in ("brief", "morning", "evening", "afternoon", "report")):
        return briefing.weather_line(settings) or "I couldn't reach the weather service just now, sir."
    return briefing.compose(settings)
