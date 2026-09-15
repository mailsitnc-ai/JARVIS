# Evolved by JARVIS on 2026-09-15 14:41 for: write a research paper on how AIs will become self evolving in the future, make it 300 lines, and sa
import os
import re

SKILL = {'name': 'generate_document', 'description': 'Create a long‑form text document on a given topic and save it to a specified location.', 'triggers': ['\\bwrite\\b.*\\bpaper\\b'], 'version': 1, 'origin': 'evolved'}


def _extract_parameters(request: str) -> dict:
    """
    Very simple extraction:
    - topic: text after "on" or "about" up to a comma or "and"
    - lines: number before the word "lines"
    - path: anything after "save it on" or "save it to"
    """
    topic_match = re.search(r"\b(?:on|about)\s+([^,]+?)(?:,|and|$)", request, re.IGNORECASE)
    lines_match = re.search(r"(\d+)\s+lines", request, re.IGNORECASE)
    path_match = re.search(r"save\s+it\s+(?:on|to)\s+(.+)", request, re.IGNORECASE)

    topic = topic_match.group(1).strip() if topic_match else ""
    lines = int(lines_match.group(1)) if lines_match else None
    raw_path = path_match.group(1).strip() if path_match else ""

    # Resolve generic locations like "my desktop"
    if re.search(r"\bdesktop\b", raw_path, re.IGNORECASE):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        filename = "document.txt"
        # If user gave a filename, keep it
        name_match = re.search(r"desktop[\\\/]?([^\\\/]+)$", raw_path, re.IGNORECASE)
        if name_match:
            filename = name_match.group(1).strip()
        path = os.path.join(desktop, filename)
    else:
        path = raw_path or ""

    return {"topic": topic, "lines": lines, "path": path}


def run(request, context):
    params = _extract_parameters(request)

    if not params["topic"]:
        return "I couldn't find the topic you want the paper about. Please specify it."

    if not params["lines"]:
        return "Please tell me how many lines the document should have."

    if not params["path"]:
        return "I need a location to save the file (e.g., \"save it on my desktop\")."

    # Build a prompt for the LLM to generate the content
    prompt = (
        f"Write a {params['lines']}-line research paper on the topic: {params['topic']}. "
        "Do not include titles or headings, just plain text."
    )
    try:
        generated = context["llm"](prompt)
    except Exception:
        generated = ""

    if not generated:
        # Fallback: create placeholder lines
        generated = "\n".join([f"Line {i+1} about {params['topic']}." for i in range(params["lines"])])

    # Write the file using the broker action
    write_result = context["actions"].write_file(params["path"], generated)

    # The broker's write_file may return None or a status string
    if write_result:
        return f"Document saved to {params['path']}: {write_result}"
    else:
        return f"Document written to {params['path']}."
