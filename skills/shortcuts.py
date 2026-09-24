"""Press keyboard shortcuts for you: tabs, apps, desktops, lock/sleep, copy/paste/undo.

  'next tab'  'previous tab'  'close tab'  'new tab'  'reopen that tab'  'go to tab 3'
  'switch app'  'hide this app'  'next desktop'  'lock the screen'  'put the mac to sleep'
  'select all'  'undo that'
"""
SKILL = {
    "name": "shortcuts",
    "description": "Press a keyboard shortcut: tabs (next/previous/close/new/reopen/'go to tab 3'), switch "
                   "app, next/previous desktop, lock the screen, sleep the Mac, select all, undo.",
    "triggers": [
        r"\b(?:next|previous|prev|last|new|close|cross|re-?open|restore)\s+(?:this\s+|that\s+|the\s+|my\s+|"
        r"current\s+|last\s+|closed\s+)*tabs?\b|\btabs?\s+(?:left|right|back|next|close)\b",
        r"\b(?:go|switch|jump)\s+to\s+tab\s*\d?\b|\btab\s+(?:number\s*)?(?:\d|one|two|three|four|five|six|"
        r"seven|eight|nine|last)\b",
        r"\b(?:switch|change|cycle)\s+(?:the\s+)?apps?\b|\bapp\s+switcher\b|\bcommand\s*tab\b",
        r"\b(?:next|previous|prev|last)\s+(?:desktop|space)\b|\b(?:desktop|space)\s+(?:left|right)\b",
        r"\block\s+(?:the\s+)?(?:screen|mac|computer|laptop)\b",
        r"\b(?:put\s+(?:the\s+)?(?:mac|computer|laptop)\s+to\s+sleep|sleep\s+the\s+(?:mac|computer|laptop))\b",
        r"\bhide\s+(?:this|the\s+current)\s+(?:app|window)\b",
        r"^\W*(?:select\s+all|undo\s+that|redo)\W*$",
    ],
    "version": 1,
    "origin": "builtin",
}


def run(request, context):
    from core.shortcuts import parse, press
    name, number = parse(request)
    if not name:
        return ("Which shortcut, sir? e.g. 'next tab', 'go to tab 3', 'close tab', 'switch app', "
                "'next desktop', 'lock the screen'.")
    if context.get("dry_run"):
        return f"Would press the {name.replace('_', ' ')} shortcut."
    actions = context.get("actions")
    if name in ("lock", "sleep") and actions is not None:
        # Disruptive: goes through the broker so it can be refused / confirmed like any other action.
        return actions.run_shortcut(name, number)
    return press(name, number)
