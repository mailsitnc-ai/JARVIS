# Evolved by JARVIS on 2026-09-15 11:43 for: saying ma fave aussie ken barbie doll
import re

SKILL = {'name': 'speak_text', 'description': 'Speak a given phrase aloud using the system TTS engine.', 'triggers': ['\\bsaying\\b'], 'version': 1, 'origin': 'evolved'}

def _build_powershell_command(text: str) -> list:
    # Escape single quotes for PowerShell string literal
    escaped = text.replace("'", "''")
    ps_script = (
        f"Add-Type -AssemblyName System.Speech; "
        f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{escaped}')"
    )
    return ["powershell", "-Command", ps_script]

def run(request: str, context: dict) -> str:
    """
    Extract the phrase after keywords like 'say', 'speak', 'saying', or 'reading'
    and invoke the system TTS engine via a PowerShell command.
    """
    match = re.search(r"\b(?:say|speak|saying|reading)\b\s+(.+)", request, re.IGNORECASE)
    phrase = match.group(1).strip(" .!?") if match else ""

    if not phrase:
        return "I didn't catch what you want me to say. Please provide a phrase."

    # Execute the TTS command; the broker returns the command output (or None)
    result = context["actions"].run_command(_build_powershell_command(phrase))

    # Provide a human‑readable confirmation
    return f"Said: \"{phrase}\"."
