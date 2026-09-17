"""Search the web - on Google by default, or on a named site (Scholar, YouTube, GitHub, Wikipedia...).

Opens a real results page in your browser (via the broker, so it uses your chosen browser). It also
reads the conversation focus: a bare "search X" right after you opened a site searches THAT site, so
"open google scholar ... search super capacitors" returns scholar results, not a generic web search.
"""
import re
from urllib.parse import quote_plus, urlparse

from core.actions import ActionBroker

SKILL = {
    "name": "web_search",
    "description": "Search the web for something in the default browser, or a specific site (Google "
                   "Scholar, YouTube, GitHub, Wikipedia, Amazon, Reddit, Stack Overflow, Maps), e.g. "
                   "'search scholar for supercapacitors'.",
    "triggers": [r"^\s*(?:please\s+)?(?:search|google|bing|look\s+up)\b"],
    "version": 2,
    "origin": "builtin",
}

# alias -> results-page URL template ({q} = url-encoded query).
_SITES = {
    "google": "https://www.google.com/search?q={q}",
    "the web": "https://www.google.com/search?q={q}",
    "web": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "google scholar": "https://scholar.google.com/scholar?q={q}",
    "scholar": "https://scholar.google.com/scholar?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "github": "https://github.com/search?q={q}&type=repositories",
    "amazon": "https://www.amazon.com/s?k={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "stack overflow": "https://stackoverflow.com/search?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "google maps": "https://www.google.com/maps/search/{q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "google drive": "https://drive.google.com/drive/search?q={q}",
    "drive": "https://drive.google.com/drive/search?q={q}",
    "twitter": "https://x.com/search?q={q}",
    "x": "https://x.com/search?q={q}",
}
_ALIASES = sorted(_SITES, key=len, reverse=True)  # match "google scholar" before "google"/"scholar"
# host substring -> alias, to infer the site from the last URL opened (the conversation focus).
_HOST_SITE = {"scholar.google": "scholar", "youtube.": "youtube", "github.": "github",
              "wikipedia.": "wikipedia", "amazon.": "amazon", "reddit.": "reddit",
              "stackoverflow.": "stackoverflow", "maps.google": "maps", "drive.google": "drive",
              "x.com": "x", "twitter.": "twitter", "bing.": "bing"}
_GENERIC = {"google", "web", "the web", "bing"}  # not really a "specific site"


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _strip_verb(text):
    m = re.match(r"^\s*(?:please\s+)?(?:search|google|bing|look)\s+(?:up\s+)?(?:on\s+)?"
                 r"(?:the\s+web\s+|online\s+|for\s+)?", text, re.IGNORECASE)
    return text[m.end():].strip() if m else text.strip()


def _detect_site(rest):
    """Return (site_alias | None, query). Recognises '<site> for <q>' and '<q> on <site>'."""
    low = rest.lower()
    # "<query> on/in <site>"
    tail = re.search(r"\b(?:on|in|using|via|through)\s+(?:my\s+|the\s+)?([a-z .]+?)\s*$", low)
    if tail:
        cand = tail.group(1).strip()
        for alias in _ALIASES:
            if cand == alias:
                return alias, rest[:tail.start()].strip(" ?.!,")
    # "<site> for <query>"
    for alias in _ALIASES:
        m = re.match(rf"^{re.escape(alias)}\s+for\s+(.+)$", rest, re.IGNORECASE)
        if m:
            return alias, m.group(1).strip(" ?.!,")
    return None, rest.strip(" ?.!,")


def _site_from_focus(context):
    focus = context.get("focus") or {}
    url = focus.get("url") or ""
    host = urlparse(url).netloc.lower() if url else ""
    for needle, alias in _HOST_SITE.items():
        if needle in host:
            return alias
    return None


def run(request, context):
    rest = _strip_verb(request)
    rest = re.sub(r"^(?:the\s+web|online|the\s+internet)\b\s*(?:for\s+)?", "", rest, flags=re.IGNORECASE).strip()
    site, query = _detect_site(rest)
    query = re.sub(r"^for\s+", "", query, flags=re.IGNORECASE).strip(" ?.!,")
    if not query:
        return "What should I search for?"
    if site is None or site in _GENERIC:
        inferred = None if _explicit_web(request) else _site_from_focus(context)
        site = inferred or ("google" if site is None else site)
    url = _SITES.get(site, _SITES["google"]).format(q=quote_plus(query))
    label = "the web" if site in _GENERIC else site
    result = _actions(context).open_url(url)
    if not isinstance(result, str):
        return f"Searching {label} for '{query}'."
    return (result.replace(f"Opening {url}", f"Searching {label} for '{query}'")
                  .replace(f"Would open {url} in your browser", f"Would search {label} for '{query}'"))


def _explicit_web(request):
    """True when the user explicitly said 'the web'/'online' - don't override that with the focus site."""
    return bool(re.search(r"\b(?:the\s+web|online|the\s+internet)\b", request, re.IGNORECASE))
