"""Search the web in the default browser, through the permission broker."""
import re
from urllib.parse import quote_plus

from core.actions import ActionBroker

SKILL = {
    "name": "web_search",
    "description": "Search the web for something in the default browser.",
    "triggers": [r"^\s*(?:please\s+)?(?:search|google|bing|look\s+up)\s+(?:the\s+web\s+|online\s+)?(?:for\s+)?\S+"],
    "version": 1,
    "origin": "builtin",
}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    query = re.sub(r"^\s*(?:please\s+)?(?:search|google|bing|look\s+up)\s+(?:the\s+web\s+|online\s+)?(?:for\s+)?",
                   "", request, flags=re.IGNORECASE).strip(" ?.!")
    if not query:
        return "What should I search for?"
    url = "https://www.google.com/search?q=" + quote_plus(query)
    result = _actions(context).open_url(url)
    return result.replace(f"open {url}", f"search the web for '{query}'").replace(f"Opening {url}",
                                                                                 f"Searching the web for '{query}'")
