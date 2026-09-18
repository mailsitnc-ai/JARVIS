# Evolved by JARVIS on 2026-09-15 13:31 for: delete C:\Users\Pradeep\Desktop\timer.py
import os
import re
from pathlib import Path

SKILL = {'name': 'delete_file', 'description': 'Delete a specified file from the filesystem.', 'triggers': [r"\b(?:delete|remove|trash|erase)\b[^.\n]{0,30}\b(?:file|folder|\.[A-Za-z0-9]{1,5}\b)"], 'version': 2, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # Try to extract a Windows‑style path after the word "delete"
    match = re.search(r"\bdelete\s+([A-Za-z]:[\\/][^\s]+)", request, re.IGNORECASE)
    if not match:
        # Fallback: take the last token that looks like a path
        tokens = request.split()
        possible = tokens[-1] if tokens else ""
        if re.match(r"[A-Za-z]:[\\/]", possible):
            path_str = possible
        else:
            return "I couldn't find a file path to delete. Please specify the full path."
    else:
        path_str = match.group(1)

    # Clean surrounding punctuation
    path_str = path_str.strip('\'"`.,;!')

    file_path = Path(path_str)

    if not file_path.exists():
        return f"The file '{file_path}' does not exist."
    if not file_path.is_file():
        return f"The path '{file_path}' is not a file."

    # Perform deletion
    file_path.unlink()

    return f"Deleted file '{file_path}'."
