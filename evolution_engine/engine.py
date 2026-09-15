"""The self-evolution loop.

    missing capability
        -> 1. analyze   (LLM: is this a skill or just an answer? name, triggers, test inputs, plan)
        -> 2. draft     (LLM writes the module)
        -> 3. verify    (static checks, then an isolated sandbox run on every test input)
             failed? feed the exact error back into step 2, up to evolution.max_attempts
        -> 4. install   (atomic write into /skills, hot-reload, hand back to the orchestrator)

The same loop repairs an evolved skill that crashes at runtime: the traceback becomes the
feedback, the old version is archived, and the fix is only installed if it verifies.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from core.config import ARCHIVE_DIR
from core.llm_router import LLMError

from core.actions import Blocked

from .analyzer import CapabilitySpec, _keywords, analyze, is_meta_request
from .drafter import draft_skill
from .prompts import ANSWER_SYSTEM
from .sandbox import (MODULE_TO_PIP, declared_requires, ensure_entry_point, enforce_skill_meta,
                      missing_module, run_sandbox, static_check, tidy_top_level)


@dataclass
class EvolutionOutcome:
    kind: str  # "skill", "answer" or "failed"
    skill: object = None
    answer: str = ""
    detail: str = ""
    attempts: int = 0
    spec: CapabilitySpec | None = None
    reused: bool = False  # skill is an existing one we reused rather than building a duplicate


META_REPLY = ("I can't edit my own code or prompt from here. But I do gain new abilities the normal way - "
              "just tell me the task (\"read this aloud: hello\", \"take a screenshot\") and I'll build a skill for it.")


def _first_line(text: str, limit: int = 160) -> str:
    lines = [line.strip() for line in str(text).strip().splitlines() if line.strip()]
    return (lines[-1] if lines and lines[0].endswith(":") and len(lines) > 1 else (lines[0] if lines else ""))[:limit]


class EvolutionEngine:
    def __init__(self, llm, registry, settings, memory=None, emit=None, archive_dir: Path | None = None,
                 learning=None, installer=None, permissions=None):
        self.llm = llm
        self.registry = registry
        self.memory = memory
        self.learning = learning
        self.installer = installer  # callable(pkg) -> message; raises actions.Blocked if the user declines
        self.permissions = permissions  # to honour autonomy mode (risky calls allowed while unleashed)
        self.cancel_check = None  # optional callable() -> bool; True means the user interrupted, stop building
        self.emit = emit
        self.archive_dir = Path(archive_dir or ARCHIVE_DIR)
        self.max_attempts = max(1, int(settings.get("evolution.max_attempts", 3)))
        self.timeout_s = float(settings.get("evolution.sandbox_timeout_s", 20))
        self.memory_mb = int(settings.get("evolution.sandbox_memory_mb", 256))
        self._allow_risky_setting = bool(settings.get("evolution.allow_risky_calls", False))

    @property
    def allow_risky(self) -> bool:
        # Risky calls (delete files, eval/exec, ctypes, shell=True) are permitted when the setting is on
        # OR while autonomy mode is unleashed - the user has explicitly taken the gloves off.
        if self._allow_risky_setting:
            return True
        try:
            return bool(self.permissions and self.permissions.autonomy())
        except Exception:
            return False

    def _emit(self, stage: str, message: str) -> None:
        if self.emit:
            self.emit(stage, message)

    def _install_pkg(self, pkg: str) -> bool:
        """Install one package via the orchestrator's gated installer. True if available afterwards."""
        if not self.installer:
            return False
        self._emit("packages", f"Installing package '{pkg}'...")
        message = self.installer(pkg)  # may raise Blocked if the user declines
        return "installed" in str(message).lower() or "already" in str(message).lower()

    def _verify(self, code, spec: CapabilitySpec, request: str):
        """Verify in the sandbox, first installing declared packages, then any module it turns out to miss."""
        for pkg in declared_requires(code):
            self._install_pkg(pkg)  # best effort; a real miss is caught by the loop below
        tried: set[str] = set()
        result = None
        for _ in range(3):
            result = run_sandbox(code, expected_name=spec.name, must_match=request, inputs=spec.test_inputs,
                                 timeout_s=self.timeout_s, memory_mb=self.memory_mb)
            if result.ok:
                return result
            module = missing_module(result.error)
            if not module or module in tried:
                return result
            tried.add(module)
            pkg = MODULE_TO_PIP.get(module, module)
            if not self._install_pkg(pkg):
                return result
            self._emit("packages", f"Installed '{pkg}'; re-verifying the skill...")
        return result

    def _recent_lessons(self) -> list[str]:
        if self.learning is None:
            return []
        try:
            return self.learning.recent_lessons(limit=4, kinds=("build_failed", "skill_crashed", "taught"))
        except OSError:
            return []

    def _log(self, event: str, **fields) -> None:
        if self.memory is not None:
            try:
                self.memory.log_evolution({"event": event, **fields})
            except OSError:
                pass

    def _fail(self, request: str, detail: str, spec: CapabilitySpec | None = None, attempts: int = 0) -> EvolutionOutcome:
        self._log("failed", request=request, skill=spec.name if spec else None, attempts=attempts, detail=detail[-2000:])
        self._emit("failed", f"Evolution failed: {_first_line(detail)}")
        return EvolutionOutcome("failed", detail=detail[:600], attempts=attempts, spec=spec)

    # ---- entry points -------------------------------------------------------------------

    def handle_missing(self, request: str, build_only: bool = False) -> EvolutionOutcome:
        # build_only: the understanding layer already decided this is a task -> always build a skill,
        # never fall back to answering with a snippet.
        if not build_only and is_meta_request(request):
            # "give yourself a voice module" etc.: decline fast instead of thrashing the model.
            self._emit("answer", "That would change JARVIS itself; answering instead of building.")
            self._log("meta_declined", request=request)
            return EvolutionOutcome("answer", answer=META_REPLY)

        self._emit("analyze", "No skill covers that. Analyzing the missing capability...")
        try:
            spec = analyze(self.llm, request, self.registry.summaries())
        except LLMError as exc:
            return self._fail(request, f"Evolution needs an LLM and none answered. {exc}")

        if build_only and spec.kind == "answer":
            spec.kind = "skill"  # force building: the caller wants it done, not explained

        if spec.kind == "answer":
            self._emit("answer", "No new code needed; answering directly.")
            self._log("answered", request=request)
            try:
                answer = self.llm.complete(request, system=ANSWER_SYSTEM, temperature=0.4, max_tokens=700)
            except LLMError as exc:
                return self._fail(request, str(exc), spec)
            return EvolutionOutcome("answer", answer=answer.strip(), spec=spec)

        existing = self._find_duplicate(spec, request)
        if existing is not None:
            self._emit("reuse", f"'{existing.name}' already covers this; reusing it instead of building a copy.")
            self._log("reused", request=request, skill=existing.name)
            return EvolutionOutcome("skill", skill=existing, spec=spec, reused=True)

        spec.name = self._free_name(spec.name)
        self._emit("plan", f"New capability '{spec.name}': {spec.description}")
        self._log("analyzed", request=request, skill=spec.name, triggers=spec.triggers, test_inputs=spec.test_inputs)
        return self._evolve(spec, request, target=self.registry.directory / f"{spec.name}.py")

    def _find_duplicate(self, spec: CapabilitySpec, request: str):
        """An existing skill that already covers this capability, so we reuse rather than clone."""
        # Compare the proposed capability (its description + name), not the request, which would dilute it.
        want = set(_keywords(spec.description)) | {p for p in spec.name.split("_") if p.isalpha() and len(p) > 2}
        want.discard("skill")
        if not want:
            return None
        best, best_score = None, 0.0
        for skill in self.registry.skills.values():
            have = set(_keywords(skill.description)) | set(skill.name.split("_"))
            shared = want & have
            score = len(shared) / len(want)
            if score > best_score and len(shared) >= 3:
                best, best_score = skill, score
        # A high bar on purpose: a loose match once hijacked "type 1+1 into calculator" to the calculator.
        return best if best_score >= 0.75 else None

    def repair(self, skill, request: str, error_text: str):
        """Try to fix an evolved skill that crashed. Returns the reloaded skill or None."""
        self._emit("repair", f"Skill '{skill.name}' crashed; attempting a self-repair...")
        spec = CapabilitySpec(
            kind="skill", name=skill.name, description=skill.description, triggers=skill.trigger_sources,
            test_inputs=[request], plan="Fix the bug shown in the traceback without changing what the skill does.",
        )
        try:
            code = Path(skill.path).read_text(encoding="utf-8")
        except OSError:
            code = ""
        feedback = f"The installed skill crashed at runtime on the request {request!r}:\n{error_text[-2500:]}"
        outcome = self._evolve(spec, request, target=Path(skill.path), feedback=feedback, code=code, repairing=True)
        return outcome.skill if outcome.kind == "skill" else None

    def improve(self, skill, reason: str, request: str | None = None) -> EvolutionOutcome:
        """Rewrite an existing evolved skill to be better - not because it crashed, but to raise its quality.

        This is JARVIS evolving what it already has: given a reason (a weak/wrong output it produced, or an
        explicit ask to make it better), it redrafts the skill, verifies the new version in the sandbox, and
        only hot-swaps if the improvement passes. If nothing better verifies, the current skill is untouched.
        """
        origin = getattr(skill, "origin", "evolved")
        if origin == "builtin":
            return EvolutionOutcome("failed", detail=f"'{skill.name}' is a built-in skill; I won't overwrite it.")
        # A plain-words probe the skill's own trigger should match (the name, not the raw regex trigger).
        probe = request or " ".join(p for p in skill.name.split("_") if p) or skill.name
        self._emit("improve", f"Improving skill '{skill.name}': {_first_line(reason, 100)}")
        spec = CapabilitySpec(
            kind="skill", name=skill.name, description=skill.description, triggers=skill.trigger_sources,
            test_inputs=[probe],
            plan=("Improve this skill while keeping what it does and its run(request, context) interface. "
                  "Fix the problem described, make the output clearer and more robust, and never swallow errors."),
        )
        try:
            code = Path(skill.path).read_text(encoding="utf-8")
        except OSError:
            code = ""
        feedback = (f"You are improving your own installed skill '{skill.name}'. Reason it needs to be better:\n"
                    f"{str(reason)[-2000:]}\nRewrite the whole module. Keep the SKILL metadata and the entry point.")
        outcome = self._evolve(spec, probe, target=Path(skill.path), feedback=feedback, code=code, repairing=True)
        if outcome.kind == "skill":
            self._log("improved", skill=skill.name, reason=str(reason)[:300])
        return outcome

    # ---- the loop ---------------------------------------------------------------------------

    def _evolve(self, spec: CapabilitySpec, request: str, *, target: Path, feedback: str | None = None,
                code: str | None = None, repairing: bool = False) -> EvolutionOutcome:
        target = Path(target).resolve()
        lessons = self._recent_lessons()
        for attempt in range(1, self.max_attempts + 1):
            if self.cancel_check and self.cancel_check():  # user interrupted (Ctrl+Alt+C)
                self._emit("interrupt", "Build interrupted.")
                return EvolutionOutcome("cancelled", spec=spec, attempts=attempt - 1)
            verb = "Repairing" if repairing else "Drafting"
            self._emit("draft", f"{verb} {spec.name} (attempt {attempt}/{self.max_attempts})...")
            # Warmer on each retry: at a fixed low temperature a small model re-emits the same broken draft.
            temperature = min(0.2 + 0.3 * (attempt - 1), 0.9)
            try:
                code = draft_skill(self.llm, spec, request, feedback=feedback, previous_code=code,
                                   temperature=temperature, lessons=lessons)
            except LLMError as exc:
                return self._fail(request, f"Drafting needs an LLM and none answered. {exc}", spec, attempt)

            code, removed = tidy_top_level(code)
            if removed:
                self._emit("tidy", f"Removed {removed} line(s) of example code the model added outside functions.")
            code, wrapped = ensure_entry_point(code)
            if wrapped:
                self._emit("tidy", "Added the run(request, context) entry point the draft was missing.")
            code, _ = enforce_skill_meta(code, spec.name, spec.description, spec.triggers)

            problems = static_check(code, allow_risky=self.allow_risky)
            if problems:
                feedback = "Static checks failed:\n- " + "\n- ".join(problems)
                self._emit("reject", f"Rejected draft: {problems[0]}")
                self._log("rejected", skill=spec.name, attempt=attempt, stage="static", detail=feedback)
                continue

            self._emit("sandbox", "Verifying the draft in an isolated sandbox...")
            try:
                result = self._verify(code, spec, request)
            except Blocked as exc:
                return self._fail(request, f"That needs a package I wasn't allowed to install: {exc}", spec, attempt)
            if not result.ok:
                feedback = f"Sandbox verification failed at the {result.error}"
                self._emit("reject", f"Sandbox failed: {_first_line(result.error)}")
                self._log("rejected", skill=spec.name, attempt=attempt, stage="sandbox", detail=result.error[-2000:])
                continue

            previous = target.read_text(encoding="utf-8") if target.exists() else None
            self._install(target, code, request)
            self.registry.reload()
            skill = self.registry.get(spec.name)
            if skill is None or Path(skill.path).resolve() != target:
                error = self.registry.errors.get(target.name, "the skill did not register after install")
                self._restore(target, previous)
                self.registry.reload()
                feedback = f"The module passed the sandbox but JARVIS could not load it: {error}"
                self._log("rejected", skill=spec.name, attempt=attempt, stage="load", detail=error)
                continue

            self._emit("install", f"Installed '{spec.name}' and hot-reloaded it (sandbox {result.duration_s:.1f}s).")
            self._log("repaired" if repairing else "installed", skill=spec.name, attempt=attempt,
                      request=request, sandbox_outputs=result.outputs)
            return EvolutionOutcome("skill", skill=skill, attempts=attempt, spec=spec)

        return self._fail(request, feedback or "no draft passed verification", spec, self.max_attempts)

    def _free_name(self, name: str) -> str:
        taken = set(self.registry.skills) | {p.stem for p in self.registry.directory.glob("*.py")}
        if name not in taken:
            return name
        suffix = 2
        while f"{name}_{suffix}" in taken:
            suffix += 1
        return f"{name}_{suffix}"

    def _install(self, target: Path, code: str, request: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, self.archive_dir / f"{target.stem}.{time.strftime('%Y%m%d-%H%M%S')}.py")
        origin = " ".join(request.split())[:100]
        header = f"# Evolved by JARVIS on {time.strftime('%Y-%m-%d %H:%M')} for: {origin}\n"
        staging = target.with_name(f"_{target.stem}.installing")  # not *.py, so the loader never sees it half-written
        staging.write_text(header + code, encoding="utf-8")
        os.replace(staging, target)

    @staticmethod
    def _restore(target: Path, previous: str | None) -> None:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(previous, encoding="utf-8")
