# Evolved by JARVIS on 2026-09-17 19:50 for: C:\Users\Pradeep\Desktop\world_time.py to keep the application window open (e.g., add a main loop or
import re
import os

SKILL = {'name': 'modify_file_add_wait', 'description': 'Add a simple wait or main‑loop to a script so its window stays open.', 'triggers': ['\\bkeep\\b.*\\bopen\\b', '\\bmain\\s*loop\\b', '\\bwait\\s*for\\s*user\\s*input\\b'], 'version': 1, 'origin': 'evolved'}


_WAIT_SNIPPET = (
    "\n\nif __name__ == \"__main__\":\n"
    "    input(\"Press Enter to exit...\")\n"
)


def _extract_path(request: str) -> str:
    """
    Find a Windows‑style path in the request.
    """
    # Look for something that looks like a full path, optionally quoted
    m = re.search(r'(?:"|\')?([A-Za-z]:[\\/][^\s"\']+)(?:"|\')?', request)
    return m.group(1) if m else ""


def run(request, context):
    path = _extract_path(request)
    if not path:
        return "I couldn't find a file path in your request. Please specify the script file to modify."

    # Normalize path separators
    path = os.path.normpath(path)

    # Read the file
    original = context["actions"].read_file(path)
    if original is None:
        return f"Failed to read the file at '{path}'."

    # If the wait snippet is already present, do nothing
    if _WAIT_SNIPPET.strip() in original:
        return f"The file '{os.path.basename(path)}' already contains a wait for user input."

    # Append the snippet
    modified = original.rstrip() + _WAIT_SNIPPET

    # Write back
    context["actions"].write_file(path, modified)

    return f"Added a simple wait to '{os.path.basename(path)}'. The script will now pause for user input before closing."
