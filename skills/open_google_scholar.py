# Evolved by JARVIS on 2026-09-17 13:03 for: connect to google scholar
import re

SKILL = {'name': 'open_google_scholar', 'description': 'Open Google Scholar in the default web browser.', 'triggers': ['\\bgoogle\\s+scholar\\b'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Detect intent; any request containing the trigger is enough.
    if not re.search(r"google\s+scholar", request, re.IGNORECASE):
        return "Please ask me to open Google Scholar."

    url = "https://scholar.google.com"
    result = context["actions"].open_url(url)
    # The broker may return a confirmation string or None.
    return result or f"Opened Google Scholar at {url}."
