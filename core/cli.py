"""Command line interface. Run `jarvis --help`."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, keystore
from .config import ROOT, load_settings, parse_value, set_user_value, unset_user_value, user_config_path

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _print_event(_stage: str, message: str) -> None:
    print(f"  .. {message}", flush=True)


def _print_reply(reply) -> None:
    print(reply.text)
    meta = [reply.skill and f"skill: {reply.skill}", f"route: {reply.route}",
            reply.provider and f"via {reply.provider}", f"{reply.elapsed_s:.1f}s"]
    print("  [" + " | ".join(m for m in meta if m) + "]")


def _pythonw() -> str:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def _terminal_confirm(req) -> str:
    """Ask permission for one action at the terminal."""
    print(f"\n  JARVIS wants to: {req.summary}", flush=True)
    if req.details:
        print(f"    {req.details}")
    try:
        answer = input("  Allow?  [y] once  [a] always  [N] no: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "deny"
    return {"y": "once", "yes": "once", "o": "once", "a": "always", "always": "always"}.get(answer, "deny")


# ---- assistant --------------------------------------------------------------------------------

def cmd_ask(args) -> int:
    from .orchestrator import Jarvis

    jarvis = Jarvis(on_event=_print_event, confirm=_terminal_confirm)
    _print_reply(jarvis.handle(" ".join(args.text), force_evolve=args.force))
    return 0


def cmd_do(args) -> int:
    """Hand a request to the JARVIS that's already running, so it lands in that session - the one
    with the browser open, the voice listening and whatever it's watching."""
    from window_manager import ipc

    request = " ".join(args.text).strip()
    reply = ipc.send(f"run {request}")
    if reply is None:
        print("JARVIS isn't running - start it (open JARVIS.app) or use `jarvis ask` for a one-off.")
        return 1
    if not reply.get("ok"):
        print(f"JARVIS: {reply.get('error', 'command failed')}")
        return 1
    print(f"Sent to JARVIS: {request}\n(the answer appears in the JARVIS panel)")
    return 0


def cmd_chat(_args) -> int:
    from .orchestrator import Jarvis

    jarvis = Jarvis(on_event=_print_event, confirm=_terminal_confirm)
    print("JARVIS chat. Type 'exit' to leave.")
    while True:
        try:
            text = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in {"exit", "quit"}:
            return 0
        if text:
            _print_reply(jarvis.handle(text))


def cmd_skills(args) -> int:
    from .skill_loader import SkillRegistry

    registry = SkillRegistry()
    registry.reload()
    for skill in sorted(registry.skills.values(), key=lambda s: (s.origin != "builtin", s.name)):
        print(f"{skill.name:<26} {skill.origin:<8} {skill.description}")
        if args.verbose:
            for trigger in skill.trigger_sources:
                print(f"{'':<36}/{trigger}/")
    for filename, error in registry.errors.items():
        print(f"[!!] {filename}: {error}")
    return 0


# ---- window / daemon ---------------------------------------------------------------------------

def _spawn_daemon(show: bool) -> None:
    from .oslayer import IS_WINDOWS
    argv = [_pythonw(), str(ROOT / "jarvis.py"), "daemon"] + (["--show"] if show else [])
    kwargs = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if IS_WINDOWS:
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # detach from the terminal so the daemon outlives it
    subprocess.Popen(argv, **kwargs)


def _control(command: str, start_if_missing: bool) -> int:
    from window_manager import ipc

    reply = ipc.send(command)
    if reply is None:
        if not start_if_missing:
            print("JARVIS is not running.")
            return 0
        _spawn_daemon(show=True)
        print("Starting JARVIS (the first launch takes a few seconds)...")
        return 0
    if not reply.get("ok"):
        print(f"JARVIS: {reply.get('error', 'command failed')}")
        return 1
    return 0


def cmd_toggle(_args) -> int:
    return _control("toggle", start_if_missing=True)


def cmd_on(_args) -> int:
    return _control("show", start_if_missing=True)


def cmd_off(_args) -> int:
    return _control("hide", start_if_missing=False)


def cmd_stop(_args) -> int:
    return _control("quit", start_if_missing=False)


def cmd_interrupt(_args) -> int:
    return _control("interrupt", start_if_missing=False)


def cmd_daemon(args) -> int:
    from window_manager import ipc

    if ipc.send("ping", timeout=1.0) is not None:
        if args.show:
            ipc.send("show")
        print("JARVIS is already running.")
        return 0
    from ui.panel import run_daemon

    return run_daemon(show=args.show)


def _startup_link() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "JARVIS.lnk"


KEEPER_LABEL = "com.jarvis.keeper"


def cmd_serve(_args) -> int:
    """JARVIS with no screen - what runs on the always-on machine."""
    from .serve import run

    return run()


def _launch_agent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{KEEPER_LABEL}.plist"


def _keeper_log() -> Path:
    return Path.home() / "Library" / "Logs" / "JARVIS-keeper.log"


def cmd_keeper(args) -> int:
    """The watchdog launchd runs: start JARVIS whenever it isn't answering, forever."""
    from .keeper import GAP, answering, guard, launch

    if args.once:
        print("JARVIS is running." if answering() else f"Not running - {launch()}")
        return 0
    gap = args.gap or GAP
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} keeper watching JARVIS (every {gap:.0f}s)",
          flush=True)
    guard(gap=gap, log=lambda line: print(line, flush=True))
    return 0


def cmd_startup(args) -> int:
    from .oslayer import IS_MAC, IS_WINDOWS
    if IS_WINDOWS:
        link = _startup_link()
        if args.action == "disable":
            link.unlink(missing_ok=True)
            print("JARVIS will no longer start at sign-in.")
            return 0
        if args.action == "status":
            print(f"Start at sign-in: {'enabled' if link.exists() else 'disabled'}  ({link})")
            return 0
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:J_LINK);"
                  "$s.TargetPath=$env:J_TARGET;$s.Arguments=$env:J_ARGS;$s.WorkingDirectory=$env:J_DIR;"
                  "$s.Description='JARVIS assistant';$s.Save()")
        env = dict(os.environ, J_LINK=str(link), J_TARGET=_pythonw(), J_ARGS=f'"{ROOT / "jarvis.py"}" daemon', J_DIR=str(ROOT))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True)
        print(f"JARVIS will start in the background at sign-in ({link}).")
        return 0
    if IS_MAC:
        plist = _launch_agent_plist()
        old = Path.home() / "Library" / "LaunchAgents" / "com.jarvis.daemon.plist"
        if args.action == "disable":
            for gone in (plist, old):
                subprocess.run(["launchctl", "unload", str(gone)], capture_output=True)
                gone.unlink(missing_ok=True)
            print("JARVIS will no longer start on its own. Your phone will only reach it while you\n"
                  "have started it yourself.")
            return 0
        if args.action == "status":
            from .keeper import answering
            loaded = subprocess.run(["launchctl", "list", KEEPER_LABEL], capture_output=True).returncode == 0
            print(f"Start on its own: {'enabled' if plist.exists() else 'disabled'}  ({plist})")
            print(f"Keeper loaded now: {'yes' if loaded else 'no'}")
            print(f"JARVIS answering:  {'yes' if answering() else 'no'}")
            print(f"Keeper log:        {_keeper_log()}")
            return 0
        # The agent runs the KEEPER, not the daemon: a loop that starts JARVIS whenever it isn't
        # answering. launchd alone would only start it at login - the keeper also covers a crash, a
        # quit, and waking up hours later, and it launches JARVIS.app when that exists so macOS
        # keeps the camera/microphone grants attached to JARVIS.
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f'  <key>Label</key><string>{KEEPER_LABEL}</string>\n'
            '  <key>ProgramArguments</key><array>'
            f'<string>{_pythonw()}</string><string>{ROOT / "jarvis.py"}</string><string>keeper</string></array>\n'
            f'  <key>WorkingDirectory</key><string>{ROOT}</string>\n'
            '  <key>RunAtLoad</key><true/>\n'
            '  <key>KeepAlive</key><true/>\n'
            '  <key>ThrottleInterval</key><integer>30</integer>\n'
            f'  <key>StandardOutPath</key><string>{_keeper_log()}</string>\n'
            f'  <key>StandardErrorPath</key><string>{_keeper_log()}</string>\n'
            '  <key>EnvironmentVariables</key><dict>'
            '<key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>'
            '</dict>\n'
            '</dict></plist>\n', encoding="utf-8")
        for gone in (old,):        # the previous arrangement started the daemon directly
            subprocess.run(["launchctl", "unload", str(gone)], capture_output=True)
            gone.unlink(missing_ok=True)
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist)], capture_output=True)
        print(f"Done. JARVIS now starts itself at login and comes back within a minute if it ever\n"
              f"stops - so your phone can reach it without you touching this Mac.\n"
              f"  agent: {plist}\n  log:   {_keeper_log()}")
        return 0
    print("Start-at-login isn't wired up for this platform yet; launch 'jarvis on' manually.")
    return 0


# ---- permissions ---------------------------------------------------------------------------------

def cmd_permissions(args) -> int:
    from .permissions import CAPABILITIES, PermissionRegistry

    registry = PermissionRegistry()
    if args.reset:
        registry.reset()
        registry.set_autonomy(False)
        print("All permissions reset to 'ask' (autonomy off).")
        return 0
    if registry.autonomy():
        print("[autonomy ON] Every capability is currently allowed. Turn it off: jarvis autonomy off\n")
    if args.capability and args.state:
        registry.set(args.capability, args.state)
        print(f"{args.capability} -> {args.state}")
        return 0
    if args.capability:
        print(f"{args.capability}: {registry.state(args.capability)}")
        return 0
    labels = {"ask": "asks each time", "allow": "always allowed", "deny": "always blocked"}
    print("What JARVIS may do on this PC (change with: jarvis permissions <capability> allow|deny|ask):\n")
    for capability, description in CAPABILITIES.items():
        state = registry.state(capability)
        print(f"  {capability:<12} {labels[state]:<16} {description}")
    return 0


def cmd_autonomy(args) -> int:
    """The 'unleash it' switch: while on, JARVIS acts without asking and may use risky calls."""
    from .permissions import PermissionRegistry

    registry = PermissionRegistry()
    if args.mode in ("on", "off"):
        registry.set_autonomy(args.mode == "on")
    if registry.autonomy():
        print("Autonomy is ON - JARVIS acts on every capability without asking, skips plan review, and lets\n"
              "evolved skills use risky calls (delete files, run shell, eval). Turn it off: jarvis autonomy off")
    else:
        print("Autonomy is OFF - JARVIS asks before each new capability and shows plans for approval.\n"
              "Unleash it: jarvis autonomy on")
    return 0


def cmd_usage(args) -> int:
    """Show how much each model (groq / gemini / ollama) has been used."""
    from .usage import UsageStore

    store = UsageStore()
    if args.reset:
        store.reset()
        print("Model usage counters reset.")
        return 0
    data = store.all()
    if not data:
        print("No model usage recorded yet. Ask JARVIS something that uses a cloud model.")
        return 0
    print("Model usage (calls and tokens per model):\n")
    print(f"  {'model':<9} {'calls':>7} {'in tokens':>11} {'out tokens':>11} {'total':>11}   last used")
    tc = ti = to = 0
    for name in sorted(data, key=lambda n: -int(data[n].get("calls", 0))):
        row = data[name]
        ca, pin, pout = int(row.get("calls", 0)), int(row.get("prompt_tokens", 0)), int(row.get("completion_tokens", 0))
        tc += ca; ti += pin; to += pout
        print(f"  {name:<9} {ca:>7} {pin:>11,} {pout:>11,} {pin + pout:>11,}   {row.get('last_used', '-')}")
    print(f"  {'TOTAL':<9} {tc:>7} {ti:>11,} {to:>11,} {ti + to:>11,}")
    return 0


def cmd_model(args) -> int:
    """Show or manually switch which model JARVIS uses (auto walks the fallback chain)."""
    from .config import load_settings, set_user_value
    from window_manager import ipc

    valid = ["auto", "groq", "gemini", "ollama"]
    if not args.name:
        s = load_settings()
        pinned = str(s.get("llm.provider", "auto") or "auto")
        order = s.get("llm.fallback_order") or []
        order = order if isinstance(order, list) else str(order).split(",")
        print(f"Current model: {pinned}" + ("" if pinned != "auto" else f"  (auto -> {' > '.join(order)})"))
        print(f"Switch with:  jarvis model <{'|'.join(valid)}>")
        return 0
    name = args.name.strip().lower()
    if name not in valid:
        print(f"Unknown model '{name}'. Choose one of: {', '.join(valid)}")
        return 1
    set_user_value("llm.provider", name)
    live = ipc.send("reloadconfig", timeout=2)
    where = "applied live" if (live and live.get("ok")) else "saved (applies when JARVIS is running/restarts)"
    print(f"Model set to '{name}' - {where}.")
    return 0


def _parse_duration(text: str) -> int | None:
    import re as _re
    m = _re.fullmatch(r"(\d+)\s*([smhd])", str(text).strip().lower())
    if not m:
        return None
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def cmd_agenda(args) -> int:
    """JARVIS's own to-do list - scheduled tasks it runs on its own initiative (while autonomy is on)."""
    from .agenda import AgendaStore, describe_trigger

    store = AgendaStore()
    action = (args.action or "list").lower()

    if action in ("on", "off"):
        store.set_enabled(action == "on")
        print(f"Agenda scheduler {'enabled' if action == 'on' else 'disabled'}.")
        return 0
    if action == "reflect":
        mode = (args.rest[0].lower() if args.rest else "on")
        existing = [t for t in store.list() if t["kind"] == "reflection"]
        if mode == "off":
            for t in existing:
                store.remove(t["id"])
            print("Self-reflection task removed.")
        else:
            if existing:
                store.set_task_enabled(existing[0]["id"], True)
            else:
                store.add("Self-reflection", "__reflect__", {"type": "interval", "seconds": 6 * 3600}, kind="reflection")
            print("Self-reflection scheduled every 6h (runs only while autonomy is on).")
        return 0
    if action == "add":
        prompt = " ".join(args.rest).strip()
        if not prompt:
            print('What should it do? e.g. jarvis agenda add "organize my downloads" --every 1h')
            return 1
        if args.once:
            at = args.once.strip()
            if len(at) == 16:      # 'YYYY-MM-DD HH:MM' -> add seconds
                at += ":00"
            trigger = {"type": "once", "at": at}
        elif args.daily:
            trigger = {"type": "daily", "time": args.daily}
        else:
            secs = _parse_duration(args.every or "1h")
            if secs is None:
                print("Use --every like 30m/2h/1d, or --daily 08:00, or --once 'YYYY-MM-DD HH:MM'.")
                return 1
            trigger = {"type": "interval", "seconds": secs}
        task = store.add(args.title or prompt[:40], prompt, trigger)
        print(f"Added task {task['id']}: {task['title']} ({describe_trigger(trigger)}). Runs while autonomy is on.")
        return 0
    if action == "remove":
        if not args.rest:
            print("Usage: jarvis agenda remove <id>")
            return 1
        print("Removed." if store.remove(args.rest[0]) else "No task with that id.")
        return 0
    if action == "run":
        if not args.rest:
            print("Usage: jarvis agenda run <id>")
            return 1
        from .orchestrator import Jarvis
        task = store.get(args.rest[0])
        if task is None:
            print("No task with that id.")
            return 1
        jarvis = Jarvis(on_event=_print_event, confirm=_terminal_confirm)
        print(jarvis.run_agenda_task(task))
        store.mark_ran(task["id"], "ran manually")
        return 0

    # default: list
    tasks = store.list()
    print(f"Agenda scheduler: {'ON' if store.enabled() else 'OFF'}   (tasks run only while autonomy is on)\n")
    if not tasks:
        print('  (empty)   add one:  jarvis agenda add "summarize my day" --daily 18:00')
        return 0
    for t in tasks:
        flag = " " if t.get("enabled") else "×"
        print(f"  [{flag}] {t['id']}  {t['title'][:34]:<34} {describe_trigger(t['trigger']):<16} "
              f"next {t.get('next_run', '-')}  runs {t.get('runs', 0)}")
    return 0


def cmd_improve(args) -> int:
    """Ask JARVIS to rewrite one of its own evolved skills to be better."""
    from .orchestrator import Jarvis

    jarvis = Jarvis(on_event=_print_event, confirm=_terminal_confirm)
    skill = jarvis.registry.get(args.skill)
    if skill is None:
        print(f"No skill named '{args.skill}'. See installed skills with: jarvis skills")
        return 1
    if skill.origin != "evolved":
        print(f"'{args.skill}' is a built-in skill; I only improve skills I evolved myself.")
        return 1
    reason = " ".join(args.reason) if args.reason else "Make it more robust, clearer and more capable."
    outcome = jarvis.evolution.improve(skill, reason=reason)
    if outcome.kind == "skill":
        print(f"Improved '{args.skill}' and verified the new version.")
        return 0
    print(f"Couldn't improve '{args.skill}': {outcome.detail or 'no better version verified'}")
    return 1


def cmd_edit_self(args) -> int:
    """Have JARVIS rewrite one of its own source files (syntax-checked, tested, rolled back on failure)."""
    from .orchestrator import Jarvis

    jarvis = Jarvis(on_event=_print_event, confirm=_terminal_confirm)
    instruction = " ".join(args.instruction)
    result = jarvis._self_editor.edit(instruction) if jarvis._gate_self_edit() else None
    if result is None:
        print("Permission for self_edit was declined. Allow it with: jarvis permissions self_edit allow")
        return 1
    print(result.message)
    return 0 if result.ok else 1


# ---- configuration -------------------------------------------------------------------------------

def _fingerprint(value: str, keep: int = 7) -> str:
    """Show enough of a secret to spot a bad paste, without printing the whole thing."""
    n = len(value)
    if n <= keep + 4:
        return f"{n} chars (too short to mask safely)"
    return f"{n} chars, starts '{value[:keep]}', ends '{value[-3:]}'"


def cmd_google(args) -> int:
    from .google import GoogleAuth

    auth = GoogleAuth()
    if args.action == "status":
        print(f"Google: {auth.status()}")
        return 0
    if args.action == "logout":
        auth.logout()
        print("Disconnected from Google.")
        return 0
    if args.action == "setup":
        print("Create a free OAuth client so JARVIS can read your Drive and Gmail:")
        print("  1. https://console.cloud.google.com/  ->  create a project")
        print("  2. APIs & Services > Enabled APIs: enable 'Google Drive API' and 'Gmail API'")
        print("  3. OAuth consent screen: External; add your own email as a Test user")
        print("  4. Credentials > Create credentials > OAuth client ID > application type 'Desktop app'")
        print("  5. Paste the Client ID and Client secret below.\n")
        client_id = input("Client ID: ").strip()
        if getattr(args, "show", False):
            client_secret = input("Client secret (visible): ").strip()
        else:
            client_secret = getpass.getpass("Client secret (hidden): ").strip()
        if not client_id or not client_secret:
            print("Nothing saved.")
            return 1
        auth.set_credentials(client_id, client_secret)
        print("Saved (encrypted). Now run:  jarvis google login")
        print(f"  Client ID  : {_fingerprint(client_id, keep=16)}")
        print(f"  Secret     : {_fingerprint(client_secret)}")
        if not client_secret.startswith("GOCSPX-"):
            print("  ! Warning: a Google client secret normally starts with 'GOCSPX-'. If yours doesn't,")
            print("    you copied the wrong field. It's the 'Client secret' shown next to the client in")
            print("    APIs & Services > Credentials (not the Client ID, not a downloaded JSON filename).")
        elif len(client_secret) < 30:
            print("  ! Warning: that secret looks short - it may have been truncated on paste. Re-run and")
            print("    use the copy icon next to the secret, or run:  jarvis google setup --show")
        return 0
    print("Opening your browser to sign in to Google...")
    print(auth.login())
    return 0


def _learning_store():
    from memory.learning import LearningStore

    from .config import MEMORY_DIR

    return LearningStore(MEMORY_DIR)


def cmd_teach(args) -> int:
    from .skill_loader import SkillRegistry

    skill = args.skill.strip()
    phrase = " ".join(args.phrase).strip()
    registry = SkillRegistry()
    registry.reload()
    if registry.get(skill) is None:
        known = ", ".join(sorted(registry.skills)) or "(none)"
        print(f"No skill called '{skill}'. Known skills: {known}")
        return 1
    _learning_store().teach(phrase, skill)
    print(f"Learned: messages like '{phrase}' will use the '{skill}' skill.")
    return 0


def cmd_lessons(args) -> int:
    store = _learning_store()
    if args.clear:
        for path in (store.lessons_path, store.hints_path):
            path.unlink(missing_ok=True)
        print("Cleared all lessons and learned routes.")
        return 0
    hints = store.hints()
    print(f"Learned routes ({len(hints)}):")
    for hint in hints[-20:]:
        print(f"  '{hint.get('phrase', '')}' -> {hint.get('skill')}  ({hint.get('source')})")
    print(f"\nRecent lessons ({store.lesson_count()} total):")
    for text in store.recent_lessons(limit=20):
        print(f"  - {text}")
    return 0


def _key_problem(key: str) -> str | None:
    """Why a pasted key looks invalid, or None if it's plausible. Catches the Ctrl+V (^V / \\x16) mistake."""
    if not key:
        return "nothing was entered"
    if any(ord(c) < 0x20 for c in key):
        return "it contains control characters - Ctrl+V was typed instead of pasting"
    if len(key) < 12 or " " in key:
        return "it's too short or has spaces"
    return None


def cmd_setkey(args) -> int:
    if not args.provider:
        # Bare `jarvis setkey`: ask for every cloud key in turn (Enter on an empty prompt skips one).
        worst = 0
        for name in keystore.ENV_VARS:
            print(f"\n== {name} ==  (press Enter without pasting to skip)")
            worst = max(worst, cmd_setkey(argparse.Namespace(provider=name, clear=False, show=args.show,
                                                             skippable=True)))
        return worst
    provider = args.provider.lower()
    if provider not in keystore.ENV_VARS:
        print(f"Unknown provider '{provider}'. Keys are only needed for: {', '.join(keystore.ENV_VARS)}")
        return 1
    if args.clear:
        print("Key removed." if keystore.clear_key(provider) else "No stored key to remove.")
        return 0
    hints = {"groq": "console.groq.com", "gemini": "aistudio.google.com/apikey"}
    if provider in hints:
        print(f"Get a {provider} key at {hints[provider]}")
    from .oslayer import IS_MAC
    print("Tip: paste with Cmd+V - the key stays hidden while you paste, that's normal." if IS_MAC else
          "Tip: paste with RIGHT-CLICK or Ctrl+Shift+V - Ctrl+V does not paste in a terminal.")
    if args.show:
        key = input(f"Paste your {provider} API key: ").strip()
    else:
        key = getpass.getpass(f"Paste your {provider} API key (hidden; press Enter after pasting): ").strip()

    if not key and getattr(args, "skippable", False):
        print(f"Skipped {provider} (kept whatever was stored).")
        return 0
    problem = _key_problem(key)
    if problem:
        print(f"That didn't look like a valid key ({problem}).")
        print("Paste it again. If it keeps failing, run:  jarvis setkey "
              f"{provider} --show   to paste it visibly, or set the {keystore.ENV_VARS[provider]} "
              "environment variable instead.")
        return 1
    keystore.store_key(provider, key)
    settings = load_settings()
    from .oslayer import IS_WINDOWS
    how = "for this Windows account, encrypted with DPAPI" if IS_WINDOWS \
        else "in a user-only file (obfuscated, not encrypted - prefer the env var for anything sensitive)"
    print(f"Stored the {provider} key {how}.")
    print(f"Provider selection is unchanged: provider={settings.get('llm.provider')}, "
          f"order={' > '.join(settings.get('llm.fallback_order') or [])}")
    return 0


def cmd_config(args) -> int:
    if args.set:
        key, sep, raw = args.set.partition("=")
        if not sep or not key.strip():
            print("Use: jarvis config --set section.key=value")
            return 1
        set_user_value(key.strip(), parse_value(raw))
        print(f"Set {key.strip()} in {user_config_path()}")
    if args.unset:
        print(f"Removed {args.unset}." if unset_user_value(args.unset) else f"{args.unset} was not set.")
    if not args.set and not args.unset:
        print(json.dumps(load_settings().data, indent=2))
        print(f"\nuser config: {user_config_path()}")
    return 0


def _user_env(name: str) -> str | None:
    """A user-level environment variable. On Windows read it from the registry (what a newly started
    Ollama sees); elsewhere the process environment is authoritative."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except (OSError, ImportError):
        return os.environ.get(name)


def cmd_doctor(args) -> int:
    from memory.store import MemoryStore
    from window_manager import ipc
    from window_manager.hotkey import parse_hotkey

    from .config import MEMORY_DIR
    from .llm_router import LLMError, LLMRouter
    from .skill_loader import SkillRegistry
    from .winsys import memory_status

    settings = load_settings()
    mark = lambda ok: "[ok]" if ok else "[--]"

    print(f"JARVIS {__version__} doctor")
    print(f"  python     {platform.python_version()}  {sys.executable}")
    print(f"  root       {ROOT}")
    config_path = user_config_path()
    print(f"  config     {config_path}{'' if config_path.exists() else '  (not created; defaults in use)'}")
    ram = memory_status()
    if ram:
        print(f"  RAM        {ram.total_gb:.1f} GB total, {ram.available_gb:.1f} GB available ({ram.load_percent}% in use)")

    router = LLMRouter(settings)
    print("\nLLM")
    print(f"  provider        {settings.get('llm.provider')}  (from {settings.origin('llm.provider')})")
    print(f"  fallback order  {' > '.join(router.order())}  (from {settings.origin('llm.fallback_order')})")
    status = {name: (ok, reason, model) for name, ok, reason, model in router.status()}
    for name, (ok, reason, model) in status.items():
        print(f"  {mark(ok)} {name:<7} {model:<34} {reason}")
    next_provider = next((n for n in router.order() if status.get(n, (False,))[0]), None)
    pinned = str(settings.get("llm.provider", "auto") or "auto")
    print(f"  next call goes to: {next_provider or 'nothing - no provider is available'}"
          f"   (model: {pinned}; change with: jarvis model <name>)")
    from .usage import UsageStore
    used = UsageStore().all()
    if used:
        parts = [f"{n} x{int(r.get('calls', 0))}" for n, r in sorted(used.items(), key=lambda kv: -int(kv[1].get('calls', 0)))]
        print(f"  usage so far: {', '.join(parts)}   (full table: jarvis usage)")
    if "ollama" in router.order():
        tuning = ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_MAX_LOADED_MODELS", "OLLAMA_NUM_PARALLEL")
        missing = [name for name in tuning if not _user_env(name)]
        print(f"  {mark(not missing)} Ollama memory tuning: {'set' if not missing else 'missing ' + ', '.join(missing)}")
        if ram and ram.available_gb < 1.2:
            print(f"  [!!] only {ram.available_gb:.1f} GB RAM free right now; close Chrome before JARVIS builds a skill")
    if args.live and next_provider:
        router.on_progress = lambda message: print(f"  .. {message}", flush=True)
        started = time.monotonic()
        try:
            text = router.complete("Reply with exactly: OK", temperature=0.0, max_tokens=300)
            print(f"  live test: {router.last_provider} replied {text.strip()[:30]!r} in {time.monotonic() - started:.1f}s")
        except LLMError as exc:
            print(f"  live test failed: {exc}")

    registry = SkillRegistry()
    registry.reload()
    evolved = sum(1 for s in registry.skills.values() if s.origin == "evolved")
    print("\nSkills")
    print(f"  {len(registry.skills)} loaded ({len(registry.skills) - evolved} built-in, {evolved} evolved)")
    for filename, error in registry.errors.items():
        print(f"  [!!] {filename}: {error}")

    print("\nEvolution")
    print(f"  enabled={settings.get('evolution.enabled')}  attempts={settings.get('evolution.max_attempts')}  "
          f"sandbox={settings.get('evolution.sandbox_timeout_s')}s/{settings.get('evolution.sandbox_memory_mb')}MB  "
          f"risky calls allowed={settings.get('evolution.allow_risky_calls')}")
    memory = MemoryStore(MEMORY_DIR)
    print(f"  memory: {len(memory.interactions())} interactions, {len(memory.evolution_events())} evolution events")

    from memory.learning import LearningStore

    learning = LearningStore(MEMORY_DIR)
    print(f"  learning: {len(learning.hints())} learned routes, {learning.lesson_count()} lessons")

    from .google import GoogleAuth

    print(f"  google: {GoogleAuth().status()}")

    from .permissions import CAPABILITIES, PermissionRegistry

    perms = PermissionRegistry()
    if perms.autonomy():
        from .permissions import SENSITIVE
        print("\nPermissions  [AUTONOMY ON - acts without asking; turn off: jarvis autonomy off]")
        for capability in CAPABILITIES:
            note = f"{perms.state(capability)} (still gated - privacy)" if capability in SENSITIVE else "allow (autonomy)"
            print(f"  {capability:<12} {note}")
    else:
        print("\nPermissions (jarvis permissions <capability> allow|deny|ask;  unleash: jarvis autonomy on)")
        for capability in CAPABILITIES:
            print(f"  {capability:<12} {perms.state(capability)}")

    print("\nWindow")
    hotkey = str(settings.get("window.hotkey"))
    try:
        parse_hotkey(hotkey)
        hotkey_ok = True
    except ValueError as exc:
        hotkey_ok = False
        hotkey += f"  (invalid: {exc})"
    print(f"  {mark(hotkey_ok)} hotkey {hotkey}   split {settings.get('window.split')}/{1 - float(settings.get('window.split')):.1f}, "
          f"JARVIS on the {settings.get('window.jarvis_side')}")
    interrupt = str(settings.get("window.interrupt_hotkey", "ctrl+alt+c"))
    try:
        parse_hotkey(interrupt)
        interrupt_ok = True
    except ValueError as exc:
        interrupt_ok = False
        interrupt += f"  (invalid: {exc})"
    print(f"  {mark(interrupt_ok)} interrupt hotkey {interrupt}   (stops the current task)")
    ping = ipc.send("ping", timeout=1.0)
    if ping:
        hotkey_state = ping.get("hotkey_error") or "hotkey registered"
        print(f"  [ok] daemon running (pid {ping.get('pid')}, {'visible' if ping.get('visible') else 'hidden'}, {hotkey_state})")
    else:
        print("  [--] daemon not running (start it with: jarvis on)")
    return 0


# ---- parser -------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS 1.0: a self-evolving assistant for Windows. "
                                                                "With no command, toggles the 60/40 split view.")
    parser.add_argument("--version", action="version", version=f"JARVIS {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    for name, func, text in [
        ("toggle", cmd_toggle, "show or hide JARVIS in the 60/40 split (starts it if needed)"),
        ("on", cmd_on, "show JARVIS in the 60/40 split"),
        ("off", cmd_off, "hide JARVIS and restore your window"),
        ("stop", cmd_stop, "quit the background JARVIS process"),
        ("interrupt", cmd_interrupt, "stop whatever JARVIS is currently doing"),
        ("chat", cmd_chat, "chat with JARVIS in this terminal"),
    ]:
        sub.add_parser(name, help=text).set_defaults(func=func)

    ask = sub.add_parser("ask", help="one request, answered in the terminal")
    ask.add_argument("text", nargs="+")
    ask.set_defaults(func=cmd_ask, force=False)

    do = sub.add_parser("do", help="give the RUNNING JARVIS a request (answered in its panel)")
    do.add_argument("text", nargs="+")
    do.set_defaults(func=cmd_do)

    evolve = sub.add_parser("evolve", help="build a new skill for this request even if one seems to match")
    evolve.add_argument("text", nargs="+")
    evolve.set_defaults(func=cmd_ask, force=True)

    daemon = sub.add_parser("daemon", help="run the hotkey listener and panel (normally started for you)")
    daemon.add_argument("--show", action="store_true")
    daemon.set_defaults(func=cmd_daemon)

    skills = sub.add_parser("skills", help="list installed skills")
    skills.add_argument("-v", "--verbose", action="store_true", help="show triggers")
    skills.set_defaults(func=cmd_skills)

    doctor = sub.add_parser("doctor", help="check providers, skills, hotkey and daemon")
    doctor.add_argument("--live", action="store_true", help="also send a tiny test prompt to the LLM")
    doctor.set_defaults(func=cmd_doctor)

    setkey = sub.add_parser("setkey", help="store an API key (encrypted, never changes provider selection)")
    setkey.add_argument("provider", nargs="?", help="groq or gemini; leave out to be asked for each")
    setkey.add_argument("--clear", action="store_true")
    setkey.add_argument("--show", action="store_true", help="paste the key visibly (if hidden paste won't work)")
    setkey.set_defaults(func=cmd_setkey)

    config = sub.add_parser("config", help="show settings, or --set/--unset a user setting")
    config.add_argument("--set", metavar="KEY=VALUE")
    config.add_argument("--unset", metavar="KEY")
    config.set_defaults(func=cmd_config)

    startup = sub.add_parser("startup", help="start JARVIS by itself at sign-in, and keep it up")
    startup.add_argument("action", choices=["enable", "disable", "status"])
    startup.set_defaults(func=cmd_startup)

    serve = sub.add_parser("serve", help="run JARVIS with no screen (for an always-on machine)")
    serve.set_defaults(func=cmd_serve)

    keeper = sub.add_parser("keeper", help="watchdog: start JARVIS whenever it isn't running")
    keeper.add_argument("--gap", type=float, default=0.0, help="seconds between checks")
    keeper.add_argument("--once", action="store_true", help="check once and exit")
    keeper.set_defaults(func=cmd_keeper)

    from .permissions import CAPABILITIES, STATES

    permissions = sub.add_parser("permissions", help="show or change what JARVIS may do on this PC")
    permissions.add_argument("capability", nargs="?", choices=list(CAPABILITIES))
    permissions.add_argument("state", nargs="?", choices=list(STATES))
    permissions.add_argument("--reset", action="store_true", help="set every capability back to 'ask'")
    permissions.set_defaults(func=cmd_permissions)

    autonomy = sub.add_parser("autonomy", help="unleash JARVIS: act without asking (on|off|status)")
    autonomy.add_argument("mode", nargs="?", default="status", choices=["on", "off", "status"])
    autonomy.set_defaults(func=cmd_autonomy)

    usage = sub.add_parser("usage", help="show how much each model has been used")
    usage.add_argument("--reset", action="store_true", help="clear the usage counters")
    usage.set_defaults(func=cmd_usage)

    model = sub.add_parser("model", help="show or switch which model JARVIS uses")
    model.add_argument("name", nargs="?", help="auto | groq | gemini | ollama")
    model.set_defaults(func=cmd_model)

    agenda = sub.add_parser("agenda", help="JARVIS's scheduled tasks it runs on its own (list/add/remove/run/on/off/reflect)")
    agenda.add_argument("action", nargs="?", default="list",
                        help="list | add | remove | run | on | off | reflect")
    agenda.add_argument("rest", nargs="*", help="the task text (for add), or an id (remove/run)")
    agenda.add_argument("--every", help="interval like 30m, 2h, 1d")
    agenda.add_argument("--daily", help="time of day like 08:00")
    agenda.add_argument("--once", help="a single time: 'YYYY-MM-DD HH:MM'")
    agenda.add_argument("--title", help="a short name for the task")
    agenda.set_defaults(func=cmd_agenda)

    improve = sub.add_parser("improve", help="have JARVIS rewrite one of its own evolved skills")
    improve.add_argument("skill")
    improve.add_argument("reason", nargs="*", help="what to make better (optional)")
    improve.set_defaults(func=cmd_improve)

    edit_self = sub.add_parser("edit-self", help="have JARVIS rewrite its own source code (tested, auto-rollback)")
    edit_self.add_argument("instruction", nargs="+", help="what to change, e.g. 'add a /uptime command to core/cli.py'")
    edit_self.set_defaults(func=cmd_edit_self)

    teach = sub.add_parser("teach", help="teach JARVIS that a phrasing should use a given skill")
    teach.add_argument("skill")
    teach.add_argument("phrase", nargs="+")
    teach.set_defaults(func=cmd_teach)

    lessons = sub.add_parser("lessons", help="show what JARVIS has learned (routes and lessons)")
    lessons.add_argument("--clear", action="store_true", help="forget all learned routes and lessons")
    lessons.set_defaults(func=cmd_lessons)

    google = sub.add_parser("google", help="connect Google Drive + Gmail (setup, login, status, logout)")
    google.add_argument("action", nargs="?", default="login", choices=["setup", "login", "status", "logout"])
    google.add_argument("--show", action="store_true",
                        help="with setup: type the client secret visibly so you can verify the paste")
    google.set_defaults(func=cmd_google)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    func = getattr(args, "func", cmd_toggle)
    return int(func(args) or 0)
