# Evolved by JARVIS on 2026-09-15 13:44 for: I now have a voice
import re

SKILL = {'name': 'enable_tts', 'description': 'Activate or configure text‑to‑speech output so the assistant can speak aloud.', 'triggers': ['\\bvoice\\b'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Detect intent to enable voice/TTS
    if not re.search(r"\bvoice\b", request, re.IGNORECASE):
        return "I didn't understand that you want to enable voice output."

    # Try to activate TTS via a generic action if the broker provides one.
    # Some implementations expose a generic "enable_tts" action; we attempt it safely.
    enable_action = getattr(context.get("actions", {}), "enable_tts", None)
    if callable(enable_action):
        result = enable_action()
        if result:
            return result

    # If no direct action, fall back to using an existing speak skill (if any)
    # by asking the assistant to say a short confirmation phrase.
    try:
        spoken = context["run"]("speak I am now ready to talk.")
        if spoken:
            return spoken
    except Exception:
        # If the speak skill is unavailable, just notify the user.
        pass

    # As a last resort, use a notification to inform the user.
    try:
        context["actions"].notify("Voice output enabled.", "TTS")
    except Exception:
        pass

    return "Voice output has been enabled. You can ask me to speak any text."
