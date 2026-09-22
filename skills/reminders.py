"""Reminders and timers - spoken + notification when due, and they survive restarts.

  'remind me in 20 minutes to check the oven'    'remind me at 6pm to call Maa'
  'set a timer for 25 minutes'                   'remind me tomorrow at 9 to submit the essay'
  'what are my reminders'                        'cancel my timer' / 'cancel the reminder about Maa'
"""
import datetime as dt
import re

SKILL = {
    "name": "reminders",
    "description": "Set, list or cancel reminders and timers, e.g. 'remind me in 20 minutes to check the "
                   "oven', 'remind me at 6pm to call Maa', 'set a timer for 25 minutes', 'what are my "
                   "reminders', 'cancel my timer'. JARVIS says it aloud and notifies you when it's due.",
    "triggers": [
        r"\bremind\s+(?:me|us)\b",
        r"\b(?:set|start|add|create)\s+(?:a\s+|an\s+)?(?:\d+[\s-]*(?:min(?:ute)?s?|hours?|h|m|sec(?:ond)?s?)\s+)?"
        r"(?:timer|reminder|countdown|alarm)\b",
        r"^\W*timer\s+(?:for\s+)?\d",
        r"\b(?:my|the|all)\s+(?:reminders?|timers?)\b",
        r"\bdon'?t\s+let\s+me\s+forget\b",
    ],
    "version": 1,
    "origin": "builtin",
}


def run(request, context):
    from core.reminders import ReminderStore, parse_what, parse_when, say_when
    low = request.lower()
    store = ReminderStore()
    now = dt.datetime.now()

    if re.search(r"\b(?:cancel|delete|remove|clear|stop|dismiss)\b", low):
        if context.get("dry_run"):
            return "Would cancel reminders."
        m = re.search(r"\b(?:about|for|to)\s+(.+)$", request, re.IGNORECASE)
        match = m.group(1).strip(" .") if m else ("timer" if "timer" in low and "all" not in low else None)
        gone = store.cancel(match)
        if not gone:
            return "There was nothing to cancel, sir."
        return f"Cancelled {len(gone)}: " + "; ".join(i["text"] for i in gone) + "."

    if re.search(r"\b(?:what|which|list|show|any)\b.*\b(?:reminders?|timers?)\b", low) and not \
            re.search(r"\bremind\s+me\s+(?:to|about|in|at)\b", low):
        items = store.list()
        if not items:
            return "You have no reminders or timers set, sir."
        lines = [f"- {i['text']} — {say_when(dt.datetime.fromtimestamp(i['due']), now)}" for i in items]
        return f"You have {len(items)}:\n" + "\n".join(lines)

    due = parse_when(request, now)
    if due is None:
        return "When should I remind you, sir? e.g. 'in 20 minutes' or 'at 6pm'."
    is_timer = bool(re.search(r"\b(?:timer|countdown)\b", low)) and not re.search(r"\bremind\b", low)
    what = parse_what(request)
    what = re.sub(r"^(?:set|start|add|create)\s+(?:a\s+|an\s+)?(?:timer|countdown|alarm|reminder)\W*", "", what,
                  flags=re.IGNORECASE).strip()
    if is_timer:
        what = what or "timer"
    elif not what:
        return "What should I remind you about, sir?"
    if context.get("dry_run"):
        return f"Would set a {'timer' if is_timer else 'reminder'} {say_when(due, now)}."
    store.add(due, what, "timer" if is_timer else "reminder")
    if is_timer:
        return f"Timer set - I'll let you know {say_when(due, now)}."
    return f"Very good, sir. I'll remind you to {what} {say_when(due, now)}." if not \
        re.match(r"^(?:the|my|a|an)\b", what, re.I) else f"Very good, sir. I'll remind you about {what} " \
        f"{say_when(due, now)}."
