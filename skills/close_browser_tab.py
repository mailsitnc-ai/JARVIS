# Evolved by JARVIS on 2026-09-25 15:37 for: close the current browser tab
import re

SKILL = {'name': 'close_browser_tab', 'description': 'Close the currently active tab in the frontmost web browser (Safari or Chrome).', 'triggers': ['\\bclose\\b[^.]{0,20}\\bbrowser\\b'], 'version': 1, 'origin': 'evolved'}


def _apple_script():
    # AppleScript that closes the active tab of the frontmost Safari or Chrome window
    return """
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
        end tell
        if frontApp is "Safari" then
            tell application "Safari"
                if (count of windows) > 0 then
                    close current tab of front window
                end if
            end tell
        else if frontApp is "Google Chrome" then
            tell application "Google Chrome"
                if (count of windows) > 0 then
                    close active tab of front window
                end if
            end tell
        else
            error "No supported browser is frontmost."
        end if
    """


def run(request: str, context: dict) -> str:
    # Verify the request actually asks to close a browser tab
    if not re.search(r"\bclose\b.{0,20}\bbrowser\b", request, re.IGNORECASE):
        return "I can only close the active tab of a web browser."

    script = _apple_script()
    try:
        # Use the broker's safe command runner
        output = context["actions"].run_command(["osascript", "-e", script])
        # osascript returns nothing on success; we treat any output as a message
        if output:
            return f"Browser tab close result: {output.strip()}"
        return "Closed the active browser tab."
    except Exception as e:
        # Propagate unexpected errors so JARVIS can surface them
        raise
