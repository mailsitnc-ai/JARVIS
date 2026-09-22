"""The JARVIS workspace: a Tk panel that docks into the 40% side of the split, plus the
background daemon that owns the global hotkey and the terminal control channel.

Threads: Tk runs on the main thread. The hotkey loop, the control server and each request
run on their own threads and talk to Tk only through self.events.
"""
from __future__ import annotations

import ast
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback

from core.config import ARCHIVE_DIR, SKILLS_DIR, load_settings, user_dir
from ui.reactor import ArcReactor
from window_manager import macwm, win32
from window_manager.hotkey import HotkeyListener
from window_manager.ipc import ControlServer
from window_manager.split import SplitController

log = logging.getLogger("jarvis.panel")

# Arc-reactor palette: deep space background, cyan/blue glow.
C = {
    "bg": "#05070d", "panel": "#080d16", "fg": "#cfe8ff", "dim": "#5b7290", "accent": "#38bdf8",
    "user": "#7fdbff", "error": "#ff6b6b", "input": "#0a1524", "border": "#123",
}

HELP = """Commands
  /skills       list skills           /reload   hot-reload the skills folder
  /permissions  what JARVIS may do    /teach <skill> <phrase>   bind a phrasing to a skill
  /autonomy on|off  unleash / re-gate /lessons  what JARVIS learned
  /model [name]  show/switch the model  /usage    model usage per model
  /agenda       scheduled tasks JARVIS runs on its own (manage from: jarvis agenda)
  /voice on|off  talk to JARVIS ("Jarvis, ...") - on-device speech, spoken replies
  /clear        clear this panel (or Ctrl+Shift+3)    /hide  hide JARVIS (or Esc)
Anything else is a request. If no skill can handle it, JARVIS builds one.
By default actions on your PC ask for approval. Turn on autonomy (🔥 header button) to let it act freely.
Say "improve your <name> skill" and JARVIS rewrites that skill to be better."""


def _skill_meta_from_file(path) -> dict:
    """Read a skill file's SKILL dict without executing it (works for disabled files too)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SKILL" for t in node.targets):
            try:
                meta = ast.literal_eval(node.value)
                return meta if isinstance(meta, dict) else {}
            except (ValueError, SyntaxError):
                return {}
    return {}


class JarvisPanel:
    def __init__(self, settings):
        self.settings = settings
        self.hotkey = str(settings.get("window.hotkey", "ctrl+alt+j"))
        self.interrupt_hotkey = str(settings.get("window.interrupt_hotkey", "ctrl+alt+c"))
        self.events: queue.Queue = queue.Queue()
        self.visible = False
        self.busy = False
        self.jarvis = None
        self.history: list[str] = []
        self.history_pos = 0
        ratio = float(settings.get("window.split", 0.6))
        side = str(settings.get("window.jarvis_side", "right"))
        self.split = SplitController(ratio, side)
        self.is_mac = sys.platform == "darwin"
        # macOS docks via the Accessibility API instead of Win32; None -> we still size our own 40%
        # column with Tk (below), we just can't move the other app's window.
        self.mac_split = macwm.MacSplit(ratio, side) if macwm.available() else None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("JARVIS")
        self.root.configure(bg=C["bg"])
        self.root.minsize(320, 320)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._build()
        self._write("JARVIS 1.0 online. Type /help for commands.\n", "event")
        threading.Thread(target=self._boot, name="jarvis-boot", daemon=True).start()
        self.root.after(40, self._pump)

    # ---- layout -------------------------------------------------------------------------------

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=C["bg"])
        header.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(header, text="J A R V I S", fg=C["accent"], bg=C["bg"],
                 font=("Segoe UI Semibold", 13)).pack(side="left")
        tk.Button(header, text="⚙ skills", command=self._open_skills_manager, bg=C["bg"], fg=C["accent"],
                  activebackground=C["panel"], activeforeground=C["accent"], relief="flat", cursor="hand2",
                  font=("Segoe UI", 8)).pack(side="right", padx=(6, 0))
        self._autonomy_btn = tk.Button(header, text="", command=self._toggle_autonomy, bg=C["bg"],
                                       activebackground=C["panel"], relief="flat", cursor="hand2",
                                       font=("Segoe UI", 8))
        self._autonomy_btn.pack(side="right", padx=(6, 0))
        self._model_btn = tk.Button(header, text="", command=self._cycle_model, bg=C["bg"], fg=C["dim"],
                                    activebackground=C["panel"], relief="flat", cursor="hand2",
                                    font=("Segoe UI", 8))
        self._model_btn.pack(side="right", padx=(6, 0))
        self.status = tk.Label(header, text="booting", fg=C["dim"], bg=C["bg"], font=("Consolas", 8))
        self.status.pack(side="right")
        self._mgr = None
        self._refresh_autonomy_btn()
        self._refresh_model_btn()

        self._revert_after = None
        # The chat box lives at the bottom; everything above it is the reactor. Pack the bottom
        # pieces first (footer, input, transcript) so the reactor can expand to fill all the rest.
        footer = tk.Label(self.root,
                          text=f"Enter send · Esc hide · {self.hotkey} toggle · {self.interrupt_hotkey} stop · ctrl+shift+3 clear",
                          fg=C["dim"], bg=C["bg"], font=("Segoe UI", 8))
        footer.pack(side="bottom", anchor="w", padx=14, pady=(0, 8))

        self.bar = tk.Frame(self.root, bg=C["bg"])
        self.bar.pack(side="bottom", fill="x", padx=14, pady=(6, 4))

        body = tk.Frame(self.root, bg=C["panel"])
        body.pack(side="bottom", fill="x", padx=14, pady=(0, 2))
        self.log = tk.Text(body, wrap="word", bg=C["panel"], fg=C["fg"], relief="flat", borderwidth=0,
                           padx=12, pady=8, font=("Consolas", 10), state="disabled", cursor="arrow", height=8)
        scrollbar = tk.Scrollbar(body, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("label", foreground=C["accent"], font=("Consolas", 10, "bold"))
        self.log.tag_configure("user", foreground=C["user"], spacing1=10)
        self.log.tag_configure("jarvis", foreground=C["fg"], spacing3=2)
        self.log.tag_configure("event", foreground=C["dim"], font=("Consolas", 9))
        self.log.tag_configure("error", foreground=C["error"])

        self.entry = tk.Entry(self.bar, bg=C["input"], fg=C["fg"], insertbackground=C["accent"], relief="flat",
                              font=("Segoe UI", 11), highlightthickness=1, highlightbackground=C["border"],
                              highlightcolor=C["accent"])
        self.entry.pack(fill="x", ipady=8)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Up>", lambda _e: self._recall(-1))
        self.entry.bind("<Down>", lambda _e: self._recall(1))
        self.root.bind("<Escape>", lambda _e: self.hide())
        for _seq in ("<Control-Shift-Key-3>", "<Control-numbersign>", "<Control-Key-3>"):  # hard refresh
            self.root.bind_all(_seq, self._hard_refresh)  # clear the transcript (Ctrl+Shift+3)

        # The reactor fills everything between the header and the chat box - JARVIS's animated face.
        self.reactor = ArcReactor(self.root)
        self.reactor.pack(side="top", fill="both", expand=True, padx=6, pady=(2, 4))

    def _write(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def _set_reactor(self, state: str, revert_ms: int = 0) -> None:
        """Set the reactor's state; optionally return to idle/busy after a moment (for error/speaking)."""
        self.reactor.set_state(state)
        if self._revert_after is not None:
            try:
                self.root.after_cancel(self._revert_after)
            except (ValueError, tk.TclError):
                pass
            self._revert_after = None
        if revert_ms:
            self._revert_after = self.root.after(revert_ms, lambda: self.reactor.set_state("busy" if self.busy else "idle"))

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _hard_refresh(self, _event=None) -> str:
        """Wipe the on-screen transcript AND the short conversation memory - a clean slate (Ctrl+Shift+R)."""
        self._clear_log()
        if self.jarvis is not None:
            try:
                self.jarvis.history.clear()  # forget the recent back-and-forth so nothing carries over
            except Exception:
                pass
        self._write("JARVIS 1.0 online. Type /help for commands.\n", "event")
        if self.reactor.state_name != "offline":
            self._set_reactor("idle")
        return "break"

    def _refresh_autonomy_btn(self) -> None:
        """Reflect autonomy state in the header toggle (green flame when unleashed)."""
        on = False
        try:
            on = bool(self.jarvis and self.jarvis.permissions.autonomy())
        except Exception:
            pass
        if on:
            self._autonomy_btn.config(text="🔥 unleashed", fg="#ff9a3c")
        else:
            self._autonomy_btn.config(text="🔒 gated", fg=C["dim"])

    def _refresh_model_btn(self) -> None:
        """Show the current model in the header (pinned name, or 'auto→provider')."""
        label = "model"
        try:
            if self.jarvis is not None:
                ms = self.jarvis.model_status()
                pinned = ms["pinned"]
                label = f"◆ {pinned}" if pinned != "auto" else f"◆ auto·{ms.get('next') or '?'}"
        except Exception:
            pass
        self._model_btn.config(text=label)

    def _cycle_model(self) -> None:
        """Click the header model button to rotate auto -> groq -> gemini -> ollama -> auto, live."""
        if not self.jarvis:
            return
        order = ["auto", "groq", "gemini", "ollama"]
        try:
            cur = self.jarvis.model_status()["pinned"]
        except Exception:
            cur = "auto"
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "auto"
        msg = self.jarvis.set_model(nxt)
        self._refresh_model_btn()
        self._refresh_status()
        self._write(f"  · {msg}\n", "event")

    def _toggle_autonomy(self) -> None:
        if not self.jarvis:
            return
        try:
            now = not self.jarvis.permissions.autonomy()
            self.jarvis.permissions.set_autonomy(now)
        except Exception:
            return
        self._refresh_autonomy_btn()
        if now:
            self._write("Autonomy ON - I'll act without asking, skip plan approvals, and use risky calls. "
                        "Click again to re-gate.\n", "event")
        else:
            self._write("Autonomy OFF - back to asking before each new capability.\n", "event")

    # ---- background work ------------------------------------------------------------------------

    def _boot(self) -> None:
        try:
            from core.orchestrator import Jarvis

            jarvis = Jarvis(on_event=lambda _stage, message: self.events.put(("event", message)),
                            confirm=self._confirm_action)
            for filename, error in jarvis.registry.errors.items():
                self.events.put(("error", f"Skill {filename} failed to load: {error}"))
            self.jarvis = jarvis
            self.events.put(("status", None))
        except Exception:
            self.events.put(("error", "JARVIS failed to start:\n" + traceback.format_exc(limit=4)))
            return
        try:
            from core import voice
            voice.configure(on_command=lambda text: self.events.put(("voice", text)),
                            on_state=lambda state, detail=None: self.events.put(("voice_state", (state, detail))),
                            settings=self.settings)
            if self.settings.get("voice.enabled", False):
                self.events.put(("event", voice.start()))
                if self.settings.get("voice.greet_on_start", True):
                    import datetime as _dt
                    from core.briefing import greeting
                    self._say(f"{greeting(_dt.datetime.now())}, sir. JARVIS online.")
        except Exception as exc:
            self.events.put(("event", f"Voice unavailable: {exc}"))
        try:
            from core import oslayer, reminders
            reminders.start_loop(emit=lambda text: self.events.put(("event", f"⏰ {text}")), speak=self._say,
                                 notify=lambda text: oslayer.notify(text, "JARVIS"))
        except Exception as exc:
            self.events.put(("event", f"Reminders unavailable: {exc}"))
        try:
            from core import sentinel
            sentinel.start(self.settings, emit=lambda text: self.events.put(("event", f"⚠ {text}")),
                           speak=self._say)
        except Exception as exc:
            self.events.put(("event", f"Alerts unavailable: {exc}"))

    def _confirm_action(self, req) -> str:
        """Called on a worker thread: ask the panel to show approval buttons, then block for the answer."""
        box = {"decision": "deny"}
        answered = threading.Event()
        self.events.put(("confirm", (req, box, answered)))
        answered.wait(timeout=180)
        return box["decision"]

    def _work(self, text: str, spoken: bool = False) -> None:
        try:
            reply, job = self.jarvis.submit(text)
            self.events.put(("reply", reply))
            self._maybe_speak(reply, spoken)
            if job is not None:
                # A skill is being built off-thread; the panel is already free for the next message.
                result = job()
                self.events.put(("reply", result))
                self._maybe_speak(result, spoken)
        except Exception:
            self.events.put(("error", traceback.format_exc(limit=4)))
            if spoken:
                self._say("Sorry, sir, something went wrong. The details are in the panel.")

    def _say(self, text: str) -> None:
        try:
            from core import voice
            spk = voice.speaker()
            if spk is not None:
                spk.say(text)
        except Exception:
            pass

    def _maybe_speak(self, reply, spoken: bool) -> None:
        """Answer aloud when you spoke to JARVIS (voice.speak_replies = voice | always | never)."""
        mode = str(self.settings.get("voice.speak_replies", "voice") or "voice").lower()
        if mode == "never" or (mode == "voice" and not spoken):
            return
        if getattr(reply, "skill", None) in ("speak", "speak_text"):
            return                                   # it already said it
        text = getattr(reply, "text", "") or ""
        if getattr(reply, "route", "") == "building":
            text = "On it, sir. I'm building that capability now - I'll tell you when it's ready."
        self._say(text)

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "toggle":
                    self.toggle(payload)
                elif kind == "show":
                    self.show(payload)
                elif kind == "hide":
                    self.hide()
                elif kind == "quit":
                    self.quit()
                    return
                elif kind == "event":
                    self._write(f"  · {payload}\n", "event")
                elif kind == "status":
                    self._refresh_status()
                    self._refresh_autonomy_btn()
                    self._refresh_model_btn()
                    if self.reactor.state_name == "offline":
                        self._set_reactor("idle")
                elif kind == "reply":
                    self.busy = False  # free the input; a background build keeps running on its own thread
                    if payload.route == "building":
                        self._write(f"  · {payload.text}\n", "event")  # reactor stays "busy" while it builds
                    else:
                        self._write_reply(payload)
                        self._set_reactor("speaking" if payload.skill == "speak" else "idle",
                                          revert_ms=2500 if payload.skill == "speak" else 0)
                    self._refresh_status()
                elif kind == "error":
                    self._write(f"{payload}\n", "error")
                    self.busy = False
                    self._set_reactor("error", revert_ms=2500)
                elif kind == "confirm":
                    self._ask_permission(*payload)
                elif kind == "interrupt":
                    if self.jarvis is not None and self.busy:
                        self.jarvis.interrupt()
                        self._write("  · Interrupting - stopping at the next step...\n", "event")
                    elif self.jarvis is not None:
                        self._write("  · Nothing running to interrupt.\n", "event")
                elif kind == "voice":
                    self._on_voice(payload)
                elif kind == "voice_state":
                    state, detail = payload
                    log.info("voice: %s %s", state, detail or "")
                    if state == "listening":
                        self._write("  🎙 Listening - say \"Jarvis\" and your request.\n", "event")
                    elif state == "loading":
                        self._write("  🎙 Loading on-device speech recognition... (if macOS asks for the "
                                    "microphone, click Allow)\n", "event")
                    elif state == "downloading":
                        self._write("  🎙 Downloading the speech model once (~140 MB)...\n", "event")
                    elif state == "awake":
                        self._set_reactor("speaking", revert_ms=1500)
                    elif state == "off":
                        self._write("  🎙 Stopped listening.\n", "event")
                    elif state == "error":
                        self._write(f"  🎙 {detail}\n", "error")
                elif kind == "agenda_done":
                    title, result = payload
                    self._write(f"  ⏱ {title}: {result}\n", "event")
                elif kind == "reloadconfig":
                    if self.jarvis is not None:
                        order = self.jarvis.reload_settings()
                        self._write(f"  · Reloaded settings. Model order: {' > '.join(order)}\n", "event")
                        self._refresh_status()
                        self._refresh_model_btn()
                elif kind == "skills":
                    if not self.visible:
                        self.show(None)
                    self._open_skills_manager()
        except queue.Empty:
            pass
        self.root.after(40, self._pump)

    def _ask_permission(self, req, box, answered) -> None:
        try:
            from core import voice
            if voice.listening():
                self._say("I need your approval on screen for that, sir.")
        except Exception:
            pass
        self._ask_permission_ui(req, box, answered)

    def _ask_permission_ui(self, req, box, answered) -> None:
        if not self.visible:
            self.show(None)
        self._write(f"  · JARVIS is asking permission: {req.summary}\n", "event")
        frame = tk.Frame(self.root, bg=C["input"], highlightbackground=C["accent"], highlightthickness=1)
        frame.pack(fill="x", padx=14, pady=(0, 4), before=self.bar)
        tk.Label(frame, text="JARVIS wants to", fg=C["dim"], bg=C["input"], font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(6, 0))
        tk.Label(frame, text=req.summary, fg=C["fg"], bg=C["input"], font=("Segoe UI Semibold", 10),
                 wraplength=360, justify="left").pack(anchor="w", padx=8)
        if req.details:
            tk.Label(frame, text=req.details, fg=C["dim"], bg=C["input"], font=("Consolas", 8),
                     wraplength=360, justify="left").pack(anchor="w", padx=8)
        buttons = tk.Frame(frame, bg=C["input"])
        buttons.pack(fill="x", padx=8, pady=6)

        def choose(decision: str) -> None:
            box["decision"] = decision
            frame.destroy()
            self.entry.configure(state="normal")
            answered.set()
            self.entry.focus_set()

        for text, decision, color in (("Allow once", "once", C["accent"]),
                                      ("Always allow", "always", C["user"]),
                                      ("Deny", "deny", C["error"])):
            tk.Button(buttons, text=text, command=lambda d=decision: choose(d), bg=C["panel"], fg=color,
                      activebackground=C["bg"], activeforeground=color, relief="flat", padx=10, pady=3,
                      cursor="hand2").pack(side="left", padx=(0, 6))
        self.entry.configure(state="disabled")

    def _refresh_status(self) -> None:
        if self.jarvis is not None:
            order = " > ".join(self.jarvis.llm.order())
            self.status.configure(text=f"{len(self.jarvis.registry.skills)} skills  ·  {order}")

    # ---- input ---------------------------------------------------------------------------------

    def _submit(self, _event=None):
        text = self.entry.get().strip()
        if not text:
            return "break"
        self.entry.delete(0, "end")
        self.history.append(text)
        self.history_pos = len(self.history)
        if text.startswith("/"):
            self._slash(text)
        elif self.jarvis is None:
            self._write("Still starting up, one moment.\n", "event")
        elif self.busy:
            # A quick reply is still coming; a background skill build never sets this, so you can keep chatting.
            self._write("One moment, still on the last message.\n", "event")
        else:
            self._write("You  ", "label")
            self._write(f"{text}\n", "user")
            self.busy = True
            self._set_reactor("busy")
            threading.Thread(target=self._work, args=(text,), name="jarvis-request", daemon=True).start()
        return "break"

    def _on_voice(self, text: str) -> None:
        if text == "__stop__":
            if self.jarvis is not None and self.busy:
                self.jarvis.interrupt()
                self._write("  · Interrupting - stopping at the next step...\n", "event")
            return
        if self.jarvis is None:
            self._say("One moment, sir, I'm still starting up.")
            return
        if self.busy:
            self._say("One moment, sir, I'm still on the last task.")
            return
        self._write("You 🎙  ", "label")
        self._write(f"{text}\n", "user")
        self.busy = True
        self._set_reactor("busy")
        threading.Thread(target=self._work, args=(text, True), name="jarvis-voice-request", daemon=True).start()

    def _recall(self, step: int):
        if not self.history:
            return "break"
        self.history_pos = min(max(self.history_pos + step, 0), len(self.history))
        self.entry.delete(0, "end")
        if self.history_pos < len(self.history):
            self.entry.insert(0, self.history[self.history_pos])
        return "break"

    def _write_reply(self, reply) -> None:
        self._write("JARVIS  ", "label")
        self._write(f"{reply.text.strip()}\n", "jarvis")
        meta = [reply.skill and f"skill {reply.skill}", reply.route, reply.provider and f"via {reply.provider}",
                f"{reply.elapsed_s:.1f}s"]
        self._write("        " + "  ·  ".join(m for m in meta if m) + "\n", "event")

    def _slash(self, text: str) -> None:
        command = text.split()[0].lower()
        if command == "/help":
            self._write(HELP + "\n", "event")
        elif command == "/clear":
            self._hard_refresh()
        elif command == "/hide":
            self.hide()
        elif command in ("/skills", "/reload"):
            if self.jarvis is None:
                self._write("Still starting up, one moment.\n", "event")
                return
            changed = self.jarvis.registry.reload()
            if command == "/reload":
                self._write(f"Reloaded: {', '.join(changed) or 'nothing changed'}\n", "event")
            else:
                for skill in sorted(self.jarvis.registry.skills.values(), key=lambda s: (s.origin, s.name)):
                    self._write(f"  {skill.name:<24} {skill.origin}\n", "event")
            for filename, error in self.jarvis.registry.errors.items():
                self._write(f"  {filename}: {error}\n", "error")
            self._refresh_status()
        elif command == "/permissions":
            from core.permissions import CAPABILITIES

            parts = text.split()
            perms = self.jarvis.permissions if self.jarvis else None
            if perms is None:
                self._write("Still starting up, one moment.\n", "event")
                return
            if len(parts) == 3 and parts[1] in CAPABILITIES and parts[2] in ("ask", "allow", "deny"):
                perms.set(parts[1], parts[2])
                self._write(f"  {parts[1]} -> {parts[2]}\n", "event")
            else:
                for capability, description in CAPABILITIES.items():
                    self._write(f"  {capability:<12} {perms.state(capability):<6} {description}\n", "event")
                self._write("  change with: /permissions <capability> allow|deny|ask\n", "event")
        elif command == "/autonomy":
            if self.jarvis is None:
                self._write("Still starting up, one moment.\n", "event")
                return
            parts = text.split()
            perms = self.jarvis.permissions
            if len(parts) == 2 and parts[1] in ("on", "off"):
                perms.set_autonomy(parts[1] == "on")
                self._refresh_autonomy_btn()
            state = "ON - acting without asking" if perms.autonomy() else "OFF - asking before each capability"
            self._write(f"  autonomy is {state}   (use: /autonomy on|off)\n", "event")
        elif command == "/voice":
            from core import voice
            arg = text.split()[1].lower() if len(text.split()) > 1 else ""
            if arg in ("on", "start"):
                self._write(f"  🎙 {voice.start()}\n", "event")
            elif arg in ("off", "stop"):
                self._write(f"  🎙 {voice.stop()}\n", "event")
            else:
                self._write(f"  🎙 Voice is {'ON' if voice.listening() else 'off'}. /voice on | /voice off  "
                            "(or say 'start listening'). Start with JARVIS: jarvis config --set voice.enabled=true\n",
                            "event")
        elif command == "/model":
            if self.jarvis is None:
                self._write("Still starting up, one moment.\n", "event")
                return
            parts = text.split()
            if len(parts) == 2:
                self._write(f"  {self.jarvis.set_model(parts[1])}\n", "event")
                self._refresh_model_btn()
                self._refresh_status()
            else:
                ms = self.jarvis.model_status()
                self._write(f"  current: {ms['pinned']}  (auto walks: {' > '.join(ms['order'])})\n", "event")
                for name, ok, reason, model in ms["status"]:
                    self._write(f"    {name:<8} {'[ok] ' if ok else '[--] '}{model}  {'' if ok else reason}\n", "event")
                self._write("  switch: /model auto|groq|gemini|ollama  (or click the header ◆ button)\n", "event")
        elif command == "/agenda":
            from core.agenda import AgendaStore, describe_trigger
            store = AgendaStore()
            tasks = store.list()
            self._write(f"  agenda scheduler: {'ON' if store.enabled() else 'OFF'} "
                        f"(runs only while autonomy is on)\n", "event")
            if not tasks:
                self._write("  (empty)  add from a terminal: jarvis agenda add \"...\" --every 1h\n", "event")
            for t in tasks:
                flag = " " if t.get("enabled") else "×"
                self._write(f"  [{flag}] {t['id']} {t['title'][:30]:<30} {describe_trigger(t['trigger'])}"
                            f"  next {t.get('next_run', '-')}\n", "event")
        elif command == "/usage":
            from core.usage import UsageStore
            data = UsageStore().all()
            if not data:
                self._write("  No model usage recorded yet.\n", "event")
                return
            self._write(f"  {'model':<8} {'calls':>6} {'in tok':>9} {'out tok':>9}   last used\n", "event")
            for name in sorted(data, key=lambda n: -int(data[n].get("calls", 0))):
                r = data[name]
                self._write(f"  {name:<8} {int(r.get('calls', 0)):>6} {int(r.get('prompt_tokens', 0)):>9,} "
                            f"{int(r.get('completion_tokens', 0)):>9,}   {r.get('last_used', '-')}\n", "event")
        elif command == "/teach":
            parts = text.split(maxsplit=2)
            if self.jarvis is None:
                self._write("Still starting up, one moment.\n", "event")
            elif len(parts) < 3:
                self._write("Use: /teach <skill> <phrase>   e.g. /teach speak read me the news\n", "event")
            elif self.jarvis.registry.get(parts[1]) is None:
                self._write(f"No skill '{parts[1]}'. See /skills.\n", "error")
            else:
                self.jarvis.learning.teach(parts[2], parts[1])
                self._write(f"Learned: '{parts[2]}' -> {parts[1]}\n", "event")
        elif command == "/lessons":
            if self.jarvis is None:
                self._write("Still starting up, one moment.\n", "event")
                return
            hints = self.jarvis.learning.hints()
            self._write(f"Learned routes ({len(hints)}):\n", "event")
            for hint in hints[-15:]:
                self._write(f"  '{hint.get('phrase','')}' -> {hint.get('skill')}\n", "event")
            for text_line in self.jarvis.learning.recent_lessons(limit=10):
                self._write(f"  - {text_line}\n", "event")
        else:
            self._write(f"Unknown command {command}. Try /help.\n", "error")

    # ---- skills manager --------------------------------------------------------------------------

    def _open_skills_manager(self) -> None:
        if self._mgr is not None and self._mgr.winfo_exists():
            self._mgr.lift()
            self._refresh_skills()
            return
        win = tk.Toplevel(self.root)
        self._mgr = win
        win.title("J.A.R.V.I.S — Skills")
        win.configure(bg=C["bg"])
        win.geometry("620x440")
        win.minsize(480, 320)

        left = tk.Frame(win, bg=C["bg"])
        left.pack(side="left", fill="y", padx=(10, 6), pady=10)
        tk.Label(left, text="SKILLS", fg=C["dim"], bg=C["bg"], font=("Consolas", 8)).pack(anchor="w")
        self._sk_list = tk.Listbox(left, width=24, bg=C["input"], fg=C["fg"], selectbackground=C["accent"],
                                   selectforeground=C["bg"], relief="flat", highlightthickness=0,
                                   font=("Consolas", 9), activestyle="none")
        self._sk_list.pack(fill="y", expand=True, pady=(4, 0))
        self._sk_list.bind("<<ListboxSelect>>", self._on_skill_select)

        right = tk.Frame(win, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(6, 10), pady=10)
        self._sk_detail = tk.Text(right, wrap="word", bg=C["panel"], fg=C["fg"], relief="flat", height=10,
                                  padx=10, pady=8, font=("Consolas", 9), state="disabled")
        self._sk_detail.pack(fill="both", expand=True)
        self._sk_detail.tag_configure("h", foreground=C["accent"], font=("Segoe UI Semibold", 11))
        self._sk_detail.tag_configure("k", foreground=C["dim"], font=("Consolas", 8))

        buttons = tk.Frame(right, bg=C["bg"])
        buttons.pack(fill="x", pady=(8, 0))

        def button(parent, text, command, fg=C["fg"], side="left"):
            b = tk.Button(parent, text=text, command=command, bg=C["panel"], fg=fg, activebackground=C["input"],
                          activeforeground=fg, relief="flat", padx=10, pady=3, cursor="hand2", font=("Segoe UI", 9))
            b.pack(side=side, padx=(0, 6))
            return b

        self._sk_toggle_btn = button(buttons, "Disable", self._toggle_skill, C["accent"])
        button(buttons, "View source", self._view_source)
        self._sk_delete_btn = button(buttons, "Delete", self._delete_skill, C["error"])
        button(buttons, "Refresh", self._refresh_skills, C["dim"], side="right")
        self._refresh_skills()

    def _list_skill_files(self) -> list[dict]:
        items = []
        for path in sorted(SKILLS_DIR.glob("*.py")) + sorted(SKILLS_DIR.glob("*.py.disabled")):
            if path.name.startswith("_"):
                continue
            meta = _skill_meta_from_file(path)
            disabled = path.name.endswith(".disabled")
            items.append({"path": path, "disabled": disabled, "meta": meta,
                          "name": meta.get("name") or path.name.split(".")[0],
                          "origin": meta.get("origin", "builtin")})
        return sorted(items, key=lambda i: (i["origin"] != "builtin", i["name"]))

    def _refresh_skills(self) -> None:
        if self._mgr is None or not self._mgr.winfo_exists():
            return
        self._sk_items = self._list_skill_files()
        self._sk_list.delete(0, "end")
        for item in self._sk_items:
            mark = "✗ " if item["disabled"] else ("● " if item["origin"] == "evolved" else "  ")
            self._sk_list.insert("end", f"{mark}{item['name']}")
        self._set_detail(f"{len(self._sk_items)} skills. Select one to see details.\n"
                         "● evolved   ✗ disabled", None)

    def _selected(self) -> dict | None:
        sel = self._sk_list.curselection()
        return self._sk_items[sel[0]] if sel and sel[0] < len(self._sk_items) else None

    def _set_detail(self, text: str, item: dict | None) -> None:
        self._sk_detail.configure(state="normal")
        self._sk_detail.delete("1.0", "end")
        if item is not None:
            meta = item["meta"]
            self._sk_detail.insert("end", item["name"] + "\n", "h")
            status = "disabled" if item["disabled"] else "active"
            self._sk_detail.insert("end", f"{item['origin']} · {status}\n\n", "k")
            self._sk_detail.insert("end", (meta.get("description") or "(no description)") + "\n\n")
            self._sk_detail.insert("end", "triggers\n", "k")
            for trigger in meta.get("triggers", []):
                self._sk_detail.insert("end", f"  /{trigger}/\n")
            if meta.get("requires"):
                self._sk_detail.insert("end", "\nrequires: " + ", ".join(meta["requires"]) + "\n", "k")
        else:
            self._sk_detail.insert("end", text)
        self._sk_detail.configure(state="disabled")

    def _on_skill_select(self, _event=None) -> None:
        item = self._selected()
        if item is None:
            return
        self._set_detail("", item)
        self._sk_toggle_btn.configure(text="Enable" if item["disabled"] else "Disable")
        self._sk_delete_btn.configure(state="normal" if item["origin"] == "evolved" else "disabled")

    def _reload_registry(self) -> None:
        if self.jarvis is not None:
            self.jarvis.registry.reload()
            self._refresh_status()

    def _toggle_skill(self) -> None:
        item = self._selected()
        if item is None:
            return
        path = item["path"]
        target = str(path)[:-len(".disabled")] if item["disabled"] else str(path) + ".disabled"
        try:
            os.replace(str(path), target)
        except OSError as exc:
            self._set_detail(f"Couldn't change '{item['name']}': {exc}", None)
            return
        self._reload_registry()
        self._refresh_skills()

    def _delete_skill(self) -> None:
        from tkinter import messagebox

        item = self._selected()
        if item is None or item["origin"] != "evolved":
            return
        if not messagebox.askyesno("Delete skill", f"Delete the evolved skill '{item['name']}'?\n"
                                   "A copy is kept in the archive.", parent=self._mgr):
            return
        try:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            os.replace(str(item["path"]), str(ARCHIVE_DIR / f"{item['name']}.deleted-{time.strftime('%Y%m%d-%H%M%S')}.py"))
        except OSError as exc:
            self._set_detail(f"Couldn't delete '{item['name']}': {exc}", None)
            return
        if self.jarvis is not None:
            self.jarvis.learning.forget_skill(item["name"])
        self._reload_registry()
        self._refresh_skills()

    def _view_source(self) -> None:
        item = self._selected()
        if item is None:
            return
        try:
            source = item["path"].read_text(encoding="utf-8")
        except OSError as exc:
            source = f"(couldn't read file: {exc})"
        viewer = tk.Toplevel(self._mgr)
        viewer.title(f"{item['name']} — source")
        viewer.configure(bg=C["bg"])
        viewer.geometry("640x480")
        text = tk.Text(viewer, wrap="none", bg=C["panel"], fg=C["fg"], relief="flat", padx=10, pady=8,
                       font=("Consolas", 9))
        text.insert("1.0", source)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, padx=10, pady=10)

    # ---- window state ----------------------------------------------------------------------------

    def _hwnd(self) -> int:
        return win32.toplevel(self.root.winfo_id())

    def show(self, target=None) -> None:
        self.root.deiconify()
        self.root.update_idletasks()
        if self.is_mac:  # macOS: always size our own 40% column; move the other window if AX is available
            try:
                if self.mac_split is not None:
                    note = self.mac_split.enter(self.root, target)
                    if note.startswith("No app"):
                        self._write(f"  · {note}\n", "event")
                else:
                    self._place_column_tk()
            except Exception as exc:
                self._place_column_tk()
                self._write(f"Window split failed: {exc}\n", "error")
            self.visible = True
            self.reactor.set_visible(True)
            self._raise_mac()
            self.entry.focus_force()
            return
        hwnd = self._hwnd()
        try:
            note = self.split.enter(hwnd, target)
            if note.startswith("Could not") or note.startswith("No app"):
                self._write(f"  · {note}\n", "event")
        except OSError as exc:
            self._write(f"Window split failed: {exc}\n", "error")
        self.visible = True
        self.reactor.set_visible(True)
        win32.bring_to_front(hwnd)
        self.entry.focus_force()

    def _place_column_tk(self) -> None:
        """Size and position the panel as the 40% column using Tk's own screen metrics (works with no
        pyobjc). ~25px is left for the menu bar; the Dock may overlap the bottom slightly."""
        from window_manager.split import compute_layout
        from window_manager.win32 import Rect
        menubar = 25
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        _main, jarvis = compute_layout(Rect(0, menubar, sw, sh - menubar), self.split.ratio, self.split.jarvis_side)
        self.root.geometry(f"{jarvis.width}x{jarvis.height}+{jarvis.left}+{jarvis.top}")

    def _raise_mac(self) -> None:
        """Bring the panel above other apps and give it keyboard focus on macOS."""
        macwm.activate_self()
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(120, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass

    def hide(self) -> None:
        try:
            if self.mac_split is not None:
                self.mac_split.exit(self.root)
            else:
                self.split.exit()
        except OSError as exc:
            log.warning("restoring window failed: %s", exc)
        self.root.withdraw()
        self.visible = False
        self.reactor.set_visible(False)  # stop animating while hidden

    def toggle(self, target=None) -> None:
        if self.visible:
            self.hide()
        else:
            self.show(target)

    def quit(self) -> None:
        if self.visible:
            self.hide()
        self.root.destroy()


def run_daemon(show: bool = False) -> int:
    win32.set_dpi_awareness()
    log_path = user_dir() / "daemon.log"
    if sys.stdout is None or sys.stderr is None:  # pythonw has no console
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    logging.basicConfig(filename=log_path, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    settings = load_settings()
    initial_target = win32.foreground()  # captured before Tk exists, so it is the window you were in
    panel = JarvisPanel(settings)

    hotkey = HotkeyListener(panel.hotkey, lambda: panel.events.put(("toggle", win32.foreground())))
    hotkey.start()
    hotkey.ready.wait(3)
    if hotkey.error:
        log.warning("hotkey: %s", hotkey.error)
        panel.events.put(("error", f"Global shortcut unavailable: {hotkey.error}"))

    interrupt_hotkey = HotkeyListener(panel.interrupt_hotkey, lambda: panel.events.put(("interrupt", None)),
                                      hotkey_id=0x4A42, name="jarvis-interrupt-hotkey")
    interrupt_hotkey.start()
    interrupt_hotkey.ready.wait(3)
    if interrupt_hotkey.error:
        log.warning("interrupt hotkey: %s", interrupt_hotkey.error)
        panel.events.put(("event", f"Interrupt shortcut unavailable: {interrupt_hotkey.error}"))

    def on_command(command: str) -> dict:
        command = command.strip().lower()
        if command == "ping":
            return {"ok": True, "pid": os.getpid(), "visible": panel.visible, "hotkey_error": hotkey.error}
        if command in ("toggle", "show"):
            panel.events.put((command, win32.foreground()))
            return {"ok": True}
        if command in ("hide", "quit"):
            panel.events.put((command, None))
            return {"ok": True}
        if command == "skills":
            panel.events.put(("skills", None))
            return {"ok": True}
        if command == "interrupt":
            panel.events.put(("interrupt", None))
            return {"ok": True}
        if command == "reloadconfig":
            panel.events.put(("reloadconfig", None))
            return {"ok": True}
        return {"ok": False, "error": f"unknown command '{command}'"}

    def agenda_loop():
        """Work JARVIS's own to-do list on schedule - but only while autonomy is on (unattended action
        can't stop to ask), so this is safe by default and stops the moment you re-gate."""
        import time as _time
        while True:
            _time.sleep(20)
            jarvis = panel.jarvis
            if jarvis is None:
                continue
            try:
                if not jarvis.agenda.enabled():
                    continue
                if not (jarvis.permissions and jarvis.permissions.autonomy()):
                    continue  # gated: hold scheduled tasks until you unleash it
                for task in jarvis.agenda.due():
                    try:
                        result = jarvis.run_agenda_task(task)
                    except Exception as exc:
                        result = f"error: {exc}"
                    jarvis.agenda.mark_ran(task["id"], result)
                    panel.events.put(("agenda_done", (task.get("title", "task"), result)))
            except Exception:
                pass

    threading.Thread(target=agenda_loop, name="jarvis-agenda", daemon=True).start()

    server = ControlServer(on_command, int(settings.get("window.ipc_port", 47821)))
    server.start()
    log.info("JARVIS daemon started (pid %s, hotkey %s, control port %s)", os.getpid(), panel.hotkey, server.port)
    if show:
        panel.events.put(("show", initial_target))
    try:
        panel.root.mainloop()
    finally:
        hotkey.stop()
        interrupt_hotkey.stop()
        server.close()
        log.info("JARVIS daemon stopped")
    return 0
