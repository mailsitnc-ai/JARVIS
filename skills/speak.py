"""Speak text aloud with the system text-to-speech voice (Windows SAPI / macOS `say`)."""
import re
import subprocess

from core.oslayer import speak_command

SKILL = {
    "name": "speak",
    "description": "Say something out loud (text-to-speech), e.g. 'say hello' or 'read this aloud: ...'.",
    "triggers": [
        r"\bsay\b",
        r"\bspeak\b",
        r"\bread\b[^.]*\b(?:out\s*loud|aloud)\b",
        r"\b(?:out\s*loud|aloud)\b",
        r"\btext\s*to\s*speech\b",
    ],
    "version": 1,
    "origin": "builtin",
}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _extract(request):
    # Prefer whatever comes after "aloud"/"out loud", then after say/speak/read.
    match = re.search(r"(?:out\s*loud|aloud)\s*[:,\-]?\s*(.+)$", request, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(?:say|speak|read)\b\s*(?:this|that|it|the\s+following)?\s*(?:text|message|out\s*loud|aloud)?"
                          r"\s*[:,\-]?\s*(.+)$", request, re.IGNORECASE)
    text = (match.group(1) if match else "").strip().strip('"“”\'')
    text = re.sub(r"^(?:me|this|that|it)\s+", "", text, flags=re.IGNORECASE).strip()
    return text


def run(request, context):
    text = _extract(request)
    if not text:
        return "What should I say?"
    if context.get("dry_run"):
        return f'Would say: "{text}"'
    try:
        result = subprocess.run(speak_command(text), creationflags=_NO_WINDOW, timeout=60,
                                stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"I couldn't speak: {exc}"
    if result.returncode != 0:
        return f"I couldn't speak: {(result.stderr or '').strip()[:150] or 'text-to-speech failed'}"
    return f'Said: "{text}"'
