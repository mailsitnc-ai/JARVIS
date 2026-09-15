"""Deterministic helpers for understanding a message before acting on it.

The orchestrator uses these to tell three things apart without a model call:
  - a correction ("no i meant open calculator") -> re-run the corrected wording
  - small talk (thanks, bye, "who are you", "what can you do") -> a canned reply
Anything left that isn't an actionable command is treated as chat (a model reply with context);
anything actionable with no matching skill goes to the evolution engine. Keeping this layer
deterministic is what makes conversation fast and reliable on a small local model.
"""
from __future__ import annotations

import re

# "no i meant open notepad", "actually, open chrome", "i said take a screenshot"
_CORRECTION = re.compile(
    r"^\s*(?:no+[,.!\s]+)?(?:i\s+meant|i\s+mean|i\s+said|i\s+wanted|i\s+want|actually|rather|"
    r"no\s+i\s+(?:meant|wanted|want)|not\s+that)\b[,:\s]*",
    re.IGNORECASE)
_BARE_NO = re.compile(r"^\s*no+[,.!\s]+(?=\w)", re.IGNORECASE)

_THANKS = re.compile(r"^\s*(?:thanks?|thank\s+you|thx|ty|cheers|appreciate\s+it)\b", re.IGNORECASE)
_BYE = re.compile(r"^\s*(?:bye|goodbye|see\s+you|see\s+ya|cya|good\s?night)\b", re.IGNORECASE)
_CANCEL = re.compile(r"^\s*(?:never\s*mind|nvm|forget\s+it|cancel\s+that|scratch\s+that)\b", re.IGNORECASE)
_ACK = re.compile(r"^\s*(?:ok(?:ay)?|k|cool|nice|awesome|perfect|got\s+it|sounds\s+good|alright|"
                  r"yep|yeah|yup)[.!\s]*$", re.IGNORECASE)
_IDENTITY = re.compile(r"\b(?:who\s+are\s+you|what\s+are\s+you|your\s+name|are\s+you\s+(?:an?\s+)?(?:ai|bot|jarvis))\b",
                       re.IGNORECASE)
_CAPABILITIES = re.compile(r"\b(?:what\s+can\s+you\s+do|what\s+do\s+you\s+do|what\s+can\s+you\s+help|"
                           r"how\s+can\s+you\s+help|your\s+(?:skills|capabilities|features)|"
                           r"list\s+(?:your\s+)?skills|what\s+are\s+you\s+capable)\b", re.IGNORECASE)


_GREETING = re.compile(r"^\s*(?:hi|hii+|hey+|hello+|yo|hiya|howdy|good\s+(?:morning|evening|afternoon|night)|"
                       r"how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|sup)\b", re.IGNORECASE)
_QUESTION = re.compile(r"^\s*(?:who|what|whats|what's|why|how|when|where|which|whose|is|are|am|was|were|do|does|"
                       r"did|can|could|should|would|will|has|have|had|tell\s+me\s+about|explain)\b", re.IGNORECASE)
# Politeness wrappers hide the real command: "can you open notepad" -> "open notepad".
_POLITE_STEPS = (
    re.compile(r"^\s*(?:hey|ok|okay)?\s*jarvis[\s,:!]+", re.IGNORECASE),
    re.compile(r"^\s*i\s+(?:want|need|'d\s+like|would\s+like)\s+you\s+to\s+", re.IGNORECASE),
    re.compile(r"^\s*(?:can|could|would|will)\s+you\s+(?:please\s+)?", re.IGNORECASE),
    re.compile(r"^\s*(?:please|pls|kindly)\s+", re.IGNORECASE),
)


def strip_correction(text: str) -> tuple[str, bool]:
    """Remove a leading correction ("no i meant ...") and return (remainder, was_a_correction)."""
    match = _CORRECTION.match(text) or _BARE_NO.match(text)
    if not match:
        return text, False
    remainder = text[match.end():].strip()
    return (remainder, True) if len(remainder) >= 2 else (text, False)


def strip_politeness(text: str) -> str:
    """Peel off leading politeness/address so the underlying command can be routed."""
    previous = None
    while previous != text:
        previous = text
        for pattern in _POLITE_STEPS:
            text = pattern.sub("", text, count=1)
    return text.strip()


def normalize_command(text: str) -> tuple[str, bool]:
    """The command hidden under a correction and/or politeness. Returns (command, was_a_correction)."""
    cleaned, corrected = strip_correction(text)
    cleaned = strip_politeness(cleaned)
    return (cleaned or text).strip(), corrected


def is_clearly_chat(text: str) -> bool:
    """Reads as a greeting or a question, so a spoken/text answer is right (not a task)."""
    return bool(_GREETING.search(text) or _QUESTION.match(text) or text.strip().endswith("?"))


def smalltalk_reply(text: str, registry=None) -> str | None:
    """A canned reply for obvious small talk, or None to let the normal path handle it."""
    if _CANCEL.match(text):
        return "Okay, never mind."
    if _THANKS.match(text):
        return "Anytime."
    if _BYE.match(text):
        return "See you."
    if _IDENTITY.search(text):
        return ("I'm JARVIS, your local assistant. I run on this PC, do tasks through skills, "
                "and build new skills when you ask for something I can't do yet.")
    if _CAPABILITIES.search(text):
        skills = sorted(registry.skills.values(), key=lambda s: s.name) if registry else []
        if skills:
            return "Here's what I can do now, and I'll build more when you ask:\n" + \
                   "\n".join(f"  - {s.description}" for s in skills)
        return "Tell me a task and I'll do it, or build a skill for it if I can't yet."
    if _ACK.match(text):
        return "Anything else?"
    return None
