# Evolved by JARVIS on 2026-09-17 19:48 for: timer.py to add input for timer duration instead of only a stopwatch
import os
import re

SKILL = {'name': 'modify_timer_script', 'description': 'Edit timer.py to ask for a duration and run a countdown instead of a plain stopwatch.', 'triggers': ['\\btimer\\.py\\b'], 'version': 1, 'origin': 'evolved'}


def _replace_stopwatch(content: str) -> str:
    """
    Very simple heuristic: look for a loop that runs forever printing elapsed time
    and replace it with a prompt‑and‑countdown implementation.
    If the pattern is not found, the original content is returned unchanged.
    """
    # pattern matching a typical stopwatch loop:
    # while True: ... time.time() - start ...
    stopwatch_pattern = re.compile(
        r"(while\s+True\s*:\s*[\s\S]*?time\.time\(\)\s*-\s*start\s*[\s\S]*?)(?=^def\s|\Z)",
        re.MULTILINE
    )

    replacement = (
        "duration_str = input('Enter timer duration in seconds: ')\n"
        "try:\n"
        "    duration = int(duration_str)\n"
        "except ValueError:\n"
        "    print('Invalid number, exiting.')\n"
        "    exit(1)\n"
        "\n"
        "import time\n"
        "for remaining in range(duration, 0, -1):\n"
        "    mins, secs = divmod(remaining, 60)\n"
        "    print(f'Time left: {mins:02d}:{secs:02d}', end='\\r')\n"
        "    time.sleep(1)\n"
        "print('\\nTimer finished!')\n"
    )

    new_content, count = stopwatch_pattern.subn(replacement, content, count=1)
    return new_content if count else content


def run(request: str, context: dict) -> str:
    # Determine the path to timer.py – assume it is in the current working directory
    script_path = os.path.abspath("timer.py")
    if not os.path.isfile(script_path):
        return "Could not find timer.py in the current directory."

    # Read the existing file
    original = context["actions"].read_file(script_path)

    # Modify the content
    modified = _replace_stopwatch(original)

    if modified == original:
        return "I couldn't locate a stopwatch loop to replace in timer.py."

    # Write the updated script back
    context["actions"].write_file(script_path, modified)

    return f"timer.py has been updated with a duration prompt and countdown timer."
