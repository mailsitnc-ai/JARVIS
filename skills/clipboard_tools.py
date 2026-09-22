"""Do things with whatever you copied - in any app.

  'summarise what I copied'   'explain the code I copied'   'what did I copy'
  'translate my clipboard to French'   'fix the grammar in what I copied'   'make the copied text formal'

Answers (summaries, explanations) are shown; transformations (translate, fix, rewrite, format...) are
also put back on the clipboard, ready to paste.
"""
import re

SKILL = {
    "name": "clipboard_tools",
    "description": "Work on what you copied: summarise, explain, translate, fix grammar, rewrite or format "
                   "the clipboard, e.g. 'summarise what I copied', 'translate my clipboard to Hindi', "
                   "'explain the code I copied'. Rewrites go back on the clipboard.",
    "triggers": [
        r"\bclipboard\b",
        r"\b(?:what|text|code|stuff|thing|this|that|paragraph|email|error)\s+(?:i(?:'ve|\s+have)?\s+)?"
        r"(?:just\s+)?copied\b",
        r"\bcopied\s+(?:text|code|paragraph|email|error|stuff)\b",
    ],
    "version": 1,
    "origin": "builtin",
}

_SHOW = re.compile(r"^\W*(?:what(?:'s| is| did i)\s+(?:on\s+)?(?:my\s+)?(?:clipboard|copy|copied)|show\s+"
                   r"(?:me\s+)?(?:my\s+)?clipboard)\W*$", re.IGNORECASE)
_TRANSFORM = re.compile(r"\b(?:translate|fix|correct|proofread|rewrite|rephrase|paraphrase|reword|format|"
                        r"convert|shorten|expand|simplify|improve|polish|make\s+(?:it|the)|turn\s+(?:it|the)|"
                        r"bullet|capitali[sz]e|lowercase|uppercase|clean\s+up|tidy)\b", re.IGNORECASE)
_SYSTEM_TRANSFORM = ("You transform text exactly as instructed. Output ONLY the transformed text - no "
                     "preamble, no quotes around it, no explanation.")
_SYSTEM_ANSWER = ("You are JARVIS. The user copied some text or code and asked about it. Answer clearly and "
                  "concisely (a short paragraph or a few bullet points). For code, explain what it does and "
                  "point out any bugs.")


def run(request, context):
    if context.get("dry_run"):
        return "Would read your clipboard and work on it."
    actions, ask = context["actions"], context.get("llm")
    copied = str(actions.read_clipboard() or "")
    if not copied.strip():
        return "Your clipboard is empty, sir - copy some text first."
    if _SHOW.match(request):
        return f"On your clipboard ({len(copied)} characters):\n{copied[:1500]}"
    if ask is None:
        return "I need a language model for that, sir."
    clip = copied[:12000]
    if _TRANSFORM.search(request):
        out = str(ask(f"Instruction: {request}\n\nText:\n{clip}", system=_SYSTEM_TRANSFORM,
                      temperature=0.2, max_tokens=3000)).strip()
        if not out or out.startswith("[LLM unavailable"):
            return "I couldn't reach a model to do that just now."
        note = actions.write_clipboard(out)
        return f"{out}\n\n({note.rstrip('.')} - paste it anywhere.)"
    out = str(ask(f"Request: {request}\n\nCopied text:\n{clip}", system=_SYSTEM_ANSWER,
                  temperature=0.3, max_tokens=1500)).strip()
    return out or "I couldn't work that out, sir."
