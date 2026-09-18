"""The orchestrator: understand a message, then route it to a skill, a chat reply, or evolution.

For each message, in order:
  1. Multi-step? split "do A and B" into steps and run each (deterministic, no LLM).
  2. Does a skill trigger match? run the best; if it declines, try the next.
  3. Otherwise interpret it (see core/interpreter.py): corrections and small talk deterministically,
     then an LLM dispatcher that decides chat vs. an existing skill vs. a new skill, using the recent
     conversation as context. This is what lets JARVIS chat and follow corrections instead of turning
     every stray sentence into a new skill.
"""
from __future__ import annotations

import collections
import re
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from evolution_engine.engine import EvolutionEngine
from memory.learning import LearningStore
from memory.store import MemoryStore

from evolution_engine.analyzer import is_meta_request, looks_actionable

from .actions import ActionBroker, ActionRequest, Blocked
from .actions import _normalize_decision
from .config import MEMORY_DIR, ROOT, Settings, load_settings
from .focus import Focus
from .interpreter import normalize_command, smalltalk_reply
from .llm_router import LLMError, LLMRouter
from .permissions import PermissionRegistry
from .refs import resolve_references
from .skill_loader import Skill, SkillRegistry


# Split "open X and then do Y" into steps; strip leading filler from each ("and", "then", "operate"...).
_CONNECTORS = re.compile(r"\s*(?:,|;|&|\b(?:and\s+then|then|and\s+also|and|after\s+that|next)\b)\s+", re.IGNORECASE)
_FILLER = re.compile(r"^(?:and\s+|then\s+|also\s+|next\s+|after\s+that\s+|please\s+|now\s+|can\s+you\s+|"
                     r"could\s+you\s+|go\s+|just\s+|do\s+|operate\s+|calculate\s+|compute\s+|solve\s+|"
                     r"evaluate\s+|work\s+out\s+)+", re.IGNORECASE)

CHAT_SYSTEM = ("You are JARVIS, a concise, friendly assistant running on the user's Windows PC. "
               "Chat naturally and briefly. If the user seems to want a task done, offer to do it.")

# A messaging command whose body has commas ("...saying hi, everyone") must NOT be treated as a
# multi-step chain or handed to evolution - it goes straight to the whatsapp/google_chat skill. We
# recognise it by a channel word AND a message-body lead-in, so a mere mention ("search whatsapp
# help") doesn't get hijacked.
_MSG_CHANNEL = re.compile(r"\bwhats?app\b|\bwhat'?s\s?app\b|\bgoogle\s+chat\b|\bon\s+(?:google\s+)?chat\b|"
                          r"\bchat\s+(?:to|message)\b", re.IGNORECASE)
_MSG_BODY = re.compile(r"\bsaying\b|\bthat\s+says\b|\bthe\s+(?:message|text)\s+is\b|:|\s[-–—]\s|"
                       r"[\"“]", re.IGNORECASE)
_MSG_SKILLS = ("whatsapp", "google_chat")

# "improve/upgrade/fix your <name> skill": rewrite an existing evolved skill rather than build a new one.
_IMPROVE_VERB = re.compile(r"\b(improve|upgrade|enhance|optimi[sz]e|refine|rewrite|fix|make\s+\w+\s+better|better)\b",
                           re.IGNORECASE)

# "rewrite/change/edit your own code/source (or a named .py file)": modify JARVIS's OWN source, safely.
_SELF_EDIT = re.compile(
    r"(?:(?:rewrite|edit|change|modify|update|refactor|patch|fix|add\s+to|extend)\b[^.]*\b"
    r"your\s+(?:own\s+)?(?:code|source|source\s*code|program|orchestrator|router|panel|"
    r"[a-z_]+\.py|[a-z_]+\s+(?:module|file|code)))"
    r"|(?:\brewrite\s+yourself\b)|(?:\bmodify\s+your\s+own\b)|(?:\bedit\s+your\s+own\b)",
    re.IGNORECASE)

# Signs that a skill's returned text is actually a code failure it swallowed (not a legitimate answer
# like "no results" or "I couldn't find that app"). Used to trigger an adaptive rebuild.
_FAILURE_OUTPUT = re.compile(
    r"traceback \(most recent|no module named|could not be imported|is not defined|"
    r"object has no attribute|\b(?:name|type|attribute|key|index|value|import|module|runtime|zerodivision)error\b|"
    r"failed to \w|an (?:unexpected )?error occurred|unexpected error", re.IGNORECASE)


class Cancelled(Exception):
    """Raised at a checkpoint when the user interrupted the current task (Ctrl+Alt+C)."""


@dataclass
class Reply:
    text: str
    skill: str | None
    route: str
    provider: str | None = None
    elapsed_s: float = 0.0


class Jarvis:
    def __init__(self, settings: Settings | None = None, *, llm=None, registry: SkillRegistry | None = None,
                 memory: MemoryStore | None = None, archive_dir: Path | None = None, on_event=None,
                 permissions: PermissionRegistry | None = None, confirm=None, learning: LearningStore | None = None):
        self.settings = settings or load_settings()
        self.on_event = on_event
        # confirm(ActionRequest) -> "once" | "always" | "deny"; None means every action is declined.
        self.confirm = confirm
        self.permissions = permissions if permissions is not None else PermissionRegistry()
        try:
            from .config import user_dir

            self._state_dir = user_dir()
        except OSError:
            self._state_dir = None
        self._focus = Focus(self._state_dir)  # tracks what "it"/"the photo"/"the file" refer to
        self.llm = llm if llm is not None else LLMRouter(self.settings)
        if hasattr(self.llm, "on_progress"):
            self.llm.on_progress = lambda message: self._emit("llm", message)
        self.registry = registry if registry is not None else SkillRegistry()
        self.registry.reload()
        self.memory = memory if memory is not None else MemoryStore(
            MEMORY_DIR, int(self.settings.get("memory.max_interactions", 5000)))
        self.learning = learning if learning is not None else LearningStore(self.memory.directory)
        self.evolution = EvolutionEngine(self.llm, self.registry, self.settings, self.memory,
                                         emit=self._emit, archive_dir=archive_dir, learning=self.learning,
                                         installer=self._install_package, permissions=self.permissions)
        self.history: collections.deque = collections.deque(maxlen=12)  # ("you"/"jarvis", text) for context
        self._agent_max_steps = int(self.settings.get("agent.max_steps", 5))  # cap on the act-observe-adapt loop
        self._cancel = threading.Event()       # set by interrupt() to stop the current task at the next checkpoint
        self.evolution.cancel_check = self._cancelled  # let the build loop bail out on interrupt too
        from evolution_engine.self_edit import SelfEditor  # rewrites JARVIS's own source, on command

        self._self_editor = SelfEditor(self.llm, ROOT, emit=self._emit, archive_dir=archive_dir,
                                       run_tests=bool(self.settings.get("self_edit.run_tests", True)),
                                       git_commit=bool(self.settings.get("self_edit.git_commit", True)))
        from .agenda import AgendaStore
        self.agenda = AgendaStore()    # JARVIS's own to-do list, worked by the daemon's scheduler
        self._lock = threading.Lock()          # serializes the quick decision phase
        self._evolve_lock = threading.Lock()   # one background build at a time

    def _emit(self, stage: str, message: str) -> None:
        if self.on_event:
            try:
                self.on_event(stage, message)
            except Exception:
                pass

    # ---- interruption ---------------------------------------------------------------------------

    def interrupt(self) -> None:
        """Ask the current task to stop. Cooperative: it ends at the next checkpoint (between steps,
        build attempts, or retries), not mid-network-call. Safe to call from any thread."""
        self._cancel.set()
        self._emit("interrupt", "Interrupt received - stopping at the next step...")

    def _cancelled(self) -> bool:
        return self._cancel.is_set()

    def _raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def handle(self, request: str, *, force_evolve: bool = False) -> Reply:
        """Full synchronous handling (used by the CLI and tests): fast path, then evolution if needed."""
        request = (request or "").strip()
        if not request:
            return Reply("", None, "empty")
        started = time.monotonic()
        self._cancel.clear()
        with self._lock:
            try:
                outcome = self._fast(request, force_evolve)
                if outcome is None:
                    outcome = self._evolve_new(request)
            except Cancelled:
                outcome = ("Stopped.", None, "interrupted")
            return self._finish(request, outcome, started)

    def submit(self, request: str, *, force_evolve: bool = False):
        """Reply now, but if a skill has to be built, hand back a background job instead of blocking.

        Returns (reply, job): job is None for anything answered immediately (chat, an existing skill,
        a correction, a chain). Otherwise reply is a short acknowledgement and job() runs the slow
        build off the main thread and returns the final Reply, so the panel stays usable meanwhile.
        """
        request = (request or "").strip()
        if not request:
            return Reply("", None, "empty"), None
        started = time.monotonic()
        self._cancel.clear()
        with self._lock:
            try:
                outcome = self._fast(request, force_evolve)
            except Cancelled:
                outcome = ("Stopped.", None, "interrupted")
        if outcome is not None:
            return self._finish(request, outcome, started), None

        ack = Reply("On it - building a new skill for this. You can keep chatting; I'll run it when it's ready.",
                    None, "building")

        def job() -> Reply:
            job_started = time.monotonic()
            with self._evolve_lock:  # one build at a time, so two requests don't load the model twice
                try:
                    result = self._evolve_new(request)
                except Cancelled:
                    result = ("Stopped the build.", None, "interrupted")
            return self._finish(request, result, job_started)

        return ack, job

    def _resolve_refs(self, request: str) -> str:
        """Rewrite a back-reference ("open it", "the photo") to the concrete thing the conversation is
        about, so routing and understanding get a target instead of a dangling pronoun."""
        try:
            resolved, changed = resolve_references(request, self._focus)
        except Exception:
            return request
        if changed and resolved != request:
            self._emit("context", f"Understood '{request}' as: {resolved}")
            return resolved
        return request

    def _fast(self, request: str, force_evolve: bool):
        """The quick decisions. None => needs a build. Cloud brain -> LLM understanding; local -> rules."""
        self.llm.last_provider = None
        self.registry.reload()  # picks up skills edited or added by hand, too
        if force_evolve:
            return None
        # Resolve "it"/"that"/"the photo" against the conversation focus BEFORE routing.
        request = self._resolve_refs(request)
        if _SELF_EDIT.search(request) or self._improve_target(request) is not None:
            return None  # "rewrite your own code" / "improve your X skill" -> the (backgroundable) build path
        direct = self._direct_message_route(request)  # explicit whatsapp/chat send -> the real skill, always
        if direct is not None:
            return direct
        return self._fast_cloud(request) if self._use_planner() else self._fast_local(request)

    def _direct_message_route(self, request: str):
        """An explicit WhatsApp/Google Chat send goes straight to the messaging skill, bypassing the
        multi-step splitter and evolution - its body legitimately contains commas/'and'/'saying'."""
        if not (_MSG_CHANNEL.search(request) and _MSG_BODY.search(request)):
            return None
        cands = [s for s in self.candidates(request) if s.name in _MSG_SKILLS]
        if not cands:
            return None
        return self._try_skills(cands, request, "trigger")

    def _fast_cloud(self, request: str):
        """A capable model is available: rules handle the obvious instant cases, the model understands the rest."""
        # Instant, free: a single (non multi-step) request a trigger fully handles.
        if not _CONNECTORS.search(request):
            handled = self._try_skills(self.candidates(request), request, "trigger")
            if handled is not None:
                return handled
            command, was_correction = normalize_command(request)
            if command != request:
                handled = self._try_skills(self.candidates(command), command,
                                           "corrected" if was_correction else "trigger")
                if handled is not None:
                    return handled
        else:
            command, _ = normalize_command(request)
        # Deterministic conversational shortcuts (no model needed).
        canned = smalltalk_reply(command, self.registry)
        if canned is not None:
            return canned, None, "chat"
        if is_meta_request(command):
            from evolution_engine.engine import META_REPLY

            return META_REPLY, None, "chat"
        # Everything else: let the model understand it (intent, multi-step, preferences).
        understood = self._understand_and_act(request)
        if understood is not None:
            return understood
        return None  # understanding unavailable -> evolution builds for the whole request

    def _fast_local(self, request: str):
        """No cloud brain (offline or 0.5B): the deterministic rule pipeline."""
        chained = self._chain(request)
        if chained is not None:
            return chained
        handled = self._try_skills(self.candidates(request), request, "trigger")
        if handled is not None:
            return handled
        command, was_correction = normalize_command(request)
        if command != request:
            handled = self._try_skills(self.candidates(command), command, "corrected" if was_correction else "trigger")
            if handled is not None:
                return handled
        return self._interpret(request, command)  # small talk / chat / None (build)

    def _finish(self, request: str, outcome: tuple, started: float) -> Reply:
        text, skill_name, route = outcome
        reply = Reply(text, skill_name, route, self.llm.last_provider, time.monotonic() - started)
        self.history.append(("you", request))
        self.history.append(("jarvis", text))
        try:
            self.memory.record(request, text, skill_name, route, reply.provider)
        except OSError:
            pass
        return reply

    def _try_skills(self, skills, request: str, route: str):
        """Run matching skills in order; a skill may decline (return None), then the next tries."""
        for skill in skills:
            self._emit("route", f"Using skill '{skill.name}'")
            text = self._execute(skill, request)
            if text is not None:
                return text, skill.name, route
            self._emit("route", f"'{skill.name}' declined the request.")
        return None

    def _interpret(self, request: str, command: str):
        """No skill matched. Decide: small talk, self-edit, chat, or (None) an actionable task to build.

        `command` is `request` with politeness/corrections stripped, used for intent detection.
        """
        # 1. Obvious small talk -> a canned reply, no model call.
        canned = smalltalk_reply(command, self.registry)
        if canned is not None:
            return canned, None, "chat"

        # 2. Asking me to edit my own code/prompt -> decline (gaining new abilities is fine; that's below).
        if is_meta_request(command):
            from evolution_engine.engine import META_REPLY

            return META_REPLY, None, "chat"

        # 3. An action/compute verb means a task to build; everything else is conversation. Deterministic
        #    on purpose: a 0.5B classifier proved unreliable (it once called "tell me a joke" an action)
        #    and slow. Action words are covered by triggers and the verb list; the rest is chat.
        if looks_actionable(command):
            return None  # actionable, no skill yet -> evolution builds one
        return self._chat_reply(request), None, "chat"

    def _chat_reply(self, request: str) -> str:
        messages = [{"role": "system", "content": CHAT_SYSTEM}]
        for who, text in list(self.history)[-6:]:
            messages.append({"role": "user" if who == "you" else "assistant", "content": text})
        messages.append({"role": "user", "content": request})
        self._emit("chat", "Replying...")
        try:
            return self.llm.complete(messages=messages, temperature=0.5, max_tokens=300).strip() or "I'm here."
        except LLMError:
            return "I'm here, but my language model isn't reachable right now."

    def _improve_target(self, request: str) -> Skill | None:
        """The evolved skill an 'improve/fix/upgrade ... skill' request refers to, else None. Cheap, no LLM."""
        if not self.settings.get("evolution.enabled", True):
            return None
        low = request.lower()
        if "skill" not in low or not _IMPROVE_VERB.search(low):
            return None
        evolved = [s for s in self.registry.skills.values() if s.origin == "evolved"]
        if not evolved:
            return None
        for skill in evolved:  # name mentioned? ("your qr_code skill", "the weather skill")
            tokens = [t for t in skill.name.lower().split("_") if len(t) >= 2]
            if skill.name.lower() in low or any(t in low for t in tokens):
                return skill
        return evolved[0] if len(evolved) == 1 else None  # "improve your skill" with just one evolved

    def _maybe_improve(self, request: str):
        """Handle an 'improve your X skill' request by rewriting that skill. Slow (runs the evolve loop)."""
        target = self._improve_target(request)
        if target is None:
            return None
        self._emit("improve", f"Improving my '{target.name}' skill...")
        outcome = self.evolution.improve(target, reason=request)
        if outcome.kind == "cancelled":
            raise Cancelled()
        if outcome.kind == "skill":
            self.learning.add_lesson("improved", f"Rewrote '{target.name}' to be better", request)
            return f"Done - I rewrote my '{target.name}' skill and verified the new version.", target.name, "improved"
        detail = outcome.detail or "no better version passed verification"
        return f"I tried to improve '{target.name}' but couldn't: {detail}", None, "improve-failed"

    def _gate_self_edit(self) -> bool:
        """Permission check for rewriting JARVIS's own source. Autonomy allows it; otherwise ask."""
        try:
            state = self.permissions.state("self_edit")
        except Exception:
            state = "ask"
        if state == "allow":
            return True
        if state == "deny":
            return False
        if self.confirm is None:
            return False
        req = ActionRequest("self_edit", "Rewrite my own source code", details="edits a core file, runs tests, rolls back on failure")
        return _normalize_decision(self.confirm(req)) != "deny"

    def _maybe_self_edit(self, request: str):
        """Handle a 'rewrite your own code' command: edit a core file with the safety harness."""
        if not _SELF_EDIT.search(request):
            return None
        if not self._gate_self_edit():
            return "I won't rewrite my own code without permission (enable it: jarvis permissions self_edit allow).", None, "denied"
        self._raise_if_cancelled()
        result = self._self_editor.edit(request)
        if result.ok:
            self.learning.add_lesson("self_edited", f"Rewrote own source {result.file}", request)
            self.registry.reload()  # a skill self-edit hot-reloads; core needs a restart (noted in the message)
        route = "self-edited" if result.ok else "self-edit-failed"
        return result.message, (result.file if result.ok else None), route

    def _evolve_new(self, request: str, build_only: bool = False) -> tuple[str, str | None, str]:
        if not self.settings.get("evolution.enabled", True):
            return "I don't have a skill for that yet, and evolution is turned off.", None, "no-skill"

        edited = self._maybe_self_edit(request)
        if edited is not None:
            return edited

        improved = self._maybe_improve(request)
        if improved is not None:
            return improved

        outcome = self.evolution.handle_missing(request, build_only=build_only)
        if outcome.kind == "cancelled":
            raise Cancelled()
        if outcome.kind == "answer":
            return outcome.answer, None, "answered"
        if outcome.kind == "skill":
            text = self._execute(outcome.skill, request)
            if outcome.reused:
                # This phrasing had no trigger but maps to an existing skill -> route straight there next time.
                self.learning.add_route_hint(request, outcome.skill.name, source="reuse")
            route = "reused" if outcome.reused else "evolved"
            return (text if text is not None else "The new skill declined the request."), outcome.skill.name, route
        self.learning.add_lesson("build_failed", f"Couldn't build '{request}': {outcome.detail}", request)
        return f"I couldn't build that capability yet. {outcome.detail}".strip(), None, "evolution-failed"

    def candidates(self, request: str) -> list[Skill]:
        """Skills whose triggers match (best first), then any learned to fit this phrasing."""
        found = [skill for skill, _score in self.registry.match(request)]
        names = {skill.name for skill in found}
        for name in self.learning.hinted_skills(request):  # learned from past reuses / teaching
            skill = self.registry.get(name)
            if skill is not None and skill.name not in names:
                found.append(skill)
                names.add(skill.name)
        return found

    def route(self, request: str) -> tuple[Skill | None, str]:
        found = self.candidates(request)
        return (found[0], "trigger") if found else (None, "no-trigger")

    # ---- multi-step chaining ------------------------------------------------------------------

    def _chain(self, request: str):
        """Run "do A and B" as separate steps, but only when each step maps to a distinct existing skill.

        Deterministic and LLM-free, so it's fast and never spawns evolution mid-chain.
        """
        if not _CONNECTORS.search(request):
            return None
        parts = [p.strip() for p in _CONNECTORS.split(request) if p.strip()]
        if not 2 <= len(parts) <= 4:
            return None
        steps = []
        for part in parts:
            clean = _FILLER.sub("", part).strip()
            found = self.candidates(clean)
            if not found:
                return None  # a step no existing skill handles -> let the whole request be handled normally
            steps.append((clean, found))
        if len({found[0].name for _clean, found in steps}) < 2:
            return None  # every step maps to the same skill -> handle the whole request once instead

        outputs, used = [], []
        for clean, found in steps:
            # Resolve references now, after earlier steps ran: "take a photo and open it" -> the new photo.
            try:
                resolved, changed = resolve_references(clean, self._focus)
            except Exception:
                resolved, changed = clean, False
            if changed:
                clean, found = resolved, self.candidates(resolved)
            text = None
            for skill in found:
                self._emit("route", f"Step '{clean}' -> skill '{skill.name}'")
                text = self._execute(skill, clean)
                if text is not None:
                    used.append(skill.name)
                    break
            outputs.append(text if text is not None else f"(couldn't do: {clean})")

        combined = []
        for line in outputs:  # drop repeats, e.g. two steps that both reveal the same file
            if not combined or combined[-1] != line:
                combined.append(line)
        return "\n".join(combined), " + ".join(dict.fromkeys(used)) or None, "chain"

    def _subrun(self, sub_request: str, depth: int) -> str:
        """context['run'] for skills: hand a sub-request to another existing skill and return its text.

        Existing skills only (no evolution) and depth-capped, so composition can't loop or spawn builds.
        """
        sub_request = (sub_request or "").strip()
        if not sub_request:
            return ""
        if depth >= 3:
            return "(skill call nested too deep)"
        for skill in self.candidates(sub_request):
            text = self._execute(skill, sub_request, depth=depth + 1)
            if text is not None:
                return text
        return f"(no skill handles: {sub_request})"

    # ---- LLM understanding (intent, multi-step, preferences) -----------------------------------

    def _use_planner(self) -> bool:
        """True when a capable cloud model will answer (not the local 0.5B). Gates LLM understanding."""
        if not self.settings.get("planner.enabled", True):
            return False
        providers = getattr(self.llm, "providers", None)
        if not providers:
            return False  # a plain test/custom LLM -> use the deterministic rules
        for name in self.llm.order():
            provider = providers.get(name)
            if provider is not None and provider.available()[0]:
                return name != "ollama"
        return False

    def _clean_step(self, step: str) -> str:
        """Drop a leading skill-name the model sometimes prepends ('web_search find X' -> 'find X')."""
        parts = step.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() in self.registry.skills:
            return parts[1]
        return step.strip()

    def _install_package(self, pkg: str) -> str:
        """Gated pip install used by the evolution engine when a new skill needs a package."""
        broker = ActionBroker(dry_run=False, permissions=self.permissions, confirm=self.confirm, emit=self._emit)
        return broker.install_package(pkg)  # raises Blocked if the user declines

    def _run_step(self, step: str) -> tuple[str, str | None, str]:
        """Execute one step: an existing skill if one fits, otherwise build one and run it.

        build_only: the model already decided this is a task, so build+run rather than answer with a snippet.
        """
        try:                      # resolve a reference that survived into a step ("...and open it")
            step, _changed = resolve_references(step, self._focus)
        except Exception:
            pass
        handled = self._try_skills(self.candidates(step), step, "trigger")
        if handled is not None:
            return handled
        return self._evolve_new(step, build_only=True)

    def _understand_and_act(self, request: str):
        from .understand import understand

        try:
            reading = understand(self.llm, request, self.history, self.registry.summaries(),
                                 self._settings_hint(), self._focus_hint())
        except LLMError:
            return None

        if reading.intent == "preference":
            applied = self._apply_preference(reading.setting, reading.value, reading.summary)
            if applied is not None:
                return applied
            return None  # not a setting we can change -> fall through to a build

        if reading.intent == "chat":
            return (reading.reply or self._chat_reply(request)), None, "chat"

        # intent == "act": a single step runs directly; anything multi-step runs the agentic loop.
        steps = [self._clean_step(s) for s in reading.steps if s.strip()][:5] or [request]
        if len(steps) == 1:
            text, skill, route = self._run_step(steps[0])
            return text, skill, ("understood" if route == "trigger" else route)

        # Complex task: show the suggested plan and get one approval before it starts working autonomously.
        # Autonomy mode skips the approval entirely - it's unleashed and just goes.
        plan_text = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
        self._emit("plan", "Proposed plan:\n" + plan_text)
        if self.confirm is not None and not self._autonomous():
            review = ActionRequest("plan", f"Work through this (~{len(steps)} steps)", details=plan_text)
            if _normalize_decision(self.confirm(review)) == "deny":
                return "Okay, cancelled - I didn't start.", None, "cancelled"
        return self._run_agent(request, steps)

    def _autonomous(self) -> bool:
        """True when autonomy mode is unleashed: act without asking, skip plan review, allow risky calls."""
        try:
            return bool(self.permissions and self.permissions.autonomy())
        except Exception:
            return False

    def _run_agent(self, goal: str, plan: list[str]):
        """Pursue the goal step by step, deciding each next action from the last result."""
        from .agent import next_action

        transcript: list[tuple[str, str]] = []
        used: list[str] = []
        seen: set[str] = set()
        for step in range(1, self._agent_max_steps + 1):
            self._raise_if_cancelled()  # user hit Ctrl+Alt+C between steps
            try:
                decision = next_action(self.llm, goal, plan if step == 1 else None, transcript,
                                       self.registry.summaries())
            except LLMError:
                break
            if decision["done"] or not decision["next"]:
                answer = decision["answer"] or (transcript[-1][1] if transcript else "Done.")
                return answer, " + ".join(dict.fromkeys(used)) or None, "agent"
            instruction = self._clean_step(decision["next"])
            if instruction.lower() in seen:  # stuck repeating the same step -> stop
                break
            seen.add(instruction.lower())
            self._raise_if_cancelled()
            self._emit("agent", f"Step {step}: {instruction}")
            text, skill, _route = self._run_step(instruction)
            if skill:
                used.append(skill)
            transcript.append((instruction, text))
        # Ran out of steps or looped: hand back what we gathered.
        answer = transcript[-1][1] if transcript else "I couldn't complete that."
        return answer, " + ".join(dict.fromkeys(used)) or None, "agent"

    def _focus_hint(self) -> str:
        """A short 'Current context' block naming the file/image/url the conversation is about, so the
        understanding layer can resolve references the deterministic resolver didn't catch."""
        snap = self._focus.snapshot()
        labels = {"file": "file", "image": "image/photo", "url": "web page", "app": "app", "folder": "folder"}
        lines = [f"- {labels.get(slot, slot)}: {value}" for slot, value in snap.items() if slot != "file" or "image" not in snap]
        if not lines:
            return ""
        return "Current context (the things this conversation is about right now):\n" + "\n".join(lines)

    def _settings_hint(self) -> str:
        s = self.settings
        return ("Changeable settings and current values:\n"
                f"- llm.fallback_order = {s.get('llm.fallback_order')}  (providers: groq, gemini, ollama; first = primary)\n"
                f"- llm.groq.model={s.get('llm.groq.model')}, llm.gemini.model={s.get('llm.gemini.model')}, "
                f"llm.ollama.model={s.get('llm.ollama.model')}\n"
                f"- window.hotkey={s.get('window.hotkey')}, window.split={s.get('window.split')}, "
                f"window.jarvis_side={s.get('window.jarvis_side')}\n"
                f"- evolution.enabled={s.get('evolution.enabled')}")

    def _apply_preference(self, setting: str, value, summary: str):
        """Change a setting for good, from a natural-language request. Returns a reply tuple or None."""
        from .understand import ALLOWED_SETTINGS

        if setting not in ALLOWED_SETTINGS or value in (None, ""):
            return None
        value = self._coerce_setting(setting, value)
        if value is None:
            return None
        old = self.settings.get(setting)
        from .config import load_settings, set_user_value

        set_user_value(setting, value)
        self.settings = load_settings()
        note = summary or f"Set {setting} to {value}"
        if setting.startswith("llm."):
            self.llm = LLMRouter(self.settings)  # rebuild so the change is live now
            if hasattr(self.llm, "on_progress"):
                self.llm.on_progress = lambda message: self._emit("llm", message)
            self.evolution.llm = self.llm
            return f"{note}. Model order is now: {' > '.join(self.llm.order())} (was {old}).", None, "preference"
        return f"{note} (was {old}). It takes effect from now on.", None, "preference"

    def _rebuild_llm(self) -> None:
        """Point the router (and everything that holds it) at the current settings."""
        self.llm = LLMRouter(self.settings)
        if hasattr(self.llm, "on_progress"):
            self.llm.on_progress = lambda message: self._emit("llm", message)
        self.evolution.llm = self.llm
        if getattr(self, "_self_editor", None) is not None:
            self._self_editor.llm = self.llm

    def reload_settings(self) -> list[str]:
        """Re-read config from disk and rebuild the router (used when a setting changed elsewhere)."""
        from .config import load_settings
        self.settings = load_settings()
        self._rebuild_llm()
        return self.llm.order()

    def set_model(self, name: str) -> str:
        """Manually pick which model to use: a provider name pins it, 'auto' walks the fallback chain."""
        from .config import load_settings, set_user_value
        name = (name or "auto").strip().lower()
        valid = {"auto"} | set(self.llm.providers)
        if name not in valid:
            return f"Unknown model '{name}'. Choose one of: {', '.join(sorted(valid))}."
        set_user_value("llm.provider", name)
        self.settings = load_settings()
        self._rebuild_llm()
        if name == "auto":
            return f"Model set to auto - it now walks: {' > '.join(self.llm.order())}."
        return f"Model pinned to {name} ({getattr(self.llm.providers.get(name), 'model', '')})."

    def model_status(self) -> dict:
        """Current pinning + which provider each call goes to now, for display."""
        pinned = str(self.settings.get("llm.provider", "auto") or "auto").lower()
        nxt = next((n for n, ok, _r, _m in self.llm.status() if ok), None)
        return {"pinned": pinned, "order": self.llm.order(), "next": nxt, "status": self.llm.status()}

    # ---- autonomous initiative (the daemon scheduler calls these) ------------------------------

    def run_agenda_task(self, task: dict) -> str:
        """Carry out one scheduled task on JARVIS's own initiative. Returns a short result line."""
        if task.get("kind") == "reflection":
            return self.reflect()
        prompt = str(task.get("prompt", "")).strip()
        if not prompt:
            return "empty task"
        self._emit("agenda", f"Working scheduled task: {task.get('title', prompt)}")
        reply = self.handle(prompt)
        return reply.text

    def reflect(self) -> str:
        """Self-review: find the evolved skill that fails most and improve it - evolution driven by JARVIS
        itself, not a user command. Safe (improve is sandbox-verified and rolls back)."""
        self._emit("reflect", "Reflecting on my own performance...")
        evolved = {s.name for s in self.registry.skills.values() if getattr(s, "origin", "") == "evolved"}
        if not evolved:
            return "Reflection: no evolved skills yet - nothing to improve."
        try:
            lessons = self.learning.recent_lessons(limit=40, kinds=("skill_crashed", "build_failed"))
        except Exception:
            lessons = []
        tally: dict[str, int] = {}
        for text in lessons:
            m = re.search(r"'([^']+)'", str(text))
            if m and m.group(1) in evolved:
                tally[m.group(1)] = tally.get(m.group(1), 0) + 1
        if not tally:
            return f"Reflection: {len(evolved)} evolved skill(s), no recent failures - all healthy."
        worst = max(tally, key=tally.get)
        skill = self.registry.get(worst)
        if skill is None:
            return "Reflection: nothing actionable."
        self._emit("reflect", f"'{worst}' failed {tally[worst]}x recently - improving it.")
        outcome = self.evolution.improve(skill, reason=f"Recurring failures ({tally[worst]} recently); make it robust.")
        if outcome.kind == "skill":
            self.learning.add_lesson("reflected", f"Auto-improved '{worst}' after {tally[worst]} failures", worst)
            return f"Reflection: auto-improved '{worst}' (had {tally[worst]} recent failures)."
        return f"Reflection: tried to improve '{worst}' but couldn't ({outcome.detail or 'no better version'})."

    @staticmethod
    def _coerce_setting(setting: str, value):
        try:
            if setting == "llm.fallback_order":
                items = value if isinstance(value, list) else [value]
                order = [str(v).strip().lower() for v in items if str(v).strip().lower() in ("groq", "gemini", "ollama")]
                return order or None
            if setting == "llm.provider":
                v = str(value).strip().lower()
                return v if v in ("auto", "groq", "gemini", "ollama") else None
            if setting == "window.split":
                return min(max(float(value), 0.2), 0.8)
            if setting == "window.jarvis_side":
                v = str(value).strip().lower()
                return v if v in ("left", "right") else None
            if setting == "evolution.enabled":
                return bool(value) if isinstance(value, bool) else str(value).strip().lower() in ("true", "yes", "on", "1")
            return str(value).strip()  # models, hotkey
        except (ValueError, TypeError):
            return None

    def _context(self, request: str, depth: int = 0) -> dict:
        def ask_llm(prompt: str, system: str | None = None, temperature: float = 0.3, max_tokens: int = 1024) -> str:
            try:
                return self.llm.complete(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
            except LLMError as exc:
                return f"[LLM unavailable: {exc}]"

        try:
            recalled = self.memory.recall(request, int(self.settings.get("memory.recall_k", 3)))
        except OSError:
            recalled = []
        broker = ActionBroker(dry_run=False, permissions=self.permissions, confirm=self.confirm, emit=self._emit,
                              state_dir=self._state_dir, browser=self.settings.get("window.browser", "chrome"),
                              browser_port=int(self.settings.get("window.debug_port", 9222)))
        return {"dry_run": False, "llm": ask_llm, "memory": recalled, "platform": "windows",
                "emit": self._emit, "root": str(ROOT), "actions": broker, "permissions": self.permissions,
                "run": lambda sub: self._subrun(sub, depth), "skills": sorted(self.registry.skills),
                "focus": self._focus.snapshot()}

    def _execute(self, skill: Skill, request: str, depth: int = 0, repairs_left: int = 2) -> str | None:
        """Run a skill; if it fails (crashes OR returns a swallowed error), rebuild it and try again.

        This is the adaptive loop: a skill that passed the sandbox can still be wrong live (bad API call,
        wrong library usage). We observe the failure, feed it back to the drafter, and re-run - bounded.
        """
        try:
            result = skill.run(request, self._context(request, depth))
        except Blocked as exc:
            return str(exc)  # the user declined (or it's denied) - not a bug, don't repair
        except Exception:
            error = traceback.format_exc(limit=6)
        else:
            if result is None:
                return None  # the skill deliberately declined this request
            text = str(result)
            if skill.origin != "evolved" or not _FAILURE_OUTPUT.search(text):
                return text  # a real result (or a builtin we can't rebuild)
            error = f"The skill ran but returned an error instead of a result:\n{text[:600]}"

        last_line = error.strip().splitlines()[-1]
        self.learning.add_lesson("skill_crashed", f"Skill '{skill.name}' failed: {last_line}", request)
        self._raise_if_cancelled()  # don't spend a repair cycle if the user interrupted
        if skill.origin == "evolved" and repairs_left > 0 and self.settings.get("evolution.enabled", True):
            self._emit("repair", f"'{skill.name}' didn't work; fixing it and trying again...")
            repaired = self.evolution.repair(skill, request, error)
            if repaired is not None:
                return self._execute(repaired, request, depth=depth, repairs_left=repairs_left - 1)
        return f"Skill '{skill.name}' failed: {last_line}"
