"""Self-modification: JARVIS rewriting its OWN source code, on command, with a safety harness.

This is different from evolving a skill (which adds a file in /skills). Here JARVIS edits its own core
modules - the orchestrator, the router, the panel, etc. Because that can break the running program, every
edit goes through guardrails:

  1. pick the one file the request is about (named explicitly, or chosen by the model),
  2. draft the complete new file for the stated purpose,
  3. reject it unless it parses as valid Python (ast.parse),
  4. back up the original, then write the new version,
  5. run the full test suite - and if it fails (or won't import), roll the file straight back.

So a self-edit only sticks if it compiles and the tests still pass. Core changes take effect on the next
restart (the running process keeps the old code in memory); skill edits hot-reload as usual.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from core.llm_router import LLMError

EDITABLE_DIRS = ("core", "evolution_engine", "ui", "window_manager", "memory", "skills")
# Never let a self-edit touch these - losing them would make JARVIS unrecoverable from inside itself.
PROTECTED = {"core/keystore.py", "core/permissions.py", "evolution_engine/self_edit.py", "evolution_engine/sandbox.py"}

DRAFT_SYSTEM = (
    "You are JARVIS editing one file of your own Python source. You are given the file's full current "
    "contents and what to change. Return the COMPLETE new file contents - not a diff, not a snippet, no "
    "markdown fences, no commentary. Make the requested change and keep everything else that already works "
    "exactly as it is: same imports, same public functions/classes, same behaviour elsewhere. Write correct, "
    "idiomatic Python that imports cleanly."
)
_FENCE = re.compile(r"^\s*```[\w+-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


@dataclass
class SelfEditResult:
    ok: bool
    message: str
    file: str | None = None


class SelfEditor:
    def __init__(self, llm, root: Path, emit=None, archive_dir: Path | None = None, run_tests: bool = True,
                 test_timeout_s: float = 120, git_commit: bool = True):
        self.llm = llm
        self.root = Path(root)
        self.emit = emit
        self.archive_dir = Path(archive_dir) if archive_dir else (self.root / "_self_edits")
        self.run_tests = run_tests
        self.test_timeout_s = test_timeout_s
        self.git_commit = git_commit

    def _emit(self, stage, message):
        if self.emit:
            self.emit(stage, message)

    def targets(self) -> list[str]:
        """Editable source files, as repo-relative posix paths, minus protected ones."""
        out = []
        for folder in EDITABLE_DIRS:
            base = self.root / folder
            if not base.is_dir():
                continue
            for path in sorted(base.glob("*.py")):
                rel = path.relative_to(self.root).as_posix()
                if rel not in PROTECTED and not path.name.startswith("_"):
                    out.append(rel)
        return out

    def _pick_file(self, request: str) -> str | None:
        files = self.targets()
        low = request.lower()
        named = re.search(r"([a-z_][a-z0-9_]*\.py)", low)  # explicit filename in the request
        if named:
            for folder in EDITABLE_DIRS:  # look on disk, protected included, so edit() can refuse clearly
                candidate = self.root / folder / named.group(1)
                if candidate.is_file():
                    return candidate.relative_to(self.root).as_posix()
        for rel in files:  # "your orchestrator", "the router", the bare module name
            stem = Path(rel).stem
            if re.search(rf"\byour\s+{re.escape(stem)}\b", low) or f" {stem} " in f" {low} ":
                return rel
        listing = "\n".join(files)
        prompt = (f"These are your editable source files:\n{listing}\n\nTask: {request}\n\n"
                  "Which ONE file should be edited to do this? Reply with just its path from the list.")
        try:
            answer = self.llm.complete(prompt, system="Reply with exactly one file path from the list, nothing else.",
                                       temperature=0.0, max_tokens=40)
        except LLMError:
            return None
        answer = str(answer).strip().strip("`'\"")
        for rel in files:
            if rel in answer or Path(rel).name in answer:
                return rel
        return None

    def _draft(self, rel: str, source: str, request: str) -> str:
        prompt = (f"File: {rel}\n\n--- current contents ---\n{source}\n--- end ---\n\n"
                  f"Change to make: {request}\n\nReturn the complete new contents of {rel}.")
        text = self.llm.complete(prompt, system=DRAFT_SYSTEM, temperature=0.15,
                                 max_tokens=8000).strip()
        fence = _FENCE.match(text)
        return fence.group(1) if fence else text

    def _git(self, *args, timeout=30):
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, timeout=timeout)

    def _git_commit(self, rel: str, request: str) -> str | None:
        """Commit the edited file so every self-edit is a revertible git commit. Returns the short hash, or None."""
        import shutil

        if shutil.which("git") is None:
            return None
        try:
            inside = self._git("rev-parse", "--is-inside-work-tree")
            if inside.returncode != 0:  # not a repo yet -> start one so edits are revertible
                if self._git("init").returncode != 0:
                    return None
            self._git("add", "--", rel)
            # Inline identity so we never depend on (or mutate) the user's global git config.
            message = f"JARVIS self-edit: {request.strip()[:100]}"
            commit = self._git("-c", "user.name=JARVIS", "-c", "user.email=jarvis@localhost",
                               "commit", "-m", message, "--", rel)
            if commit.returncode != 0:
                return None
            shown = self._git("rev-parse", "--short", "HEAD")
            return shown.stdout.strip() or "committed"
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _run_tests(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                                  cwd=str(self.root), capture_output=True, text=True, timeout=self.test_timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"could not run the tests ({exc})"
        if proc.returncode == 0:
            return True, "tests pass"
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, "\n".join(tail[-6:]) or "tests failed"

    def edit(self, request: str) -> SelfEditResult:
        rel = self._pick_file(request)
        if not rel:
            return SelfEditResult(False, "I couldn't work out which of my files to change. Name the file, "
                                         "e.g. 'rewrite your core/understand.py to ...'.")
        if rel in PROTECTED:
            return SelfEditResult(False, f"{rel} is protected - I won't rewrite that one.", rel)
        path = self.root / rel
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            return SelfEditResult(False, f"I couldn't read {rel}: {exc}", rel)

        self._emit("self_edit", f"Rewriting my own {rel}...")
        try:
            new_source = self._draft(rel, original, request)
        except LLMError as exc:
            return SelfEditResult(False, f"Drafting the change needed a model and none answered: {exc}", rel)
        if not new_source.strip():
            return SelfEditResult(False, "The model returned nothing; I left the file unchanged.", rel)
        try:
            ast.parse(new_source)  # must be valid Python before it ever hits disk
        except SyntaxError as exc:
            return SelfEditResult(False, f"My draft had a syntax error ({exc.msg} line {exc.lineno}); "
                                         "I left the file unchanged.", rel)
        if new_source.strip() == original.strip():
            return SelfEditResult(False, "The rewrite came back identical - no change made.", rel)

        backup = self._backup(rel, original)
        path.write_text(new_source, encoding="utf-8")

        if self.run_tests:
            self._emit("self_edit", "Running the test suite to check the change...")
            ok, detail = self._run_tests()
            if not ok:
                path.write_text(original, encoding="utf-8")  # roll back - the change broke something
                self._emit("self_edit", "Tests failed - rolled the change back.")
                return SelfEditResult(False, f"I rewrote {rel} but it failed the tests, so I rolled it back:\n{detail}",
                                      rel)

        commit = self._git_commit(rel, request) if self.git_commit else None
        self._emit("self_edit", f"Updated {rel} (backup at {backup.name})"
                                + (f", committed {commit}." if commit else "."))
        note = " Restart JARVIS for it to take effect." if not rel.startswith("skills/") else ""
        where = f"committed as {commit}; revert with: git revert {commit}" if commit else f"backup: {backup}"
        return SelfEditResult(True, f"Done - I rewrote my own {rel} and the tests still pass.{note} ({where}).", rel)

    def _backup(self, rel: str, source: str) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.archive_dir / f"{Path(rel).name}.{stamp}.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# Backup of {rel} before a self-edit on {stamp}\n{source}", encoding="utf-8")
        return dest
