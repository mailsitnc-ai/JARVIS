"""Type text into whatever app you're in - dictate instead of typing.

  'type hello everyone'        'type out: see you at 6'
  (by voice during hand control: "Jarvis, type the meeting is at four")
"""
import re

SKILL = {
    "name": "type_text",
    "description": "Type text into the app you're using (dictation), e.g. 'type hello everyone', "
                   "'type out see you at six'. Useful with hand control, when you'd rather speak than type.",
    "triggers": [
        # matches the whole request on purpose: an evolved 'keyboard_type' skill matches the same
        # phrasing, and the registry ranks by how much of the text a trigger covers
        r"^\W*(?:jarvis[,\s]+)?(?:please\s+)?type\s+(?:out\s+|in\s+|this[:,]?\s+|the\s+following[:,]?\s+)?.+$",
        r"^\W*(?:jarvis[,\s]+)?(?:write|enter|input)\s+(?:this|the\s+following)\s*[:,]\s*\S",
    ],
    "version": 1,
    "origin": "builtin",
}

_STRIP = re.compile(r"^\W*(?:jarvis[,\s]+)?(?:please\s+)?(?:type|write|enter|input)\s+"
                    r"(?:out\s+|in\s+|this\s*[:,]?\s*|the\s+following\s*[:,]?\s*)?", re.IGNORECASE)


def run(request, context):
    text = _STRIP.sub("", str(request)).strip().strip('"“”')
    if not text:
        return "What should I type, sir?"
    if context.get("dry_run"):
        return f"Would type {text[:60]!r}."
    return context["actions"].type_text(text)
