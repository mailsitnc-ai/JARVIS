"""Show a popup message box on screen (Windows MessageBox), via the gated notify action."""
import re

SKILL = {
    "name": "popup",
    "description": "Show a popup / alert / notification, e.g. 'popup saying hi' or 'show a message box: done'.",
    "triggers": [
        r"\bpop\s*-?\s*up\b",
        r"\bmessage\s*box\b",
        r"\b(?:show|display|give)\b[^.]*\b(?:alert|notification|notice)\b",
        r"\b(?:alert|notify)\s+me\b",
    ],
    "version": 1,
    "origin": "builtin",
}

_MSG = re.compile(
    r"(?:saying|that\s+says|which\s+says|says|with\s+the\s+(?:message|text)|message|text|:)\s*[\"“”']?(.+?)[\"“”']?\s*$",
    re.IGNORECASE,
)
_TITLE = re.compile(r"\btitled?\s+[\"“”']?(.+?)[\"“”']?\s*(?:saying|that\s+says|says|with|:|$)", re.IGNORECASE)
# A leading run of command/filler words that carry no message ("show a popup", "alert me the ...").
_LEAD = re.compile(r"^(?:please|can\s+you|could\s+you|show|display|give|make|create|pop\s*-?\s*up|popup|"
                   r"a|an|the|me|message\s*box|message|box|alert|notification|notice|notify|with|that|which|"
                   r"say(?:s|ing)?|:|,|-|\s)+", re.IGNORECASE)


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").strip().strip("\"“”'").strip())


def run(request, context):
    match = _MSG.search(request)
    message = _clean(match.group(1)) if match else _clean(_LEAD.sub("", request))
    if not message:
        return "What should the popup say?"
    title_match = _TITLE.search(request)
    title = _clean(title_match.group(1)) if title_match else "JARVIS"
    if context.get("dry_run"):
        return f'Would show a popup: "{message}"'
    return context["actions"].notify(message, title)
