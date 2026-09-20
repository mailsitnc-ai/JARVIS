# Evolved by JARVIS on 2026-09-15 11:43 for: saying ma fave aussie ken barbie doll
import re

# Triggers must fire ONLY on a genuine "speak aloud" request. The original bare '\bsaying\b' was far
# too greedy - it hijacked messaging commands like "whatsapp the group saying X" (turning them into
# text-to-speech) - so it now needs the command to start with say/speak/saying, or an explicit
# aloud/out-loud/text-to-speech cue.
SKILL = {'name': 'speak_text',
         'description': 'Read a phrase aloud through the system TTS voice, e.g. "say hello out loud" '
                        'or "speak this: meeting at noon".',
         'triggers': [
             r"^\s*(?:say|speak|saying)\b",
             r"\b(?:say|read|speak)\b[^.\n]*\b(?:aloud|out\s+loud)\b",
             r"\bspeak\s+(?:this|that|it|the\s+following)\b",
             r"\btext[-\s]?to[-\s]?speech\b",
         ],
         'version': 2, 'origin': 'evolved'}

def _build_powershell_command(text: str) -> list:
    # Cross-platform: Windows SAPI via PowerShell, macOS `say`, Linux `spd-say`.
    from core.oslayer import speak_command
    return speak_command(text)

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
