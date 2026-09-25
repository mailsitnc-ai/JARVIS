# Evolved by JARVIS on 2026-09-25 20:42 for: press page down
import re

SKILL = {'name': 'press_page_down', 'description': 'Simulate the Page Down keyboard shortcut to scroll down.', 'triggers': ['\\bpress\\b[^\\n]{0,20}\\bpage\\s+down\\b', '\\bpage\\s+down\\b'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Detect if the user really wants a page‑down action
    if not re.search(r"\bpage\s+down\b", request, re.IGNORECASE):
        return "I didn't understand that request. Did you want to press Page Down?"

    # AppleScript to send a Page Down keystroke (key code 121)
    script = 'tell application "System Events" to key code 121'
    try:
        output = context["actions"].run_command(["osascript", "-e", script])
        # run_command may return output or None; we treat any result as success
        return "Page Down key pressed."
    except Exception as e:
        # Let unexpected errors surface; only handle missing action gracefully
        return f"Failed to press Page Down: {e}"
