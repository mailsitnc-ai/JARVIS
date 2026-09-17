"""Control a real Chrome tab's DOM: browse, read the page, click things, type/fill, screenshot.

Drives JARVIS's own Chrome window over the DevTools protocol (core/browser.py), gated by the 'browser'
capability. This is the DOM access that plain "open a URL" can't do - it reads and acts on the live page.

Examples:
  "browse to news.ycombinator.com"     -> open it in the controllable tab
  "read the page" / "summarise this page"
  "click the Sign in button"
  "type hello world in the search box and press enter"
  "screenshot the page"
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "browser_control",
    "description": "Control the browser's page: browse to a site, read/summarise it, click a button or "
                   "link, type into a field, or screenshot the page.",
    "triggers": [
        r"\b(?:browse|navigate|go)\s+to\b\s+\S",
        r"\b(?:read|summari[sz]e|what'?s\s+on|scrape|extract)\b[^.]*\b(?:page|tab|site|website|article)\b",
        r"\bclick\b\s+\S",
        r"\b(?:type|enter|fill(?:\s+in)?|put)\b[^.]*\b(?:in|into|on)\b[^.]*\b(?:page|box|field|bar|search|form|it|input)\b",
        r"\bsearch\b[^.]*\bon\s+(?:this|the)\s+(?:page|site|tab)\b",
        r"\bscreenshot\b[^.]*\b(?:page|tab|site)\b",
        r"\bon\s+the\s+(?:page|tab|site|website)\b",
        r"\bcontrol\b[^.]*\bbrowser\b",
    ],
    "version": 1,
    "origin": "builtin",
}
_URL = re.compile(r"(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?", re.IGNORECASE)
_SUBMIT = re.compile(r"\b(?:press\s+enter|hit\s+enter|and\s+submit|then\s+search|and\s+search|and\s+go)\b", re.IGNORECASE)


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    actions = _actions(context)
    dry = context.get("dry_run")
    low = request.lower()

    # 1. Browse / go to a URL in the controllable tab.
    m = re.search(r"\b(?:browse|navigate|go)\s+to\b\s+(.+?)[\s.!?]*$", request, re.IGNORECASE)
    if m:
        url = m.group(1).strip().strip("\"'")
        hit = _URL.search(url)
        if not hit:
            return None  # "go to bed" isn't a browse command
        if dry:
            return f"Would browse to {url} in the controllable browser."
        return actions.browser_open(hit.group(0))

    # 2. Click something on the page.
    m = re.match(r"^\s*(?:please\s+)?click\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+(?:button|link|tab|icon))?[\s.!?]*$",
                 request, re.IGNORECASE)
    if m:
        target = m.group(1).strip().strip("\"'")
        return "Would click on the page." if dry else actions.browser_click(target)

    # 3. Type / fill / search into a field on the page.
    m = re.search(r"\b(?:type|enter|fill\s+in|fill|put|search(?:\s+up|\s+for)?)\s+(.+?)"
                  r"(?:\s+(?:in|into|on)\b.*)?$", request, re.IGNORECASE)
    if m and (re.search(r"\b(?:page|box|field|bar|search|form|input|it)\b", low) or "search" in low):
        text = m.group(1).strip().strip("\"'")
        text = re.sub(r"\s+(?:in|into|on)\s+(?:the\s+)?(?:search\s+)?(?:box|bar|field|form|page|input|it)\b.*$",
                      "", text, flags=re.IGNORECASE).strip()
        if text:
            submit = bool(_SUBMIT.search(request)) or "search" in low
            return "Would type on the page." if dry else actions.browser_type(text, submit=submit)

    # 4. Screenshot the current page.
    if re.search(r"\bscreenshot\b", low):
        return "Would screenshot the page." if dry else actions.browser_screenshot()

    # 5. Read / summarise the page.
    if re.search(r"\b(?:read|summari[sz]e|what'?s\s+on|scrape|extract|get)\b", low):
        if dry:
            return "Would read the current page."
        text = actions.browser_read()
        if not isinstance(text, str) or text.startswith("Browser control failed") or "unavailable" in text:
            return text
        if re.search(r"\bsummari[sz]e\b", low) and context.get("llm"):
            summary = context["llm"](f"Summarise this web page concisely:\n\n{text[:4000]}",
                                     system="You summarise web pages in a few clear sentences.")
            if summary and not summary.startswith("["):
                return summary.strip()
        return text if len(text) <= 1500 else text[:1500] + " ..."

    return None  # nothing matched -> let another skill handle it
