---
name: jarvis-model-usage-switching
description: JARVIS tracks per-model usage and lets you switch the active model manually
metadata: 
  node_type: memory
  type: project
  originSessionId: 5235d300-6e27-428c-867c-70c44be16e13
  modified: 2026-09-15T17:10:08.952Z
---

Added 2026-09-15. Two features:

**Usage tracking** — `core/usage.py` `UsageStore` persists per-provider {calls, prompt_tokens, completion_tokens, last_used} in `%APPDATA%/JARVIS/usage.json`. The router captures tokens: `OpenAICompatProvider.chat` reads `response.body["usage"]` (prompt_tokens/completion_tokens); `OllamaProvider._stream` reads the final chunk's `prompt_eval_count`/`eval_count`; each provider stores `self.last_usage`, and `LLMRouter.complete()` calls `self.usage.record(name, prompt, completion)` after a successful answer. `LLMRouter(settings, providers=None, usage=None)` makes a UsageStore by default. View: `jarvis usage` (table, `--reset` to clear), panel `/usage`, and a one-line summary in `jarvis doctor`.

**Manual model switch** — `jarvis model` shows current; `jarvis model <auto|groq|gemini|ollama>` sets `llm.provider` (auto = walk fallback_order, a name = pin) via `set_user_value` and sends IPC `reloadconfig` so the running daemon applies it LIVE (prints "applied live" vs "saved..."). Panel: `/model [name]` and a header ◆ button (`_cycle_model`) that rotates auto→groq→gemini→ollama live. Orchestrator: `set_model(name)`, `reload_settings()`, `_rebuild_llm()`, `model_status()`. New IPC command `reloadconfig` in `ui/panel.py` on_command -> panel event -> `jarvis.reload_settings()`. Verified live 2026-09-15 (usage recorded groq call with tokens; switch to gemini applied live). 137 tests pass. Related: [[jarvis-autonomy-and-self-improvement]], [[jarvis-windows-rebuild]].
