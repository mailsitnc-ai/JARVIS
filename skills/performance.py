"""Performance doctor: what's slowing the computer down, and quitting the culprit.

  'why is my mac slow'   'what's using my cpu'   'what's eating my memory'   'what's draining my battery'
  'quit Chrome' / 'close Spotify' (asks the app to quit normally - it can still offer to save)
"""
import re

SKILL = {
    "name": "performance",
    "description": "Find what's slowing the computer (top apps by CPU and memory, memory pressure) and quit "
                   "an app, e.g. 'why is my mac slow', 'what's using my cpu', 'what's eating my memory', "
                   "'quit Chrome'.",
    "triggers": [
        r"\bwhy\s+is\s+(?:my\s+|the\s+|this\s+)?(?:mac|macbook|computer|laptop|pc|system)\s+(?:so\s+|being\s+|running\s+)?"
        r"(?:slow|laggy|lagging|hot|loud|sluggish|stuck)",
        r"\bwhat(?:'s|\s+is|s)\s+(?:using|eating|hogging|draining|slowing)\s+(?:up\s+|down\s+)?(?:all\s+)?(?:my\s+|the\s+)?"
        r"(?:cpu|memory|ram|battery|mac|computer|laptop|processor)",
        r"\b(?:top|heaviest|biggest)\s+(?:apps|processes|programs)\b|\bperformance\s+(?:check|report|doctor)\b",
        r"\bspeed\s+up\s+(?:my\s+)?(?:mac|computer|laptop|pc)\b",
        r"^\W*(?:please\s+)?(?:quit|force[\s-]?quit|kill)\s+(?!(?:hand|voice|listening|watching)\b)"
        r"(?:the\s+)?(?:app\s+)?[\w .&+-]{2,40}\W*$",
    ],
    "version": 1,
    "origin": "builtin",
}

_QUIT = re.compile(r"^\W*(?:please\s+)?(?:quit|force[\s-]?quit|kill)\s+(?:the\s+)?(?:app\s+)?(.+?)\W*$", re.I)
_SELF = {"python", "python3", "python3.12", "jarvis"}
_ALIASES = {"chrome": "Google Chrome", "google chrome": "Google Chrome", "vscode": "Code",
            "vs code": "Code", "visual studio code": "Code", "teams": "Microsoft Teams", "word": "Microsoft Word",
            "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint", "whatsapp": "WhatsApp"}


def _label(name):
    return "JARVIS (me)" if name.lower() in _SELF else name


def run(request, context):
    from core import oslayer
    actions = context.get("actions")
    m = _QUIT.match(request)
    if m:
        name = m.group(1).strip()
        name = _ALIASES.get(name.lower(), name[:1].upper() + name[1:])
        if name.lower() in _SELF:
            return "I'd rather not quit myself, sir - say 'stop' or use the menu if you want me gone."
        if context.get("dry_run"):
            return f"Would quit {name}."
        return actions.quit_app(name)

    if context.get("dry_run"):
        return "Would check what's using the CPU and memory."
    raw = oslayer.top_apps(40)
    if not raw:
        return "I couldn't read the process list just now, sir."
    merged = {}
    for a in raw:                       # JARVIS runs as a few Python processes - show it once
        key = _label(a["name"])
        m = merged.setdefault(key, {"name": a["name"], "cpu": 0.0, "mem_mb": 0.0, "procs": 0})
        m["cpu"] += a["cpu"]
        m["mem_mb"] += a["mem_mb"]
        m["procs"] += a["procs"]
    apps = sorted(merged.values(), key=lambda a: (a["cpu"], a["mem_mb"]), reverse=True)[:12]
    cores = __import__("os").cpu_count() or 1
    total_cpu = sum(a["cpu"] for a in apps)
    by_cpu = [a for a in apps if a["cpu"] >= 1][:5]
    by_mem = sorted(apps, key=lambda a: a["mem_mb"], reverse=True)[:5]
    lines = []
    mem = oslayer.memory_gb()
    if mem:
        total, avail, load = mem
        lines.append(f"Memory: {avail:.1f} GB free of {total:.0f} GB ({load}% in use).")
    lines.append(f"CPU: about {min(100, round(total_cpu / cores))}% of {cores} cores busy.")
    if by_cpu:
        lines.append("Busiest right now:")
        lines += [f"  - {_label(a['name'])}: {a['cpu']:.0f}% CPU, {a['mem_mb']:.0f} MB" for a in by_cpu]
    lines.append("Most memory:")
    lines += [f"  - {_label(a['name'])}: {a['mem_mb'] / 1024:.1f} GB" if a["mem_mb"] >= 1024 else
              f"  - {_label(a['name'])}: {a['mem_mb']:.0f} MB" for a in by_mem]
    culprits = [a for a in apps if a["name"].lower() not in _SELF and
                (a["cpu"] >= 60 or a["mem_mb"] >= 1500) and not a["name"].startswith(("kernel", "Window"))]
    if culprits:
        c = culprits[0]
        why = f"{c['cpu']:.0f}% CPU" if c["cpu"] >= 60 else f"{c['mem_mb'] / 1024:.1f} GB of memory"
        lines.append(f"The main culprit looks like {c['name']} ({why}). Say 'quit {c['name']}' if you don't "
                     "need it right now.")
    elif mem and mem[2] >= 85:
        lines.append("Memory's nearly full - closing a few browser tabs or unused apps would help.")
    else:
        lines.append("Nothing's misbehaving, sir - everything looks healthy.")
    return "\n".join(lines)
