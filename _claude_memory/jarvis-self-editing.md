---
name: jarvis-self-editing
description: "JARVIS can rewrite its own core source code on command, with a test-and-rollback safety harness"
metadata: 
  node_type: memory
  type: project
  originSessionId: 39333a9b-816b-4c6c-a177-4534fa7206f3
  modified: 2026-09-15T10:48:31.942Z
---

Added 2026-09-15 (user: "give itself the ability to rewrite its own code for purposes based on my command"). This deliberately REVERSES the old "meta requests declined" rule for self-editing (skills/evolution unchanged; "give yourself the ability to X" still becomes a skill).

`evolution_engine/self_edit.py` `SelfEditor.edit(request)`: (1) `_pick_file` - explicit filename found on disk across EDITABLE_DIRS (core, evolution_engine, ui, window_manager, memory, skills), else keyword ("your orchestrator"), else the LLM picks from the list; (2) draft the COMPLETE new file (DRAFT_SYSTEM, no fences, keep everything else); (3) `ast.parse` reject on syntax error; (4) back up original to `_self_edits/<name>.<ts>.py`; (5) write; (6) if `run_tests` (default on), `python -m unittest discover -s tests -q` via subprocess in root - on failure ROLL BACK to original. Returns `SelfEditResult(ok, message, file)`. PROTECTED (never edited): core/keystore.py, core/permissions.py, evolution_engine/self_edit.py, evolution_engine/sandbox.py. Core edits need a restart (process holds old code in memory); skills hot-reload.

Wiring: capability `self_edit` in `core/permissions.py`. Orchestrator: module regex `_SELF_EDIT` (matches "rewrite/edit/change/modify... your (own) code/source/orchestrator/<x>.py/<x> module", "rewrite yourself", "edit your own"); `_fast` returns None on a match (bypasses the is_meta_request decline) so it runs via the backgroundable build path; `_evolve_new` -> `_maybe_self_edit` (before `_maybe_improve`) -> `_gate_self_edit` (permission allow, or confirm; autonomy => allow) -> `self._self_editor.edit()`; routes "self-edited" / "self-edit-failed" / "denied". `self._self_editor = SelfEditor(...)` built in `__init__` with `self_edit.run_tests` setting. CLI `jarvis edit-self "<instruction>"` (`cmd_edit_self`). Config `self_edit.run_tests` (DEFAULTS, default True).

Git auto-commit (added 2026-09-15): after a successful edit, `SelfEditor._git_commit(rel, request)` runs `git add -- rel` then `git -c user.name=JARVIS -c user.email=jarvis@localhost commit -m "JARVIS self-edit: <req>" -- rel` (inline identity, never touches global config); `git init`s the repo first if there isn't one; skipped silently if git not on PATH. Success message includes the short hash + "revert with: git revert <hash>". Constructor param `git_commit` (default True), setting `self_edit.git_commit` in DEFAULTS. The project is NOW a git repo: initialized 2026-09-15 with baseline commit f4b1bea (75 files), added `_self_edits/` to .gitignore, removed a stray empty calc.py. Test `test_successful_edit_is_committed_to_git` (skipUnless git). NOTE: a real self-edit on the live repo makes a real commit - use `git revert`/`git reset` to undo; backups also remain in `_self_edits/` (gitignored).

Live-verified 2026-09-15 (autonomy on, groq): created throwaway core/demo_selfedit.py, ran `jarvis edit-self "add a function called doubled..."` -> it rewrote the file (kept greeting(), added doubled()), ran the suite (passed), backed up original; doubled(21)=42. Re-verified with git on: `tripled` edit committed as a real commit (message "JARVIS self-edit: ..."), then reset away. Then cleaned up. 134 tests pass (SelfEditTests + SelfEditRoutingTests; updated old test_only_self_code_edits_are_declined -> now "gated not declined", route "denied" without permission). Related: [[jarvis-autonomy-and-self-improvement]], [[jarvis-code-apps-popups]], [[jarvis-windows-rebuild]].
