# Evolved by JARVIS on 2026-09-17 18:44 for: C:\Users\Pradeep\Desktop\calc2.py
import os
import re

SKILL = {'name': 'open_file_path', 'description': 'Open a file at a given filesystem path using its default associated application.', 'triggers': ['^[A-Za-z]:\\\\', '\\\\', '\\.py$'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Find a Windows‑style absolute path in the request
    path_match = re.search(r"[A-Za-z]:\\(?:[^\\\n]+\\)*[^\\\n]+", request)
    if not path_match:
        return "I couldn't find a file path in your request. Please provide a full Windows path."

    path = path_match.group(0).strip()
    # Expand environment variables and normalize
    path = os.path.expandvars(path)
    path = os.path.normpath(path)

    if not os.path.isfile(path):
        return f"The file \"{path}\" does not exist."

    # Use the broker to open the file with its default associated application
    result = context["actions"].open_app(path)

    # The broker may return a message or None; craft a friendly response
    if result:
        return result
    return f"Opened \"{path}\"."
