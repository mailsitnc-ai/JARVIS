# Evolved by JARVIS on 2026-09-15 13:44 for: for best butter chicken recipes and return the results here
import re

SKILL = {'name': 'web_search_results', 'description': 'Search the web for a query and return a concise list of top result titles and URLs.', 'triggers': ['\\brecipes?\\b'], 'version': 1, 'origin': 'evolved'}


def _extract_query(request: str) -> str:
    """
    Pull a search query from the user's request.
    Looks for phrases after keywords like 'for', 'search', 'find', or at the end of the sentence.
    """
    # Try to capture after common verbs
    m = re.search(r"\b(?:search|find|look\s+for|show|give|list)\s+(.*)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip(' .!?')
    # Fallback: take everything after the first occurrence of the trigger word 'recipe' or 'recipes'
    m = re.search(r"\brecipes?\b\s+(.*)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip(' .!?')
    # As a last resort, return the whole request stripped of punctuation
    return request.strip(' .!?')


def run(request: str, context: dict) -> str:
    query = _extract_query(request)
    if not query:
        return "I couldn't determine what you want to search for. Please specify a query."

    # Use an existing web search skill via the broker
    # The exact phrasing depends on the installed skill; we use a generic request.
    search_result = context["run"](f"search for {query}")

    # If the other skill returns plain text, we try to extract titles and URLs.
    # Expecting lines like: "Title - URL"
    lines = [ln.strip() for ln in search_result.splitlines() if ln.strip()]
    if not lines:
        return f"I searched for \"{query}\" but didn't get any results."

    # Build a concise list (up to 5 items)
    summary_items = []
    for line in lines[:5]:
        # Simple split on dash or pipe if present
        if " - " in line:
            title, url = line.split(" - ", 1)
        elif " | " in line:
            title, url = line.split(" | ", 1)
        else:
            # If no clear separator, treat whole line as title
            title, url = line, ""
        title = title.strip()
        url = url.strip()
        if url:
            summary_items.append(f"• {title}: {url}")
        else:
            summary_items.append(f"• {title}")

    summary = "\n".join(summary_items)
    return f"Top results for \"{query}\":\n{summary}"
