# Evolved by JARVIS on 2026-09-24 22:00 for: sissyy saying HI THIS IS JARVIS TESTINg TESTING 123
import re

SKILL = {'name': 'speak_text_2', 'description': 'Speak a given phrase aloud using the system text‑to‑speech engine.', 'triggers': ['\\btesting\\b'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Find the phrase after a speaking command keyword
    pattern = r"\b(?:say|speak|read\s+out\s+loud)\b\s*[:\-]?\s*(.+)"
    match = re.search(pattern, request, re.IGNORECASE)
    phrase = match.group(1).strip() if match else ""

    if not phrase:
        # Fallback: use everything after the first word if no keyword matched
        parts = request.split(maxsplit=1)
        phrase = parts[1] if len(parts) > 1 else ""

    if not phrase:
        return "I couldn't find any text to speak. Please tell me what to say."

    # Use the broker to run the macOS `say` command
    output = context["actions"].run_command(["say", phrase])

    # The run_command may return output or None; provide a friendly response
    return output if output else f"Saying: {phrase}"
