"""Open a new browser tab - blank, at a named web app (Google Docs, Gmail...), or at a site."""
import re

from core.actions import ActionBroker, strip_browser_suffix, web_url_for

SKILL = {
    "name": "browser_tab",
    "description": "Open a new browser tab, optionally at a website or web app like Google Docs or Gmail.",
    "triggers": [
        r"\bnew\s+tab\b",
        r"\bopen\s+(?:a\s+)?tab\b",
        r"\btab\s+(?:for|on|of|to)\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def _target(request):
    text = strip_browser_suffix(request)
    # Drop the "open a new tab (for/of/to)" framing, leaving the site/app name if there is one.
    text = re.sub(r"^\s*(?:please\s+)?(?:open|launch|start)?\s*(?:a\s+|another\s+)?(?:new\s+)?tab\s*(?:for|of|to|with|at)?\s*",
                  "", text, flags=re.IGNORECASE)
    return text.strip(" .!?")


def run(request, context):
    actions = context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))
    target = _target(request)
    if target:
        url = web_url_for(target)
        if url:
            return actions.open_url(url)
    return actions.open_browser()  # nothing specific -> a blank new tab
