# Evolved by JARVIS on 2026-09-15 13:33 for: create a folder on the desktop called WOW
import os
import re
import pathlib

SKILL = {'name': 'create_folder', 'description': 'Create a new folder at a specified location on the Windows filesystem.', 'triggers': ['\\bfolder\\b'], 'version': 1, 'origin': 'evolved'}


def _resolve_special_folder(name: str) -> pathlib.Path:
    """Map common folder names to user profile paths."""
    home = pathlib.Path.home()
    mapping = {
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "pictures": home / "Pictures",
        "music": home / "Music",
        "videos": home / "Videos",
    }
    return mapping.get(name.lower())


def run(request: str, context: dict) -> str:
    """
    Parse the request for a target location and folder name, create the folder,
    and return a short human‑readable confirmation.
    """
    # Try to extract a quoted folder name first
    quoted = re.findall(r"[\"']([^\"']+)[\"']", request)
    folder_name = quoted[-1] if quoted else ""

    # If no quoted name, look for the word after "called" or "named"
    if not folder_name:
        m = re.search(r"\b(?:called|named)\s+([^\s,.!?]+)", request, re.IGNORECASE)
        if m:
            folder_name = m.group(1)

    # Fallback: last word of the request
    if not folder_name:
        words = re.findall(r"\b\w+\b", request)
        folder_name = words[-1] if words else ""

    if not folder_name:
        return "I couldn't determine the folder name. Please specify it."

    # Determine the base location (desktop, documents, etc.) or use explicit path
    base_path = None
    # Look for known locations
    for key in ["desktop", "documents", "downloads", "pictures", "music", "videos"]:
        if re.search(rf"\b{key}\b", request, re.IGNORECASE):
            base_path = _resolve_special_folder(key)
            break

    # If no special location, try to find an explicit Windows path in the request
    if not base_path:
        path_match = re.search(r"([A-Za-z]:[\\/][\w\\/. ]+)", request)
        if path_match:
            base_path = pathlib.Path(path_match.group(1)).expanduser()

    # Default to Desktop if nothing else was identified
    if not base_path:
        base_path = _resolve_special_folder("desktop")

    target_path = base_path / folder_name

    # Create the folder
    os.makedirs(target_path, exist_ok=True)

    return f"Folder created at {target_path}"
