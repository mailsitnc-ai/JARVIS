# Evolved by JARVIS on 2026-09-15 14:24 for: web search for post quantum cryptography
import re
import urllib.parse

SKILL = {'name': 'web_search_2', 'description': 'Perform a web search for a given query using the default browser.', 'triggers': ['\\bsearch\\b', '\\bweb\\s+search\\b'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Try to capture the query after common phrases
    patterns = [
        r"\bsearch\s+for\s+(.+)",
        r"\bweb\s+search\s+for\s+(.+)",
        r"\bsearch\s+(.+)",
        r"\bweb\s+search\s+(.+)",
    ]
    query = ""
    for pat in patterns:
        m = re.search(pat, request, re.IGNORECASE)
        if m:
            query = m.group(1).strip()
            break

    if not query:
        return "Please tell me what you want to search for."

    # Encode the query for a URL
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?q={encoded}"

    # Open the URL in the default browser via the broker
    result = context["actions"].open_url(url)

    # Return a friendly confirmation
    return result or f"Opened a web search for \"{query}\"."
