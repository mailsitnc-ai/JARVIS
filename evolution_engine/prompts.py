"""Prompt templates for the evolution engine. Placeholders are {word}; see fill().

Tuned for small local models: the rules live in the system message and every call carries a
worked example as an earlier turn. A 0.5B model imitates examples far more reliably than it
fills in templates, which it copies verbatim ("snake_case_skill_name").
"""
from __future__ import annotations

import re

ANALYST_SYSTEM = """You are the evolution engine of JARVIS, an assistant on the user's Windows laptop. A request arrived that no existing skill handles. Reply with one JSON object:
- kind: "skill" if it needs code, computation, parsing, live data, files, apps or the operating system; "answer" if it is only conversation or general knowledge.
- name: short snake_case name of the general capability, without the specific values in the request.
- description: one sentence about the general capability.
- triggers: 1 to 3 short case-insensitive regexes built from the action or topic words of the request, never from its specific values.
- test_inputs: 2 or 3 different example requests for the same capability.
- plan: one or two sentences on how to do it in Python."""

ANALYZE_PROMPT = """Request: {request}
Existing skills that did not match:
{skills}"""

# The skill example goes last: small models lean towards whatever they saw most recently.
ANALYSIS_EXAMPLES = [
    ("who wrote romeo and juliet", {
        "kind": "answer",
        "name": "general_knowledge",
        "description": "Answer a general knowledge question.",
        "triggers": [r"\bwho\s+wrote\b"],
        "test_inputs": ["who wrote hamlet", "who wrote the odyssey"],
        "plan": "Answer directly from knowledge.",
    }),
    ("reverse the letters in the word hello", {
        "kind": "skill",
        "name": "reverse_text",
        "description": "Reverse the letters of a word or phrase.",
        "triggers": [r"\breverse\b", r"\bbackwards?\b"],
        "test_inputs": ["reverse the word python", "write 'stressed' backwards"],
        "plan": "Take the quoted text, or else the last word of the request, and return it reversed.",
    }),
]

CODER_SYSTEM = (
    "You write small, robust, standard-library-only Python 3 modules that plug into JARVIS, "
    "a Windows 10 desktop assistant. Reply with exactly one ```python code block and nothing else."
)

ANSWER_SYSTEM = (
    "You are JARVIS, a concise and capable personal assistant running on the user's Windows laptop. "
    "Answer directly and briefly."
)

DRAFT_PROMPT = """Write a JARVIS skill module.

Capability: {description}
Implementation plan: {plan}
Original user request: {request}

Follow this contract exactly:
- Python 3 standard library only. No pip packages.
- A module-level dict, with exactly this name and these triggers:
  SKILL = {"name": "{name}", "description": "...", "triggers": {triggers}, "version": 1, "origin": "evolved"}
- def run(request: str, context: dict) -> str
  Extract what you need from the natural-language request yourself and return a short, human-readable answer string.
- To do anything to the computer, use the broker context["actions"] — NEVER import subprocess or webbrowser, and never call os.startfile:
    context["actions"].open_app(name)          open an app, folder or known place
    context["actions"].open_url(url)           open a website (a new browser tab)
    context["actions"].screenshot()            capture the screen; returns the saved path
    context["actions"].read_file(path)         return a file's text
    context["actions"].write_file(path, text)  create or overwrite a file
    context["actions"].list_dir(path)          list a folder
    context["actions"].run_command(["prog","arg"])  run a program; returns its output
    context["actions"].notify(message, title)       show a popup / message box on screen
    context["actions"].http_request(url)            fetch a web page or call an API; returns the response text (use this for live data like weather, prices, definitions - never import urllib/requests/socket yourself)
  The broker handles safety, permission and verification itself, so just return what it returns. Do NOT add your own dry_run checks around it. During verification http_request returns "{}", so guard against empty results.
- context["llm"](prompt) returns a string if you need language understanding; during verification it returns a placeholder, so the skill must still work without it.
- context["run"]("some request") hands a sub-task to another existing skill and returns its text - use it to reuse skills (e.g. return context["run"]("what time is it")) instead of reimplementing them. During verification it returns a placeholder.
- Prefer the standard library. If you genuinely need a third-party package, add its pip name(s) to a
  "requires" list in SKILL (e.g. SKILL = {..., "requires": ["beautifulsoup4"]}) and import it normally;
  JARVIS installs it before running. Still use context["actions"].http_request for network, not requests.
- Never delete files. Never use eval, exec, ctypes, or input().
- Handle bad or missing USER input gracefully (return a helpful message). But do NOT hide real bugs -
  let unexpected failures (bad imports, API errors, wrong attributes) raise instead of catching them and
  returning a generic "it failed" string, so JARVIS can detect the problem and fix the skill.
- Only imports, constants, functions and classes at top level; nothing may run on import.
- No example usage, tests or print() calls outside functions."""

# Two worked example turns precede every real draft: one pure-computation skill (value
# extraction) and one action skill (using the broker). A small model imitates these closely.
DRAFT_EXAMPLES = [
    {
        "description": "Reverse the letters of a word or phrase.",
        "plan": "Take the quoted text, or else the last word of the request, and return it reversed.",
        "request": "reverse the letters in the word hello",
        "name": "reverse_text",
        "triggers": [r"\breverse\b", r"\bbackwards?\b"],
        "code": r'''import re

SKILL = {"name": "reverse_text", "description": "Reverse the letters of a word or phrase.", "triggers": ["\\breverse\\b", "\\bbackwards?\\b"], "version": 1, "origin": "evolved"}


def run(request, context):
    quoted = re.findall(r"[\"'](.+?)[\"']", request)
    words = re.findall(r"[A-Za-z]+", request)
    target = quoted[-1] if quoted else (words[-1] if words else "")
    if not target:
        return "Tell me which word to reverse."
    return f"'{target}' reversed is '{target[::-1]}'."
''',
    },
    {
        "description": "Open a named application on Windows.",
        "plan": "Pull the app name from the request and open it through the broker.",
        "request": "launch notepad",
        "name": "launch_app",
        "triggers": [r"\blaunch\b"],
        "code": r'''import re

SKILL = {"name": "launch_app", "description": "Open a named application on Windows.", "triggers": ["\\blaunch\\b"], "version": 1, "origin": "evolved"}


def run(request, context):
    match = re.search(r"(?:launch|open|start)\s+(.+)", request, re.IGNORECASE)
    name = match.group(1).strip(" .!?") if match else ""
    if not name:
        return "Tell me which app to open."
    result = context["actions"].open_app(name)
    return result or f"I don't know how to open '{name}'."
''',
    },
]

# Kept as the canonical single example for tests and any caller that wants just one.
DRAFT_EXAMPLE = DRAFT_EXAMPLES[0]

RETRY_SUFFIX = """

Your previous version failed verification:
{feedback}

Previous version:
```python
{code}
```
Fix every problem and return the complete corrected module."""


def fill(template: str, **values) -> str:
    """Single-pass {word} substitution, so braces inside JSON examples and user text are left alone."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)
