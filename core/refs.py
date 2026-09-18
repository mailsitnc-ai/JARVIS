"""Turn a back-reference into the concrete thing it points at, before routing.

"open it", "run that", "open the photo", "what's in the picture", "email it to me" - none of these
name a target, so skill triggers either miss them or grab the wrong thing (open an *app* called
"photo", search Gmail for the word "it"). This rewrites the reference to the actual path/URL that the
conversation focus (core/focus.py) is holding, so the normal machinery then does the right thing.

It is deliberately conservative:
  - it only rewrites when the focus actually has a matching referent;
  - it never rewrites something being CREATED ("take a photo", "make a file") - only things acted on;
  - a bare pronoun ("it"/"that") is resolved only in an imperative command, so questions like
    "is it done?" are left completely alone.
"""
from __future__ import annotations

import re

# Which focus slot each reference word points at.
_SLOT_WORDS = {
    "image": ("screenshot", "screen shot", "photo", "picture", "pic", "selfie", "image", "snapshot"),
    "url": ("link", "url", "webpage", "web page", "website", "web site", "page", "site"),
    "folder": ("folder", "directory"),
    # 'file' is last so plain words like "document/script" don't shadow the more specific ones above.
    # ("doc"/"docs" is left out on purpose - "open the docs" usually means Google Docs, not a local file.)
    "file": ("document", "script", "program", "file", "note", "spreadsheet", "video", "recording",
             "clip", "movie", "footage", "download", "gif",
             "the one you made", "the one you created", "the thing you made"),
}
# Verbs that make something NEW - a word after them isn't a back-reference ("take a photo").
_CREATE = ("take", "snap", "capture", "make", "create", "write", "generate", "build", "draw",
           "record", "save", "design", "produce", "shoot", "compose", "code", "new", "another")
# Imperative verbs that act on an existing thing - only then do we resolve a bare pronoun.
_CONSUME = ("open", "run", "launch", "start", "execute", "show", "display", "view", "read", "describe",
            "delete", "remove", "rename", "edit", "print", "play", "attach", "upload", "email", "send",
            "share", "post", "close", "reveal", "analyse", "analyze", "summarise", "summarize")

_DET = r"(?:the|that|this|these|those|my|it'?s)"
_PRONOUN = re.compile(r"\b(it|that|this|them|those|these)\b", re.IGNORECASE)
# A trailing relative clause on a reference: "the video (that) you just recorded", "the file I made".
_REL_VERB = ("made", "created", "wrote", "written", "saved", "built", "recorded", "captured", "took",
             "taken", "downloaded", "generated", "produced", "drew", "grabbed", "shot", "just")
_REL_CLAUSE = r"(?:\s+(?:that\s+|which\s+)?(?:you|i|we)\s+(?:just\s+)?(?:" + "|".join(_REL_VERB) + r")\b)?"


def _quote(value: str) -> str:
    return f'"{value}"' if (" " in value and not value.lower().startswith("http")) else value


def _slot_phrase_patterns():
    """(compiled 'the <word>' pattern, slot) pairs, longest phrases first so 'web page' beats 'page'."""
    pairs = []
    for slot, words in _SLOT_WORDS.items():
        for word in words:
            pairs.append((len(word), slot, word))
    pairs.sort(reverse=True)  # longest word first
    out = []
    for _n, slot, word in pairs:
        out.append((re.compile(rf"\b{_DET}\s+{re.escape(word)}s?{_REL_CLAUSE}\b", re.IGNORECASE), slot, word))
    return out


_SLOT_PATTERNS = _slot_phrase_patterns()
_CREATE_BEFORE = re.compile(r"(?:\b(?:" + "|".join(_CREATE) + r")\b\s+(?:\w+\s+){0,2})$", re.IGNORECASE)
_IMPERATIVE = re.compile(r"^\s*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|hey\s+|"
                         r"ok(?:ay)?\s+|jarvis[,\s]+)*(" + "|".join(_CONSUME) + r")\b", re.IGNORECASE)
# "what's in it", "what is on the screenshot", "whats inside that"
_WHATS_IN = re.compile(r"\bwhat(?:'?s| is| are)\s+(?:in|on|inside)\s+", re.IGNORECASE)
# An explicit absolute path is already a concrete target - don't touch references around it.
_ABS_PATH = re.compile(r"[A-Za-z]:[\\/]\S")

# --- literal message body protection -------------------------------------------------------------
# In a send/compose command the body is literal content the user is dictating ("...saying this is a
# test"), so a word like "this"/"it" in there must NEVER be rewritten to a focused path/URL.
_SEND_CMD = re.compile(r"\b(?:whats?app|google\s+chat|chat|\bdm\b|text|message|msg|email|e-?mail|"
                       r"tweet|post|reply|slack|say|tell|send)\b", re.IGNORECASE)
_BODY_LEADIN = re.compile(r"(?:\bsaying\b|\bthat\s+says\b|\bwhich\s+says\b|\bthe\s+(?:message|text)\s+is\b|"
                          r"\b(?:message|text)\s+is\b|:|\s[-–—]\s)", re.IGNORECASE)
_QUOTE = re.compile(r"[\"“‘]")  # a real opening quote starts a literal body too


def _body_start(text: str) -> int:
    """Index where a literal message body begins (everything from here is dictated content, left as-is),
    or len(text) when the command isn't a send/compose or gives no body."""
    if not _SEND_CMD.search(text):
        return len(text)
    starts = [len(text)]
    for rx in (_BODY_LEADIN, _QUOTE):
        m = rx.search(text)
        if m:
            starts.append(m.start())
    return min(starts)


def resolve_references(text: str, focus) -> tuple[str, bool]:
    """Return (possibly-rewritten text, changed?). `focus` is a core.focus.Focus (or anything with the
    same get()/most_recent() API). Never raises - on any doubt it returns the text unchanged."""
    if not text or focus is None:
        return text, False
    if _ABS_PATH.search(text):
        return text, False  # already names a concrete path; the skill will open it directly
    try:
        return _resolve(text, focus)
    except Exception:
        return text, False


def _resolve(text: str, focus) -> tuple[str, bool]:
    changed = False
    body_at = _body_start(text)  # don't rewrite anything from here on: it's a literal message body

    # 1. "the photo" / "that link" / "my file" -> the concrete referent for that slot.
    for pattern, slot, _word in _SLOT_PATTERNS:
        def repl(match):
            nonlocal changed
            if match.start() >= body_at:
                return match.group(0)  # inside the dictated message body - leave it alone
            before = text[:match.start()]
            if _CREATE_BEFORE.search(before):
                return match.group(0)  # "take another picture" - creating, not referring back
            value = focus.get(slot)
            if not value:
                return match.group(0)
            changed = True
            return _quote(value)
        text = pattern.sub(repl, text)

    # 2. A bare pronoun ("open it", "run that", "email it to me") in an imperative or a "what's in it"
    #    question -> the most recent referent. Left untouched everywhere else (incl. a message body).
    imperative = _IMPERATIVE.search(text)
    whats_in = _WHATS_IN.search(text)
    if (imperative or whats_in) and _PRONOUN.search(text):
        referent = focus.most_recent()
        if referent:
            anchor = imperative.end() if imperative else (whats_in.end() if whats_in else 0)
            m = _PRONOUN.search(text, anchor)
            if m and m.start() < body_at and not _is_dummy_it(text, m):
                text = text[:m.start()] + _quote(referent) + text[m.end():]
                changed = True

    return text, changed


def _is_dummy_it(text: str, match) -> bool:
    """True for a pronoun that isn't a real object we can open, so we never rewrite it. Covers 'is it
    done', 'it's ready', and a demonstrative subject like 'this is a test' / 'that was fun'."""
    rest = text[match.end():].lstrip()
    if rest[:3].lower().startswith(("'s", "'re", "'ll")):  # "it's ready", "they're", "it'll"
        return True
    nxt = re.match(r"[a-z']+", rest.lower())
    if nxt and nxt.group(0) in ("is", "was", "are", "were", "will", "would",
                                "could", "should", "s", "re", "ll"):  # "this is test", "that was fun"
        return True
    before = text[max(0, match.start() - 6):match.start()].lower()
    return bool(re.search(r"\b(is|was|are|were)\s+$", before))  # "is it done"
