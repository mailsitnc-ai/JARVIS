"""Find files on your computer by describing them.

  'find the chemistry pdf I downloaded last week'   'where is my MUN position paper'
  'find my population pyramid handout'               'find photos from yesterday'   'open my resume'

Searches your home folder (macOS Spotlight, which also indexes the text INSIDE documents): tries the
words as file-name words first, then as words inside the documents, then without the date limit.
The first hit becomes "it", so 'summarise it' / 'open it' work next.
"""
import datetime as dt
import re

SKILL = {
    "name": "file_finder",
    "description": "Find a file on this computer from a description (name words, type, when), e.g. 'find "
                   "the chemistry pdf I downloaded last week', 'where is my MUN position paper', 'find "
                   "photos from yesterday'. Then say 'summarise it' or 'open it'.",
    "triggers": [
        r"^(?![^\n]*\b(?:google|drive|gmail|inbox|emails?|whatsapp)\b)[^\n]*"
        r"\b(?:find|locate|search\s+for|look\s+for|dig\s+up|where(?:'s|\s+is|\s+are|\s+did\s+i\s+(?:put|save)))\b"
        r"[^.\n]*\b(?:files?|pdfs?|documents?|docx?|notes|presentations?|pptx?|slides|spreadsheets?|xlsx|"
        r"images?|photos?|pictures?|screenshots?|videos?|essays?|worksheets?|handouts?|papers?|resume|cv|"
        r"assignments?|homework|downloads?)\b",
    ],
    "version": 1,
    "origin": "builtin",
}

_TYPES = {
    r"\bpdfs?\b": ["pdf"],
    r"\b(?:word\s+(?:docs?|files?|documents?)|docx?)\b": ["docx", "doc", "pages"],
    r"\b(?:presentations?|slides|pptx?|keynote|deck)\b": ["pptx", "ppt", "key"],
    r"\b(?:spreadsheets?|excel|xlsx|sheets?|csv)\b": ["xlsx", "xls", "numbers", "csv"],
    r"\b(?:images?|photos?|pictures?|pics?|screenshots?)\b": ["png", "jpg", "jpeg", "heic", "webp"],
    r"\bvideos?\b|\bclips?\b": ["mp4", "mov", "m4v"],
    r"\b(?:python|script)\b": ["py"],
}
_STOP = set("""a an the my me i i'm im find locate search look for dig up where is are was did put save saved
    file files document documents doc docs pdf pdfs word notes of from in on that which with about last this
    week month year today yesterday downloaded download downloads got made wrote created recent recently
    open show and to it some any all called named titled please can you could jarvis photo photos image
    images picture pictures screenshot screenshots video videos presentation slides spreadsheet ago days""".split())


def _days(low):
    if re.search(r"\btoday\b", low):
        return 1
    if re.search(r"\byesterday\b", low):
        return 2
    m = re.search(r"\b(\d+)\s+days?\s+ago\b|\blast\s+(\d+)\s+days?\b", low)
    if m:
        return int(m.group(1) or m.group(2)) + 1
    if re.search(r"\b(?:this|last|past)\s+week\b|\brecent(?:ly)?\b", low):
        return 8
    if re.search(r"\b(?:this|last|past)\s+month\b", low):
        return 32
    if re.search(r"\b(?:this|last|past)\s+year\b", low):
        return 366
    return None


def parse(request):
    """(name_words, extensions, days) from a description - deterministic, no model needed."""
    low = request.lower()
    exts = []
    for pat, e in _TYPES.items():
        if re.search(pat, low):
            exts += e
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'_-]*", low) if w not in _STOP and len(w) > 1
             and not re.fullmatch(r"\d+", w)]
    return words, exts, _days(low)


def _when(ts):
    d = dt.datetime.fromtimestamp(ts)
    today = dt.date.today()
    if d.date() == today:
        return "today " + d.strftime("%H:%M")
    if d.date() == today - dt.timedelta(days=1):
        return "yesterday"
    return d.strftime("%d %b")


def run(request, context):
    words, exts, days = parse(request)
    if context.get("dry_run"):
        return f"Would search your files for {words or exts}."
    if not words and not exts and not days:
        return "What should I look for, sir? Give me a word from the name or what it's about."
    actions = context["actions"]
    attempts = [dict(names=words, exts=exts, days=days)]
    if words:
        attempts.append(dict(content=words, exts=exts, days=days))          # words inside the document
    if days:
        attempts += [dict(names=words, exts=exts), dict(content=words, exts=exts)] if words else []
    if len(words) > 1:
        attempts.append(dict(names=words[:1] + words[-1:], exts=exts, days=days))
    hits = []
    for kw in attempts:
        hits = actions.find_files(limit=6, **kw)
        if hits:
            break
    if not hits:
        return "I couldn't find anything matching that, sir. Try a word from the file's name or its contents."
    from pathlib import Path
    home = str(Path.home())
    lines = []
    for i, p in enumerate(hits, 1):
        try:
            when = _when(p.stat().st_mtime)
        except OSError:
            when = "?"
        folder = str(p.parent).replace(home, "~")
        lines.append(f"{i}. {p.name}  —  {folder}  ({when})")
    first = hits[0]
    actions.remember_focus("file", first)
    low = request.lower()
    if re.search(r"^\W*(?:please\s+)?open\b|\band\s+open\s+it\b", low):
        actions.open_path(str(first))
        return f"Opened {first.name}.\n" + "\n".join(lines[1:4] and ["Other matches:"] + lines[1:4])
    if re.search(r"\b(?:show|reveal)\b.*\b(?:finder|folder)\b", low):
        actions.reveal_file(str(first))
    more = "" if len(hits) == 1 else " Say 'open it' for the first, or 'summarise it'."
    return f"Found {len(hits)}:\n" + "\n".join(lines) + ("\n" + more.strip() if more else "")
