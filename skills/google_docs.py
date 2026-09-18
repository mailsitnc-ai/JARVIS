"""Work with Google Docs in the user's account: search, read/summarise, create (formatted), and edit.

Uses the Docs API (needs Google connected with the documents scope: re-run `jarvis google login` after
the scope was added). Creating a doc writes structured text - a title line plus '#'/'##' headings become
real Google Docs heading styles, so new docs come out formatted, not a wall of text.
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "google_docs",
    "description": "Search, read, create (formatted) or edit your Google Docs, e.g. 'search my google "
                   "docs for the budget', 'create a google doc titled Trip Plan about Japan', 'add a "
                   "conclusion to my google doc Report'.",
    "triggers": [
        r"\bgoogle\s+docs?\b",
        r"\b(?:search|find|read|open|summari[sz]e|create|make|new|edit|add\s+to|append|type|write)\b"
        r"[^.\n]*\bdoc(?:ument)?s?\b[^.\n]*\b(?:drive|google)\b",
        r"\b(?:in|to)\s+(?:my\s+)?google\s+doc",
        r"\bsearch\b[^.\n]*\bmy\s+docs?\b",
    ],
    "version": 1,
    "origin": "builtin",
}
_DOC_SYSTEM = ("You write the text contents of a Google Doc. Use '# ' for the title line, '## ' for "
               "section headings, and plain paragraphs for body text. No markdown bold/italic, no code "
               "fences - just the document text with those heading markers. Make it well-structured.")


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _title(request):
    m = re.search(r"\b(?:titled|called|named|title[:\-]?)\s+[\"']?(.+?)[\"']?"
                  r"(?:\s+(?:about|on|with|containing|that|and|to|for|saying)\b|[.\n]|$)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("\"'")
    m = re.search(r"\bdoc(?:ument)?\s+about\s+(.+?)(?:[.\n]|$)", request, re.IGNORECASE)
    return m.group(1).strip().strip("\"'").title() if m else None


def _topic(request):
    m = re.search(r"\b(?:about|on|regarding|containing|that\s+says|saying|with)\s+(.+?)(?:[.\n]|$)",
                  request, re.IGNORECASE)
    return m.group(1).strip().strip("\"'") if m else None


def _doc_name(request):
    """The name of an EXISTING doc referenced for read/edit ('my doc Report', 'the doc called X')."""
    m = re.search(r"\b(?:doc(?:ument)?)\s+(?:called|named|titled\s+)?[\"']?([\w][\w '&\-]{1,60}?)[\"']?"
                  r"(?:\s+(?:about|and|to|with|in|on)\b|[.\n?]|$)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(?:in|to|from)\s+(?:my\s+)?(?:google\s+)?doc(?:ument)?\s+[\"']?([\w][\w '&\-]{1,60}?)[\"']?"
                  r"(?:[.\n?]|$)", request, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _search_query(request):
    m = re.search(r"\b(?:search|find|look\s+for|for)\b[^.\n]*?\bfor\s+(.+?)(?:[.\n?]|$)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("\"'")
    m = re.search(r"\bdocs?\s+(?:for|about|matching|with|named|called)\s+(.+?)(?:[.\n?]|$)", request, re.IGNORECASE)
    return m.group(1).strip().strip("\"'") if m else None


def run(request, context):
    low = request.lower()
    actions = _actions(context)
    ask = context.get("llm")

    is_append = bool(re.search(r"\b(?:add|append|insert|type|put)\b", low)
                     or re.search(r"\bwrite\b[^.\n]*\b(?:in|into|to)\s+(?:my\s+|the\s+)?(?:google\s+)?doc", low))
    is_create = bool(re.search(r"\b(?:create|make|new|start|generate|draft)\b", low)
                     or re.search(r"\bwrite\s+(?:me\s+)?(?:a|an|the)?\s*(?:new\s+)?(?:google\s+)?doc", low))
    is_read = bool(re.search(r"\b(?:read|open|show|summari[sz]e|what'?s\s+in|get\s+me)\b", low))
    is_search = bool(re.search(r"\b(?:search|find|look\s+for|list)\b", low))

    # 1. Edit an existing doc.
    if is_append and not is_create:
        name = _doc_name(request)
        if not name:
            return "Which Google Doc should I add to? e.g. 'add a summary to my google doc Report'."
        m = re.search(r"\b(?:add|append|insert|type|put|write)\s+(.+?)\s+(?:to|into|in)\b", request, re.IGNORECASE)
        text = (m.group(1).strip().strip("\"'") if m else "").strip()
        if text and ask and len(text.split()) <= 4:   # a short topic -> expand it into real content
            text = str(ask(f"Write a section to add to a Google Doc. Topic: {text}", system=_DOC_SYSTEM,
                           temperature=0.4, max_tokens=800)).strip()
        if not text:
            return "What should I add to the doc?"
        return "Would edit the doc." if context.get("dry_run") else actions.append_to_doc(name, text)

    # 2. Create a new (formatted) doc.
    if is_create:
        title = _title(request) or _topic(request) or "Untitled"
        if context.get("dry_run"):
            return f"Would create a Google Doc '{title}'."
        topic = _topic(request)
        content = ""
        if topic and ask:
            content = str(ask(f"Write a Google Doc titled {title!r} about: {topic}", system=_DOC_SYSTEM,
                              temperature=0.4, max_tokens=1500)).strip()
        elif ask and not _title(request):
            content = str(ask(f"Write a Google Doc for this request: {request}", system=_DOC_SYSTEM,
                              temperature=0.4, max_tokens=1500)).strip()
        if content and not content.startswith(("# ", "#\t")):
            content = f"# {title}\n\n{content}"     # ensure a styled title line
        return actions.create_doc(title, content)

    # 3. Read / summarise an existing doc.
    if is_read and not is_search:
        name = _doc_name(request) or _search_query(request)
        if not name:
            return "Which Google Doc should I read? e.g. 'read my google doc Trip Plan'."
        if context.get("dry_run"):
            return f"Would read the Google Doc '{name}'."
        text = actions.read_doc(name)
        if not isinstance(text, str) or text.startswith(("I couldn't find", "Google isn't", "[Google")):
            return text
        if re.search(r"\bsummari[sz]e\b", low) and ask:
            summary = ask(f"Summarise this Google Doc concisely:\n\n{text[:4000]}",
                          system="You summarise documents in a few clear sentences.")
            if summary and not summary.startswith("["):
                return summary.strip()
        return text

    # 4. Search.
    if context.get("dry_run"):
        return "Would search your Google Docs."
    query = _search_query(request) or _title(request) or _doc_name(request)
    if not query:
        return "What should I search your Google Docs for?"
    return actions.search_docs(query)
