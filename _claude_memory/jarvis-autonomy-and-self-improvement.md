---
name: jarvis-autonomy-and-self-improvement
description: "JARVIS has an autonomy \"unleash\" switch and can rewrite its own evolved skills"
metadata: 
  node_type: memory
  type: project
  originSessionId: 39333a9b-816b-4c6c-a177-4534fa7206f3
  modified: 2026-09-15T06:03:48.731Z
---

Added 2026-09-15 (user: "go all out ... make it truly self evolving go crazy", accepts full laptop risk).

**Autonomy mode** = the single unleash switch. `PermissionRegistry` gained `autonomy()` / `set_autonomy(bool)`, persisted under an `"autonomy"` key in `%APPDATA%/JARVIS/permissions.json`. While on, `state(cap)` returns "allow" for every capability (overriding even explicit denies), so no broker ever prompts; turning it off restores the underlying per-cap grants. Orchestrator `_autonomous()` skips the multi-step plan-review confirm. Engine `allow_risky` is now a property: true if `evolution.allow_risky_calls` OR autonomy is on (so sandbox `static_check(allow_risky=...)` lets evolved skills delete files / shell / eval while unleashed). Engine now takes `permissions=` (orchestrator passes `self.permissions`). Controls: `jarvis autonomy on|off|status`, panel 🔥/🔒 header button (`_toggle_autonomy`/`_refresh_autonomy_btn`), `/autonomy on|off`; doctor + `jarvis permissions` show the banner; `jarvis permissions --reset` also turns autonomy off. NOTE: I left autonomy ON on the user's real machine per their explicit request; off-switch is `jarvis autonomy off`.

**Self-improvement** = rewriting existing evolved skills, not just adding. `EvolutionEngine.improve(skill, reason, request=None)` refuses builtins, derives a plain-words probe from the skill NAME (not the raw regex trigger - a regex like `\bgadget\b` won't match itself), then runs the normal `_evolve(..., repairing=True)` loop (draft->sandbox verify->hot-swap; unchanged on failure). Orchestrator `_improve_target(request)` (cheap, no LLM) detects "improve/upgrade/fix/enhance/... **skill**" naming an evolved skill (or the sole evolved skill); `_fast` returns None on a match so it runs via the backgroundable build path; `_evolve_new` calls `_maybe_improve` first -> route "improved". CLI `jarvis improve <skill> [reason]`. Constant `_IMPROVE_VERB` in orchestrator.py.

Live-verified 2026-09-15 (autonomy on, groq): "get the price of bitcoin in usd, then tell me what 0.5 bitcoin is worth" -> built `crypto_price` skill mid-agent-loop, fetched coingecko, fed real number to calculator, answered, zero prompts. Then "improve your crypto_price skill so it also shows the 24 hour percent change" -> rewrote + verified + hot-swapped; now returns 24h change (side effect: it made currency mandatory instead of defaulting usd - LLM's choice, minor). 118 tests pass. Related: [[jarvis-agentic-loop]], [[jarvis-windows-rebuild]].
