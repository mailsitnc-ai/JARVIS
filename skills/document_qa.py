"""Read a document for you and summarise it, answer questions about it, or quiz you on it.

  'summarise it' (the file just found/made)       'summarise the population change pdf'
  'what does it say about the demographic transition'   'quiz me on it'   'key points of ~/Downloads/x.pdf'

Works with PDF, Word, RTF, Pages, text/markdown/code files. The file is: a path in the request, a file
found by name words, or else the file JARVIS last touched ("it").
"""
import re
from pathlib import Path

SKILL = {
    "name": "document_qa",
    "description": "Summarise a PDF/Word/text file, answer a question about it, list key points or quiz "
                   "you on it, e.g. 'summarise it', 'summarise the population change pdf', 'what does it "
                   "say about X', 'quiz me on it'. Works on the file just found or a named one.",
    "triggers": [
        r"\b(?:summari[sz]e|summary\s+of|tl;?dr|key\s+points|main\s+points|quiz\s+me|flash\s?cards?|"
        r"test\s+me)\b(?![^\n]*\bgoogle\s+docs?\b)[^\n]*\b(?:it|this|that|pdf|file|document|doc|docx|"
        r"notes|handout|worksheet|chapter|paper|essay|reading|article)\b",
        r"^\W*(?:summari[sz]e|tl;?dr|quiz\s+me)\W*$",
        # after "it" was resolved to the file JARVIS just found: "summarise /Users/me/x.pdf"
        r"\b(?:summari[sz]e|quiz\s+me|key\s+points|explain)\b[^\n]*(?:/|\\)[^\s]+\.(?:pdf|docx?|txt|md|rtf|"
        r"pages|odt)\b",
        r"\bwhat\s+does\s+(?:it|this|that|the\s+(?:pdf|file|document|handout|notes|paper))\s+say\b",
        r"\b(?:in|from)\s+(?:the|that|this)\s+(?:pdf|file|document|handout)\b[^.\n]*\?",
    ],
    "version": 1,
    "origin": "builtin",
}

_DOC_EXTS = ["pdf", "docx", "doc", "rtf", "pages", "txt", "md", "odt", "html", "py", "csv"]
_PATH = re.compile(r"(?:~|/Users/|/home/|[A-Za-z]:\\)[^\s'\"]+\.\w{1,5}")
_STOP = set("""summarise summarize summary of tldr key main points quiz me flashcards flashcard test on it this
    that the a an my pdf file document doc docx notes handout worksheet chapter paper essay reading article
    what does say about in from please jarvis give make of for with and to is are""".split())
_SYSTEM = ("You are JARVIS, helping a student with a document. Be accurate to the document - if it doesn't "
           "cover something, say so. Keep answers tight and well organised.")


def _target(request, context):
    m = _PATH.search(request)
    if m:
        p = Path(m.group(0)).expanduser()
        if p.is_file():
            return p
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'_-]*", request.lower()) if w not in _STOP and len(w) > 2]
    if words and not re.search(r"\b(?:it|this|that)\b\s*\W*$", request.lower()):
        actions = context["actions"]
        for kw in (dict(names=words, exts=_DOC_EXTS), dict(content=words, exts=_DOC_EXTS)):
            hits = actions.find_files(limit=1, **kw)
            if hits:
                return hits[0]
    focus = (context.get("focus") or {}).get("file")
    if focus and Path(str(focus)).is_file():
        return Path(str(focus))
    return None


def run(request, context):
    if context.get("dry_run"):
        return "Would read the document and answer."
    ask = context.get("llm")
    if ask is None:
        return "I need a language model to read documents, sir."
    target = _target(request, context)
    if target is None:
        return ("Which document, sir? Say 'find <something>' first and then 'summarise it', or name it, e.g. "
                "'summarise the population change pdf'.")
    actions = context["actions"]
    text = str(actions.read_document(str(target)))
    if not text.strip() or text.startswith(("There's no file", "I couldn't read")):
        return text or f"I couldn't get any text out of {target.name} (it may be a scanned image)."
    actions.remember_focus("file", target)
    low = request.lower()
    body = text[:40000]
    if re.search(r"\bquiz\s+me|\btest\s+me|\bflash\s?cards?\b", low):
        task = ("Write a 5-question quiz on this document (mix multiple-choice and short answer). List the "
                "questions first, then an 'Answers' section at the end.")
    elif re.search(r"\bkey\s+points|\bmain\s+points", low):
        task = "List the key points of this document as 5-8 short bullets."
    elif re.search(r"\bsummari[sz]e|\bsummary|\btl;?dr", low):
        task = "Summarise this document in one short paragraph, then 3-5 bullet points of the essentials."
    else:
        task = f"Answer this question using the document: {request}"
    out = str(ask(f"{task}\n\nDocument '{target.name}':\n{body}", system=_SYSTEM, temperature=0.3,
                  max_tokens=2000)).strip()
    if not out or out.startswith("[LLM unavailable"):
        return "I couldn't reach a model to read it just now, sir."
    cut = " (I read the first part - it's a long one.)" if len(text) > 40000 else ""
    return f"{target.name}:{cut}\n{out}"
