"""Open or run the file JARVIS most recently created ("open it", "run it", "open what you just made")."""
from core.actions import ActionBroker

SKILL = {
    "name": "open_last",
    "description": "Open or run the file JARVIS just created, e.g. 'open it', 'run it', 'open the file you made'.",
    "triggers": [
        r"^\s*(?:please\s+)?(?:open|run|launch|start|execute)\s+(?:it|that|this|them)\b",
        r"^\s*(?:please\s+)?(?:open|run|launch|start|execute)\s+(?:up\s+)?the\s+(?:file|app|program|script|game|it)\b",
        r"\b(?:open|run|launch|start|execute)\b[^.]*\byou\s+(?:just\s+)?(?:made|created|wrote|saved|built)\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def run(request, context):
    actions = _actions(context)
    path = actions.last_written()
    if not path:
        return "I haven't created a file yet - tell me what to open, e.g. 'open notepad'."
    if context.get("dry_run"):
        return f"Would open {path}."
    return actions.open_path(str(path))
