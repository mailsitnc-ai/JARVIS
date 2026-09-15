# Evolved by JARVIS on 2026-09-15 14:27 for: C:\Users\Pradeep\Pictures\JARVIS-20260915-142616.png
import re
import os

SKILL = {'name': 'open_file', 'description': 'Open a specified file (image, document, etc.) using the default associated application.', 'triggers': ['\\bpictures\\b'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Try to find a Windows‑style absolute path in the request
    path_match = re.search(r"[A-Za-z]:[\\/][\w\\/. -]+", request)
    file_path = path_match.group(0) if path_match else request.strip()

    if not file_path:
        return "I couldn't find a file path to open. Please tell me the full path."

    # Normalize the path (but do not verify existence – let the broker handle errors)
    file_path = os.path.normpath(file_path)

    # Use the broker's action to open the file with its default program
    result = context["actions"].open_app(file_path)

    # If the broker returns a message, forward it; otherwise give a friendly confirmation
    return result or f"Opened '{file_path}'."
