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
    "version": 2,
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


# Words that describe a doc rather than name one ("a random doc", "a new document", "google docs").
_GENERIC = {"a", "an", "the", "my", "new", "random", "blank", "empty", "fresh", "google", "doc", "docs",
            "document", "documents", "one", "any", "some", "and", "it", "this", "that"}


def _doc_name(request):
    """The name of an EXISTING doc referenced for read/edit/open ('my doc Report', 'the doc called X',
    'open INS document of hope'). None when the request only describes a doc ('a random doc')."""
    pats = (
        # "open X on (my) google docs" / "find my X" - the whole X, even when it contains "document"
        r"\b(?:open|read|show|summari[sz]e|edit|find)\s+(?:my\s+|the\s+)?(.+?)\s+(?:on|in|from)\s+(?:my\s+)?"
        r"google\s+(?:docs?|drive)\b",
        r"\bgoogle\s+docs?\b.*?\b(?:open|find|read|show)\s+(?:my\s+|the\s+)(.+?)(?:[.\n?,]|$)",
        r"\bdoc(?:ument)?\s+(?:called|named|titled)\s+[\"']?(.+?)[\"']?(?:\s+(?:and|to|with|on|in)\b|[.\n?,]|$)",
        r"\b(?:in|to|into|from)\s+(?:my\s+|the\s+)?(?:google\s+)?doc(?:ument)?\s+[\"']?(.+?)[\"']?(?:[.\n?,]|$)",
        r"\b(?:open|read|show|summari[sz]e|edit)\s+(?:my\s+|the\s+)?(.+?)\s+(?:google\s+)?doc(?:ument)?\b",
        r"\b(?:open|read|show|summari[sz]e|edit)\s+(?:my\s+|the\s+)?(?:google\s+)?doc(?:ument)?\s+"
        r"[\"']?(.+?)[\"']?(?:\s+(?:on|in|from)\s+(?:my\s+)?google\b|[.\n?,]|$)",
        r"\b(?:my|the)\s+(.+?)\s+(?:google\s+)?doc(?:ument)?\b",
    )
    for pat in pats:
        m = re.search(pat, request, re.IGNORECASE)
        if not m:
            continue
        name = re.sub(r"\s+(?:on|in|from)\s+(?:my\s+)?google(?:\s+docs?|\s+drive)?$", "", m.group(1).strip(),
                      flags=re.IGNORECASE).strip(" \"'")
        words = [w for w in re.findall(r"[\w'&-]+", name.lower())]
        if words and not all(w in _GENERIC for w in words) and len(name) <= 60 \
                and not re.search(r"\b(?:and|type|write|put|with)\b", name, re.IGNORECASE):
            return name
    return None


def _search_query(request):
    m = re.search(r"\b(?:search|find|look\s+for|for)\b[^.\n]*?\bfor\s+(.+?)(?:[.\n?]|$)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("\"'")
    m = re.search(r"\bdocs?\s+(?:for|about|matching|with|named|called)\s+(.+?)(?:[.\n?]|$)", request, re.IGNORECASE)
    return m.group(1).strip().strip("\"'") if m else None


_CONTENT_VERB = re.compile(r"\b(?:type|write|put|add|append|insert|paste|fill)\b", re.IGNORECASE)
_WRITE_SYSTEM = ("You write exactly the text a user asked to have put into a document - nothing else: no "
                 "preamble like 'Sure' or 'Here is', no quotes around it, no explanation. If they asked for "
                 "something of your choice (a fun fact, a poem, a random message), pick one yourself.")


_DESCRIPTIVE = re.compile(r"^(?:in\s+)?(?:a|an|some|the|random|one|something|anything|your)\b|\bof\s+your\s+choice\b|"
                          r"\b(?:random|something|fact|poem|story|essay|message|summary|paragraph|joke|list)\b",
                          re.IGNORECASE)


def _content_for(request, ask):
    """The text to put in a doc, from the request: a quoted/explicit piece is used verbatim, anything
    descriptive ('a fun fact of your choice', 'a message') is written by the model."""
    q = re.search(r"[\"“](.+?)[\"”]", request)
    if q:
        return q.group(1).strip()
    m = re.search(r"\b(?:saying|that\s+says|the\s+text\s+is)\s*:?\s*(.+)$", request, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # "type hello world into my doc Notes" -> literally "hello world"
    m = re.search(r"\b(?:type|write|put|add|append|insert|paste)\s+(.+?)\s+(?:in|into|to|on)\s+(?:my\s+|the\s+|a\s+)?"
                  r"(?:new\s+|random\s+)?(?:google\s+)?doc", request, re.IGNORECASE)
    if m and not _DESCRIPTIVE.search(m.group(1)):
        return m.group(1).strip()
    if not ask:
        return ""
    text = str(ask(f"The user said: {request!r}\nWrite the text that should go into the document.",
                   system=_WRITE_SYSTEM, temperature=0.7, max_tokens=900)).strip()
    return "" if text.startswith("[") else text


def _short_title(text, ask):
    first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
    if first and len(first) <= 60 and text.lstrip().startswith("#"):
        return first
    if ask:
        t = str(ask(f"Give a 2-5 word title for this document, and nothing else:\n\n{text[:800]}",
                    temperature=0.3, max_tokens=300)).strip().split("\n")[0].strip("\"'#. ")
        if t and not t.startswith("[") and len(t) <= 60:
            return t
    return "JARVIS note"


def _link(result):
    m = re.search(r"https://docs\.google\.com/\S+", result or "")
    return m.group(0) if m else None


def _open(actions, result):
    """Open the doc a create/append returned, so the user sees it (not just a link in the panel)."""
    url = _link(result)
    if url:
        try:
            actions.open_url(url)
            actions.remember_focus("url", url)
        except Exception:
            pass
    return result


def run(request, context):
    low = request.lower()
    actions = _actions(context)
    ask = context.get("llm")
    dry = context.get("dry_run")

    name = _doc_name(request)
    wants_create = bool(re.search(r"\b(?:create|make|new|start|generate|draft|random|blank)\b", low)
                        or re.search(r"\bwrite\s+(?:me\s+)?(?:a|an)\s+(?:new\s+)?(?:google\s+)?doc", low))
    wants_content = bool(_CONTENT_VERB.search(request))
    wants_open = bool(re.search(r"\b(?:open|show|go\s+to|launch)\b", low))
    wants_read = bool(re.search(r"\b(?:read|summari[sz]e|what'?s\s+in|get\s+me|tell\s+me\s+what)\b", low))
    wants_search = bool(re.search(r"\b(?:search|find|look\s+for|list)\b", low))

    # 1. New doc: "make a random doc and put a fun fact", "open google docs and type a message",
    #    "create a google doc titled Trip Plan about Japan". Written, then opened for the user to see.
    if wants_create or (wants_content and not name):
        title = _title(request)
        if dry:
            return f"Would create a Google Doc '{title or 'new doc'}' and open it."
        topic = _topic(request) if title else None
        if title and topic and ask and not wants_content:
            content = str(ask(f"Write a Google Doc titled {title!r} about: {topic}", system=_DOC_SYSTEM,
                              temperature=0.4, max_tokens=1500)).strip()
        else:
            content = _content_for(request, ask) if (wants_content or not title) else ""
        if content.startswith("["):
            content = ""
        title = title or (_short_title(content, ask) if content else "Untitled")
        return _open(actions, actions.create_doc(title, content))

    # 2. Add to an existing doc: "type hello into my doc Notes".
    if name and wants_content:
        if dry:
            return f"Would add text to the Google Doc '{name}' and open it."
        text = _content_for(request, ask)
        if not text:
            return f"What should I add to '{name}'?"
        return _open(actions, actions.append_to_doc(name, text))

    # 3. Open an existing doc in the browser: "open INS document of hope on google docs".
    if name and wants_open and not wants_read:
        if dry:
            return f"Would open the Google Doc '{name}'."
        found = actions.search_docs(name)
        url = _link(found)
        if not url:
            return found
        actions.open_url(url)
        actions.remember_focus("url", url)
        title = found.splitlines()[1].lstrip("- ").rsplit("  http", 1)[0] if "\n" in found else name
        return f"Opened '{title}'."

    # 4. Just "open google docs" -> the Docs home page.
    if wants_open and not name and not wants_read and not wants_search:
        return "Would open Google Docs." if dry else actions.open_url("https://docs.google.com/document/u/0/")

    # 5. Read / summarise an existing doc.
    if wants_read and not wants_search:
        name = name or _search_query(request)
        if not name:
            return "Which Google Doc should I read? e.g. 'read my google doc Trip Plan'."
        if dry:
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

    # 6. Search.
    if dry:
        return "Would search your Google Docs."
    query = _search_query(request) or _title(request) or name
    if not query:
        return "What should I search your Google Docs for?"
    return actions.search_docs(query)
