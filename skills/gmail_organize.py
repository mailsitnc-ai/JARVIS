"""Organize Gmail: archive, mark read, trash (reversible), or label messages matching a description.

Never sends mail and never permanently deletes. Needs Google connected with write scope
(jarvis google login after the scope bump). Read/search stays in the gmail_search skill.
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "gmail_organize",
    "description": "Organize your inbox: archive / mark read / trash / label emails, e.g. "
                   "'archive emails from noreply@x.com' or 'label emails from boss as Work'.",
    "triggers": [
        r"\b(?:archive|trash|delete|label|tag)\b[^.]*\b(?:email|emails|mail|inbox|gmail|message|messages)\b",
        r"\bmark\b[^.]*\bread\b[^.]*\b(?:email|emails|mail|inbox)\b",
        r"\b(?:clean\s*up|organi[sz]e|tidy|triage|sort)\b[^.]*\b(?:inbox|email|emails|gmail)\b",
    ],
    "version": 1,
    "origin": "builtin",
}
_CATEGORIES = {"promotions": "category:promotions", "social": "category:social",
               "updates": "category:updates", "forums": "category:forums"}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _action(low):
    if "archive" in low:
        return "archive"
    if "trash" in low or "delete" in low or "bin" in low:
        return "trash"
    if "mark" in low and "read" in low:
        return "read"
    if "label" in low or "tag" in low:
        return "label"
    return None


_UNIT = {"day": "d", "week": "w", "month": "m", "year": "y"}
# Where a "from ..." sender phrase ends: at a time clause, an "as/about" clause, or a connector.
# A period only ends it when followed by space/end (so it never splits an email like noreply@x.com).
_FROM_STOP = (r"\s+(?:as|about|older|newer|that|which|labell?ed|tagged|and|to|before|after|in\s+the)\b"
              r"|[,;]|\.(?=\s|$)|$")


def _query(request):
    low = request.lower()
    parts = []
    m = re.search(rf"\bfrom\s+(.+?)(?:{_FROM_STOP})", request, re.IGNORECASE)
    if m:
        parts.append(f"from:{m.group(1).strip().rstrip('.,;')}")
    for word, q in _CATEGORIES.items():
        if word in low:
            parts.append(q)
    if "newsletter" in low:
        parts.append("(newsletter OR unsubscribe)")
    if "unread" in low:
        parts.append("is:unread")
    # "older than / newer than / in the last  N  day|week|month|year(s)"
    for kind, key in (("older", "older_than"), ("newer", "newer_than")):
        t = re.search(rf"{kind}\s+than\s+(\d+)\s*(day|week|month|year)", low)
        if t:
            parts.append(f"{key}:{t.group(1)}{_UNIT[t.group(2)]}")
    recent = re.search(r"(?:in\s+the\s+)?last\s+(\d+)\s*(day|week|month|year)", low)
    if recent and not any(p.startswith("newer_than") for p in parts):
        parts.append(f"newer_than:{recent.group(1)}{_UNIT[recent.group(2)]}")
    about = re.search(r"\babout\s+(.+?)(?:\s+as\s+|$)", request, re.IGNORECASE)
    if about and not parts:
        parts.append(about.group(1).strip().rstrip(".,;"))
    return " ".join(parts).strip()


def _label(request):
    # The label is the text after the trailing "as / labelled as / tagged as" - never the leading verb.
    m = re.search(r"\b(?:labell?ed\s+as|tagged\s+as|as)\s+([\w][\w /&+.-]*?)\s*$", request, re.IGNORECASE)
    return m.group(1).strip() if m else None


def run(request, context):
    low = request.lower()
    action = _action(low)
    if action is None:
        return "Tell me what to do with them: archive, mark read, trash, or label. " \
               "e.g. 'archive emails from noreply@x.com'."
    label = _label(request) if action == "label" else None
    if action == "label" and not label:
        return "What label should I apply? e.g. 'label emails from boss@x.com as Work'."
    query = _query(request)
    if not query:
        return "Which emails? Add something like 'from someone', 'promotions', 'unread', or 'older than 30 days'."
    if context.get("dry_run"):
        return f"Would {action} emails matching '{query}'" + (f" as '{label}'" if label else "") + "."
    return _actions(context).gmail_organize(query, action, label)
