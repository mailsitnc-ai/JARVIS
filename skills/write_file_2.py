# Evolved by JARVIS on 2026-09-15 13:26 for: calc.py
import re
import os

SKILL = {'name': 'write_file_2', 'description': 'Create and save a code or text file with specified content.', 'triggers': ['\\bcalc\\b'], 'version': 1, 'origin': 'evolved'}


def _extract_filename_and_content(request: str):
    """
    Looks for patterns like:
        "create file foo.py with content ...",
        "save bar.txt: ...",
        or just a bare filename like "calc.py".
    Returns (filename, content) where content may be an empty string.
    """
    # Try explicit "filename ... with content ..."
    m = re.search(r"(?i)(?:create|make|save|write)\s+file\s+([^\s:]+)\s*(?:with\s+content\s*[:\-]?)?\s*(.*)", request)
    if m:
        return m.group(1), m.group(2).strip()

    # Try "filename: content"
    m = re.search(r"(?i)([^\s:]+\.?\w*)\s*[:\-]\s*(.*)", request)
    if m:
        return m.group(1), m.group(2).strip()

    # Fallback: first token that looks like a filename (contains a dot)
    tokens = request.split()
    for token in tokens:
        if "." in token:
            return token, ""
    # If nothing looks like a filename, return empty strings
    return "", ""


def run(request, context):
    filename, content = _extract_filename_and_content(request)

    if not filename:
        return "I couldn't find a filename in your request. Please specify something like 'create file hello.txt with content ...'."

    # Ensure the filename is safe (no path traversal)
    filename = os.path.basename(filename)

    # Write the file using the broker action
    result = context["actions"].write_file(filename, content)

    # The broker may return a confirmation string or None
    if result:
        return result
    else:
        return f"Created file '{filename}' with {len(content)} characters."
