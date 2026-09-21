---
name: jarvis-conversation-context
description: "JARVIS conversation focus + reference resolver so 'open it'/'the photo' keep context across turns"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5235d300-6e27-428c-867c-70c44be16e13
  modified: 2026-09-17T08:49:03.355Z
---

Added 2026-09-17 (user: "fix contexting... I take a photo, tell it open it, it says nothing known as it; multistep commands fail; I have to type very long messages to keep context"). Commit `25f5ee3`.

Root cause: nothing remembered "the thing we're talking about", so back-references misrouted — "open the photo" tried to launch an *app* called "photo", "open it" only worked by luck (open_app declined the pronoun so open_last caught it), "show it"/"what's in it" matched no skill, "email it to me" hit gmail_search.

Fix — two new modules:
- `core/focus.py` `Focus(state_dir)`: persistent focus in `%APPDATA%/JARVIS/focus.json`, slots file/image/url/app/folder with timestamps. `remember/get/most_recent/recent/snapshot`; an `image` also mirrors into `file`; path slots existence-checked. The `ActionBroker` writes focus.json directly (method `remember_focus`, stdlib-only so the sandbox can still load actions.py by path) on write_file/capture_camera/screenshot/open_path/open_url/open_app.
- `core/refs.py` `resolve_references(text, focus) -> (text, changed)`: deterministic rewrite of a back-reference to the concrete path/URL BEFORE routing. Conservative: only when a referent exists; never rewrites creation ("take a photo", "make a file"); skips dummy pronouns ("is it done?"); "the docs" left alone (means Google Docs). Bare pronoun ("it"/"that") resolved only in an imperative command or a "what's in it" question.

Site search (commit `28c2665`): `skills/web_search.py` opens the right results page by URL — "search scholar for X", "X on youtube", "look up X on wikipedia" (Google/Scholar/YouTube/Wikipedia/GitHub/Amazon/Reddit/Stack Overflow/Maps/Drive/X, default Google). Focus-aware: a bare "search X" right after opening a site searches THAT site (reads `context["focus"]["url"]` host); explicit "the web"/"online" stays on Google. JARVIS still can't TYPE into an already-open page (no DOM control) — this URL approach is the workaround. understand/agent prompts nudged to keep the "search" verb in multistep steps.

Wiring in `core/orchestrator.py`: `self._focus`; `_resolve_refs()` runs at the top of `_fast` (keeps original for history/memory); `_run_step` and `_chain` resolve per-step at RUN time (so "take a photo and open it" opens the NEW photo); `context["focus"]` snapshot handed to skills; `understand()` gets a "Current context" block (`_focus_hint()`) for references the resolver can't catch ("make it landscape"). 165 tests pass (+12 in `tests/test_focus_and_refs.py`). Related: [[jarvis-camera-vision]], [[jarvis-code-apps-popups]], [[jarvis-agentic-loop]].
